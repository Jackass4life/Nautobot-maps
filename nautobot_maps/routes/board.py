"""The alert board's API (#424): the board, its activity and history, cases,
acknowledgements and criticality overrides."""

import json
import logging
from datetime import UTC

import requests
from flask import Blueprint, Response, jsonify, request, stream_with_context

from nautobot_maps import (
    alerts,
    auth,
    db,
    export,
    inventory,
    scheduler,
    scope,
    timeutil,
)

logger = logging.getLogger(__name__)

bp = Blueprint("board", __name__)


@bp.route("/api/alerts")
def api_alerts():
    """Return alert-board summaries for all Nautobot locations.

    Read from the persisted inventory snapshot; never calls Nautobot or
    LibreNMS inline.  Query parameters: ``refresh=1`` (the board's ↻) queues an
    incremental sync now, except of a source synced less than a minute ago (#382);
    ``include_non_operational=1`` includes the excluded locations.
    A normal request also queues a sync when one is due.

    Response: ``alerts`` (one entry per site: ``alert_level``,
    ``alert_reason``, ``down_devices``, ``current_downtime_seconds``,
    ``historical_downtime_seconds``, ``active_cases``, ``latest_down_at``, ...),
    ``summary`` (a count per level plus ``total``, and ``non_ok`` = critical +
    medium + low), ``sync_pending`` (a sync is running; poll again),
    ``next_update_in_seconds`` (until the next sync is due; 0 = now, null =
    unknown or running) and ``persistence_configured`` (false: no database,
    so the board is always empty).
    """
    force_refresh = request.args.get("refresh", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "refresh",
    }
    include_non_operational = request.args.get(
        "include_non_operational",
        "",
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "include",
    }
    try:
        return jsonify(
            alerts.get_alert_board_data(
                force_refresh=force_refresh,
                include_non_operational=include_non_operational,
            )
        )
    except RuntimeError:
        return (
            jsonify({"error": "Alert board unavailable because Nautobot is not configured"}),
            503,
        )
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error while building alert board: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.exception("Unexpected error building alert board: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@bp.route("/api/criticality-overrides", methods=["GET"])
@auth.require_role("operator")
def api_list_criticality_overrides():
    """Return all per-device criticality overrides stored in the DB.

    Returns 503 when the persistence DB is not configured.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        rows = conn.execute(
            "SELECT nautobot_device_id, is_critical, reason, updated_by, updated_at "
            "FROM device_criticality_override ORDER BY updated_at DESC"
        ).fetchall()
        return jsonify({"overrides": [db.row_to_dict(r) for r in rows]})
    except Exception as exc:
        logger.exception("Could not list criticality overrides: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


@bp.route("/api/criticality-overrides", methods=["POST"])
@auth.require_role("operator")
def api_set_criticality_override():
    """Create or update a per-device criticality override.

    Expected JSON body::

        {
            "nautobot_device_id": "<uuid>",
            "is_critical": true | false,
            "reason": "optional explanation",
            "updated_by": "operator-name"
        }

    Returns 503 when the DB is not configured.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    body = request.get_json(silent=True) or {}
    device_id = (body.get("nautobot_device_id") or "").strip()
    if not device_id:
        conn.close()
        return jsonify({"error": "nautobot_device_id is required"}), 400
    is_critical = bool(body.get("is_critical", True))
    reason = (body.get("reason") or "").strip()
    updated_by = (body.get("updated_by") or "").strip()
    if not updated_by:
        updated_by = auth.get_current_user().get("username", "")
    try:
        with db.transaction(conn):
            p0, p1, p2, p3 = db.placeholders(4).split(",")
            now_sql = db.sql_now()
            conn.execute(
                f"""
                INSERT INTO device_criticality_override
                    (nautobot_device_id, is_critical, reason, updated_by, updated_at)
                VALUES ({p0}, {p1}, {p2}, {p3}, {now_sql})
                ON CONFLICT(nautobot_device_id) DO UPDATE SET
                    is_critical = excluded.is_critical,
                    reason      = excluded.reason,
                    updated_by  = excluded.updated_by,
                    updated_at  = excluded.updated_at
                """,
                (device_id, int(is_critical), reason, updated_by),
            )
        return jsonify({"status": "ok", "nautobot_device_id": device_id, "is_critical": is_critical})
    except Exception as exc:
        logger.exception("Could not set criticality override: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


@bp.route("/api/criticality-overrides/<device_id>", methods=["DELETE"])
@auth.require_role("operator")
def api_delete_criticality_override(device_id: str):
    """Delete a per-device criticality override.

    Returns 404 if no override exists for the given device ID.
    Returns 503 when the DB is not configured.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        with db.transaction(conn):
            marker = db.placeholders(1)
            cur = conn.execute(
                f"DELETE FROM device_criticality_override WHERE nautobot_device_id = {marker}",
                (device_id,),
            )
        if cur.rowcount == 0:
            return jsonify({"error": "Override not found"}), 404
        return jsonify({"status": "deleted", "nautobot_device_id": device_id})
    except Exception as exc:
        logger.exception("Could not delete criticality override: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


# Most entries /api/alert-feed returns at once.
MAX_FEED_ENTRIES = 500


@bp.route("/api/alert-feed")
def api_alert_feed():
    """What changed on the alert board, newest first (#180).

    Devices going down and back up, and site severity changes.  Query
    parameters: ``limit`` (default 100, at most 500), ``since`` (ISO-8601;
    only newer entries) and ``kinds`` (comma-separated ``down``, ``up``,
    ``severity``; default all).  ``tenant=`` / ``q=`` (as the board's link,
    #369): only those sites' entries, and ``matching_sites``, how many sites
    match (#375).
    """
    try:
        limit = int(request.args.get("limit") or 100)
    except ValueError:
        return jsonify({"error": "limit must be a whole number"}), 400
    limit = max(1, min(limit, MAX_FEED_ENTRIES))
    since = (request.args.get("since") or "").strip() or None
    if since:
        parsed_since = timeutil.as_utc_datetime(since)
        if parsed_since is None:
            return jsonify({"error": "since must be an ISO-8601 timestamp"}), 400
        since = parsed_since.astimezone(UTC).isoformat()
    kinds = [kind.strip() for kind in (request.args.get("kinds") or "").split(",") if kind.strip()]
    unknown = sorted(set(kinds) - set(alerts.FEED_KINDS))
    if unknown:
        return jsonify({"error": f"Unknown kinds: {', '.join(unknown)}"}), 400
    page_scope = scope.read_scope(request.args)
    conn = db.get_conn()
    if conn is None:
        return jsonify({"events": [], "persistence_configured": False})
    try:
        site_ids = None
        if page_scope is not None:
            # A board for some sites only (#369): matched on the stored locations
            # (every one, excluded ones too, as they are now: tenant, path, name),
            # a database read, never a board build.
            locations = inventory.read_locations(include_without_coordinates=True, conn=conn)
            if not locations:
                # Not synced yet, or the read failed: an empty feed would look like
                # an all-clear for these sites, so say so instead.
                return jsonify({"error": "The site list is not stored yet; try again after the next sync"}), 503
            site_ids = [str(loc["id"]) for loc in locations if loc.get("id") and scope.in_scope(loc, page_scope)]
        events = alerts.read_alert_feed(conn, limit, since, kinds or alerts.FEED_KINDS, site_ids)
        result = {"events": events, "persistence_configured": True}
        if site_ids is not None:
            # 0 tells "no site matches" from "nothing changed" (#375).
            result["matching_sites"] = len(site_ids)
        return jsonify(result)
    except Exception as exc:
        logger.exception("Could not read the alert feed: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


@bp.route("/api/alert-history", methods=["GET"])
@auth.require_role("operator")
def api_alert_history():
    """Return historical alert instances with events and case numbers.

    Newest down first (by when the device went down), at most 500.  Query parameters filter them: ``site_id``,
    ``device_id``, and ``start_at`` / ``end_at``: only incidents created at
    or after / at or before this ISO-8601 time.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    site_id = (request.args.get("site_id") or "").strip()
    device_id = (request.args.get("device_id") or "").strip()
    start_at = (request.args.get("start_at") or "").strip()
    end_at = (request.args.get("end_at") or "").strip()
    try:
        if start_at:
            parsed_start_at = timeutil.as_utc_datetime(start_at)
            if parsed_start_at is None:
                return jsonify({"error": "start_at must be an ISO-8601 timestamp"}), 400
            start_at = parsed_start_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if end_at:
            parsed_end_at = timeutil.as_utc_datetime(end_at)
            if parsed_end_at is None:
                return jsonify({"error": "end_at must be an ISO-8601 timestamp"}), 400
            end_at = parsed_end_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        conditions = []
        params = []
        if site_id:
            conditions.append(f"site_id = {db.placeholders(1)}")
            params.append(site_id)
        if device_id:
            conditions.append(f"device_id = {db.placeholders(1)}")
            params.append(device_id)
        if start_at:
            conditions.append(f"created_at >= {db.placeholders(1)}")
            params.append(start_at)
        if end_at:
            conditions.append(f"created_at <= {db.placeholders(1)}")
            params.append(end_at)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"""
            SELECT id, alert_key, site_id, site_name, device_id, device_name, alert_level,
                   alert_reason, status, down_started_at, last_seen_down_at, resolved_at,
                   total_downtime_seconds, created_at, updated_at, probable_cause
            FROM alert_instances
            {where_clause}
            ORDER BY down_started_at DESC, id DESC
            LIMIT 500
            """,
            tuple(params),
        ).fetchall()
        instances = [db.row_to_dict(row) for row in rows]
        instance_ids = [row["id"] for row in instances if row.get("id") is not None]
        events_by_instance: dict[str, list[dict]] = {str(instance_id): [] for instance_id in instance_ids}
        cases_by_instance: dict[str, list[dict]] = {str(instance_id): [] for instance_id in instance_ids}
        if instance_ids:
            markers = db.placeholders(len(instance_ids))
            ev_rows = conn.execute(
                f"""
                SELECT alert_instance_id, event_type, event_at, alert_level, alert_reason, snapshot_json
                FROM alert_events
                WHERE alert_instance_id IN ({markers})
                ORDER BY alert_instance_id ASC, id ASC
                """,
                tuple(instance_ids),
            ).fetchall()
            case_rows = conn.execute(
                f"""
                SELECT alert_instance_id, case_number, created_by, created_at
                FROM alert_cases
                WHERE alert_instance_id IN ({markers})
                ORDER BY alert_instance_id ASC, id DESC
                """,
                tuple(instance_ids),
            ).fetchall()
            for ev_row in ev_rows:
                event = db.row_to_dict(ev_row)
                instance_id = str(event.pop("alert_instance_id"))
                try:
                    event["snapshot"] = json.loads(event.pop("snapshot_json", "{}") or "{}")
                except Exception:
                    event["snapshot"] = {}
                events_by_instance.setdefault(instance_id, []).append(event)
            for case_row in case_rows:
                case_data = db.row_to_dict(case_row)
                instance_id = str(case_data.pop("alert_instance_id"))
                cases_by_instance.setdefault(instance_id, []).append(case_data)
        for instance in instances:
            instance_id = str(instance.get("id"))
            instance["events"] = events_by_instance.get(instance_id, [])
            instance["cases"] = cases_by_instance.get(instance_id, [])
        return jsonify({"instances": instances})
    except Exception as exc:
        logger.exception("Could not fetch alert history: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


@bp.route("/api/alert-history.csv", methods=["GET"])
@auth.require_role("operator")
def api_alert_history_csv():
    """A site's alert history as a CSV download (#277).

    Query parameters: ``site_id`` (required), ``days`` (``7``, ``30``
    (default), ``90`` or ``all``) and ``view``: ``incidents`` (default, one row
    per time a device went down) or ``devices`` (one row per device: times
    down, total and longest downtime, last down, down now).  Includes every
    incident that was down at any point in the period.  Times are UTC.
    """
    site_id = (request.args.get("site_id") or "").strip()
    days = (request.args.get("days") or "30").strip().lower()
    view = (request.args.get("view") or "incidents").strip().lower()
    if not site_id:
        return jsonify({"error": "site_id is required"}), 400
    if view not in export.VIEWS:
        return jsonify({"error": f"view must be one of: {', '.join(export.VIEWS)}"}), 400
    now = timeutil.parse_iso_datetime(timeutil.iso_utc_now())
    try:
        since = export.period_start(days, now)
    except ValueError:
        return jsonify({"error": f"days must be one of: {', '.join(export.PERIOD_DAYS)}"}), 400
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    headers = {"Cache-Control": "no-store"}
    try:
        name = export.file_name(export.site_name(conn, site_id), view, days, now)
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
        if view == "devices":
            body = export.devices_csv(export.read_device_summary(conn, site_id, since, now))
            conn.close()
            return Response(body, mimetype="text/csv", headers=headers)
    except Exception as exc:
        conn.close()
        logger.exception("Could not export alert history: %s", exc)
        return jsonify({"error": "Internal server error"}), 500

    # Incidents are streamed: a site's whole history need not fit in memory.
    def lines():
        try:
            yield from export.incident_lines(export.iter_site_incidents(conn, site_id, since), now)
        except Exception as exc:
            # The status line has been sent; the download ends short.
            logger.exception("Alert history export failed while streaming: %s", exc)
        finally:
            conn.close()

    return Response(stream_with_context(lines()), mimetype="text/csv", headers=headers)


# Upper bound on devices linked to one case in a single request.
MAX_CASE_DEVICES = 200


def _acknowledge(clear: bool):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Expected a JSON object"}), 400
    site_id = body.get("site_id").strip() if isinstance(body.get("site_id"), str) else ""
    # Left out (or null): every open alert of the site.  Anything else must
    # be a non-empty list of IDs, so bad input never means "everything".
    raw_ids = body.get("device_ids")
    device_ids: list[str] = []
    if raw_ids is not None:
        if (
            not isinstance(raw_ids, list)
            or not raw_ids
            or not all(isinstance(value, str) and value.strip() for value in raw_ids)
        ):
            return jsonify(
                {"error": "device_ids must be a non-empty list of device IDs (leave it out for every device)"}
            ), 400
        device_ids = [value.strip() for value in raw_ids]
    note = body.get("note")
    if note is None:
        note = ""
    if not isinstance(note, str):
        return jsonify({"error": "note must be text"}), 400
    if not site_id:
        return jsonify({"error": "site_id is required"}), 400
    if len(device_ids) > MAX_CASE_DEVICES:
        return jsonify({"error": f"At most {MAX_CASE_DEVICES} devices per request"}), 400
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    by = (auth.get_current_user().get("username") or "").strip()
    try:
        result = alerts.acknowledge(conn, site_id, device_ids, by, note, clear=clear)
    finally:
        conn.close()
    if result["missing"]:
        return (
            jsonify(
                {
                    "error": "No active alert for some devices; nothing was changed",
                    "missing_device_ids": result["missing"],
                }
            ),
            404,
        )
    scheduler.board_changed()
    return jsonify({"site_id": site_id, "changed": result["changed"]})


@bp.route("/api/alert-acks", methods=["POST"])
@auth.require_role("operator", open_when_disabled=True)
def api_acknowledge():
    """Acknowledge a site's alarm (#288): someone is on it, without a case number.

    JSON body: ``site_id``, optional ``device_ids`` (default: every device
    with an open alert at the site) and ``note``.  Shown on the board and the
    wall until the alarm ends; recorded in the history.
    """
    return _acknowledge(clear=False)


@bp.route("/api/alert-acks/remove", methods=["POST"])
@auth.require_role("operator", open_when_disabled=True)
def api_unacknowledge():
    """Remove an acknowledgement (#288): ``site_id``, optional ``device_ids``."""
    return _acknowledge(clear=True)


@bp.route("/api/alert-cases", methods=["POST"])
@auth.require_role("operator", open_when_disabled=True)
def api_add_alert_case():
    """Attach a case number to the open alert of one or more devices at a site.

    JSON body: ``site_id``, ``case_number`` and either ``device_ids`` (list)
    or the single ``device_id``.  All-or-nothing: if any device has no open
    alert, nothing is written and the response is 404 with
    ``missing_device_ids``, so a case is never applied to only part of the
    selection.
    """
    body = request.get_json(silent=True) or {}
    site_id = (body.get("site_id") or "").strip() if isinstance(body.get("site_id"), str) else ""
    case_number = (body.get("case_number") or "").strip() if isinstance(body.get("case_number"), str) else ""
    raw_ids = body.get("device_ids")
    if raw_ids is None:
        raw_ids = [body.get("device_id")] if body.get("device_id") else []
    if not isinstance(raw_ids, list) or not all(isinstance(value, str) for value in raw_ids):
        return jsonify({"error": "device_ids must be a list of device ID strings"}), 400
    # De-duplicate while keeping the operator's order.
    device_ids = list(dict.fromkeys(value.strip() for value in raw_ids if value.strip()))
    if not site_id or not device_ids or not case_number:
        return jsonify({"error": "site_id, device_ids and case_number are required"}), 400
    if len(device_ids) > MAX_CASE_DEVICES:
        return jsonify({"error": f"At most {MAX_CASE_DEVICES} devices per request"}), 400

    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    created_by = (auth.get_current_user().get("username") or "").strip()
    try:
        with db.transaction(conn):
            instance_ids: dict[str, int] = {}
            for device_id in device_ids:
                p0, p1 = db.placeholders(2).split(",")
                row = conn.execute(
                    f"""
                    SELECT id
                    FROM alert_instances
                    WHERE site_id = {p0} AND device_id = {p1} AND status = 'open'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (site_id, device_id),
                ).fetchone()
                if row is not None:
                    instance_ids[device_id] = db.row_to_dict(row)["id"]
            missing = [device_id for device_id in device_ids if device_id not in instance_ids]
            if missing:
                return (
                    jsonify(
                        {
                            "error": "No active alert found for some devices; nothing was changed",
                            "missing_device_ids": missing,
                        }
                    ),
                    404,
                )
            for device_id in device_ids:
                p0, p1, p2 = db.placeholders(3).split(",")
                conn.execute(
                    f"""
                    INSERT INTO alert_cases (alert_instance_id, case_number, created_by, created_at)
                    VALUES ({p0}, {p1}, {p2}, {db.sql_now()})
                    ON CONFLICT(alert_instance_id, case_number) DO NOTHING
                    """,
                    (instance_ids[device_id], case_number, created_by),
                )
        # The board payload embeds case numbers; drop it so the new case shows.
        scheduler.board_changed()
        result = {
            "status": "ok",
            "site_id": site_id,
            "case_number": case_number,
            "linked": [
                {"device_id": device_id, "alert_instance_id": instance_ids[device_id]} for device_id in device_ids
            ],
        }
        if len(device_ids) == 1:
            # Fields of the original single-device response, kept for API clients.
            result["device_id"] = device_ids[0]
            result["alert_instance_id"] = instance_ids[device_ids[0]]
        return jsonify(result)
    except Exception as exc:
        logger.exception("Could not add alert case: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()
