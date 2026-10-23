"""The alert board's API (#424; on FastAPI since #436): the board, its
activity and history, cases, acknowledgements and criticality overrides."""

import json
import logging
from datetime import UTC
from typing import Annotated

import requests
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse

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
from nautobot_maps.api import deps, models
from nautobot_maps.repositories import board as board_store

logger = logging.getLogger(__name__)

router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES)

Body = Annotated[object, Depends(deps.json_body)]


@router.get("/api/alerts", name="board.api_alerts")
def api_alerts(query: Annotated[models.BoardQuery, Query()]):
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
    force_refresh = (query.refresh or "").lower() in {
        "1",
        "true",
        "yes",
        "refresh",
    }
    include_non_operational = (query.include_non_operational or "").lower() in {
        "1",
        "true",
        "yes",
        "include",
    }
    try:
        return deps.Json(
            alerts.get_alert_board_data(
                force_refresh=force_refresh,
                include_non_operational=include_non_operational,
            )
        )
    except RuntimeError:
        return deps.Json({"error": "Alert board unavailable because Nautobot is not configured"}, status_code=503)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error while building alert board: %s", exc)
        return deps.Json({"error": "Failed to communicate with Nautobot API"}, status_code=502)
    except Exception as exc:
        logger.exception("Unexpected error building alert board: %s", exc)
        return deps.Json({"error": "Internal server error"}, status_code=500)


@router.get(
    "/api/criticality-overrides",
    name="board.api_list_criticality_overrides",
    dependencies=[deps.require_role("operator")],
)
def api_list_criticality_overrides():
    """Return all per-device criticality overrides stored in the DB.

    Returns 503 when the persistence DB is not configured.
    """
    conn = db.get_conn()
    if conn is None:
        return deps.Json({"error": "Persistence DB not configured"}, status_code=503)
    try:
        rows = conn.execute(
            "SELECT nautobot_device_id, is_critical, reason, updated_by, updated_at "
            "FROM device_criticality_override ORDER BY updated_at DESC"
        ).fetchall()
        return deps.Json({"overrides": [db.row_to_dict(r) for r in rows]})
    except Exception as exc:
        logger.exception("Could not list criticality overrides: %s", exc)
        return deps.Json({"error": "Internal server error"}, status_code=500)
    finally:
        conn.close()


@router.post(
    "/api/criticality-overrides",
    name="board.api_set_criticality_override",
    openapi_extra=deps.body_schema(models.CriticalityOverride),
    dependencies=[deps.require_role("operator")],
)
def api_set_criticality_override(body: Body):
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
        return deps.Json({"error": "Persistence DB not configured"}, status_code=503)
    body = body or {}
    device_id = (body.get("nautobot_device_id") or "").strip()
    if not device_id:
        conn.close()
        return deps.Json({"error": "nautobot_device_id is required"}, status_code=400)
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
        return deps.Json({"status": "ok", "nautobot_device_id": device_id, "is_critical": is_critical})
    except Exception as exc:
        logger.exception("Could not set criticality override: %s", exc)
        return deps.Json({"error": "Internal server error"}, status_code=500)
    finally:
        conn.close()


@router.delete(
    "/api/criticality-overrides/{device_id}",
    name="board.api_delete_criticality_override",
    dependencies=[deps.require_role("operator")],
)
def api_delete_criticality_override(device_id: str):
    """Delete a per-device criticality override.

    Returns 404 if no override exists for the given device ID.
    Returns 503 when the DB is not configured.
    """
    conn = db.get_conn()
    if conn is None:
        return deps.Json({"error": "Persistence DB not configured"}, status_code=503)
    try:
        with db.transaction(conn):
            marker = db.placeholders(1)
            cur = conn.execute(
                f"DELETE FROM device_criticality_override WHERE nautobot_device_id = {marker}",
                (device_id,),
            )
        if cur.rowcount == 0:
            return deps.Json({"error": "Override not found"}, status_code=404)
        return deps.Json({"status": "deleted", "nautobot_device_id": device_id})
    except Exception as exc:
        logger.exception("Could not delete criticality override: %s", exc)
        return deps.Json({"error": "Internal server error"}, status_code=500)
    finally:
        conn.close()


# Most entries /api/alert-feed returns at once.
MAX_FEED_ENTRIES = 500


@router.get("/api/alert-feed", name="board.api_alert_feed")
def api_alert_feed(request: Request, query: Annotated[models.FeedQuery, Query()]):
    """What changed on the alert board, newest first (#180).

    Devices going down and back up, and site severity changes.  Query
    parameters: ``limit`` (default 100, at most 500), ``since`` (ISO-8601;
    only newer entries) and ``kinds`` (comma-separated ``down``, ``up``,
    ``severity``; default all).  ``tenant=`` / ``q=`` (as the board's link,
    #369): only those sites' entries, and ``matching_sites``, how many sites
    match (#375).
    """
    try:
        limit = int(query.limit or 100)
    except ValueError:
        return deps.Json({"error": "limit must be a whole number"}, status_code=400)
    limit = max(1, min(limit, MAX_FEED_ENTRIES))
    since = query.since
    if since:
        parsed_since = timeutil.as_utc_datetime(since)
        if parsed_since is None:
            return deps.Json({"error": "since must be an ISO-8601 timestamp"}, status_code=400)
        since = parsed_since.astimezone(UTC).isoformat()
    kinds = [kind.strip() for kind in (query.kinds or "").split(",") if kind.strip()]
    unknown = sorted(set(kinds) - set(alerts.FEED_KINDS))
    if unknown:
        return deps.Json({"error": f"Unknown kinds: {', '.join(unknown)}"}, status_code=400)
    page_scope = scope.read_scope(request.query_params)
    conn = db.get_conn()
    if conn is None:
        return deps.Json({"events": [], "persistence_configured": False})
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
                return deps.Json(
                    {"error": "The site list is not stored yet; try again after the next sync"}, status_code=503
                )
            site_ids = [str(loc["id"]) for loc in locations if loc.get("id") and scope.in_scope(loc, page_scope)]
        events = board_store.read_alert_feed(conn, limit, since, kinds or alerts.FEED_KINDS, site_ids)
        result = {"events": events, "persistence_configured": True}
        if site_ids is not None:
            # 0 tells "no site matches" from "nothing changed" (#375).
            result["matching_sites"] = len(site_ids)
        return deps.Json(result)
    except Exception as exc:
        logger.exception("Could not read the alert feed: %s", exc)
        return deps.Json({"error": "Internal server error"}, status_code=500)
    finally:
        conn.close()


@router.get("/api/alert-history", name="board.api_alert_history", dependencies=[deps.require_role("operator")])
def api_alert_history(query: Annotated[models.HistoryQuery, Query()]):
    """Return historical alert instances with events and case numbers.

    Newest down first (by when the device went down), at most 500.  Query parameters filter them: ``site_id``,
    ``device_id``, and ``start_at`` / ``end_at``: only incidents created at
    or after / at or before this ISO-8601 time.
    """
    conn = db.get_conn()
    if conn is None:
        return deps.Json({"error": "Persistence DB not configured"}, status_code=503)
    site_id = query.site_id or ""
    device_id = query.device_id or ""
    start_at = query.start_at or ""
    end_at = query.end_at or ""
    try:
        if start_at:
            parsed_start_at = timeutil.as_utc_datetime(start_at)
            if parsed_start_at is None:
                return deps.Json({"error": "start_at must be an ISO-8601 timestamp"}, status_code=400)
            start_at = parsed_start_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if end_at:
            parsed_end_at = timeutil.as_utc_datetime(end_at)
            if parsed_end_at is None:
                return deps.Json({"error": "end_at must be an ISO-8601 timestamp"}, status_code=400)
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
        return deps.Json({"instances": instances})
    except Exception as exc:
        logger.exception("Could not fetch alert history: %s", exc)
        return deps.Json({"error": "Internal server error"}, status_code=500)
    finally:
        conn.close()


@router.get("/api/alert-history.csv", name="board.api_alert_history_csv", dependencies=[deps.require_role("operator")])
def api_alert_history_csv(query: Annotated[models.HistoryCsvQuery, Query()]):
    """A site's alert history as a CSV download (#277).

    Query parameters: ``site_id`` (required), ``days`` (``7``, ``30``
    (default), ``90`` or ``all``) and ``view``: ``incidents`` (default, one row
    per time a device went down) or ``devices`` (one row per device: times
    down, total and longest downtime, last down, down now).  Includes every
    incident that was down at any point in the period.  Times are UTC.
    """
    site_id = query.site_id or ""
    days = (query.days or "30").lower()
    view = (query.view or "incidents").lower()
    if not site_id:
        return deps.Json({"error": "site_id is required"}, status_code=400)
    if view not in export.VIEWS:
        return deps.Json({"error": f"view must be one of: {', '.join(export.VIEWS)}"}, status_code=400)
    now = timeutil.parse_iso_datetime(timeutil.iso_utc_now())
    try:
        since = export.period_start(days, now)
    except ValueError:
        return deps.Json({"error": f"days must be one of: {', '.join(export.PERIOD_DAYS)}"}, status_code=400)
    conn = db.get_conn()
    if conn is None:
        return deps.Json({"error": "Persistence DB not configured"}, status_code=503)
    headers = {"Cache-Control": "no-store"}
    try:
        name = export.file_name(export.site_name(conn, site_id), view, days, now)
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
        if view == "devices":
            body = export.devices_csv(export.read_device_summary(conn, site_id, since, now))
            conn.close()
            return Response(body, media_type="text/csv", headers=headers)
    except Exception as exc:
        conn.close()
        logger.exception("Could not export alert history: %s", exc)
        return deps.Json({"error": "Internal server error"}, status_code=500)

    # Incidents are streamed: a site's whole history need not fit in memory.
    def lines():
        try:
            yield from export.incident_lines(export.iter_site_incidents(conn, site_id, since), now)
        except Exception as exc:
            # The status line has been sent; the download ends short.
            logger.exception("Alert history export failed while streaming: %s", exc)
        finally:
            conn.close()

    return StreamingResponse(lines(), media_type="text/csv", headers=headers)


# Upper bound on devices linked to one case in a single request.
MAX_CASE_DEVICES = 200


def _acknowledge(body, clear: bool):
    if not isinstance(body, dict):
        return deps.Json({"error": "Expected a JSON object"}, status_code=400)
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
            return deps.Json(
                {"error": "device_ids must be a non-empty list of device IDs (leave it out for every device)"},
                status_code=400,
            )
        device_ids = [value.strip() for value in raw_ids]
    note = body.get("note")
    if note is None:
        note = ""
    if not isinstance(note, str):
        return deps.Json({"error": "note must be text"}, status_code=400)
    if not site_id:
        return deps.Json({"error": "site_id is required"}, status_code=400)
    if len(device_ids) > MAX_CASE_DEVICES:
        return deps.Json({"error": f"At most {MAX_CASE_DEVICES} devices per request"}, status_code=400)
    conn = db.get_conn()
    if conn is None:
        return deps.Json({"error": "Persistence DB not configured"}, status_code=503)
    by = (auth.get_current_user().get("username") or "").strip()
    try:
        result = alerts.acknowledge(conn, site_id, device_ids, by, note, clear=clear)
    finally:
        conn.close()
    if result["missing"]:
        return deps.Json(
            {
                "error": "No active alert for some devices; nothing was changed",
                "missing_device_ids": result["missing"],
            },
            status_code=404,
        )
    scheduler.board_changed()
    return deps.Json({"site_id": site_id, "changed": result["changed"]})


@router.post(
    "/api/alert-acks",
    name="board.api_acknowledge",
    openapi_extra=deps.body_schema(models.Acknowledge),
    dependencies=[deps.require_role("operator", open_when_disabled=True)],
)
def api_acknowledge(body: Body):
    """Acknowledge a site's alarm (#288): someone is on it, without a case number.

    JSON body: ``site_id``, optional ``device_ids`` (default: every device
    with an open alert at the site) and ``note``.  Shown on the board and the
    wall until the alarm ends; recorded in the history.
    """
    return _acknowledge(body, clear=False)


@router.post(
    "/api/alert-acks/remove",
    name="board.api_unacknowledge",
    openapi_extra=deps.body_schema(models.Acknowledge),
    dependencies=[deps.require_role("operator", open_when_disabled=True)],
)
def api_unacknowledge(body: Body):
    """Remove an acknowledgement (#288): ``site_id``, optional ``device_ids``."""
    return _acknowledge(body, clear=True)


@router.post(
    "/api/alert-cases",
    name="board.api_add_alert_case",
    openapi_extra=deps.body_schema(models.AlertCase),
    dependencies=[deps.require_role("operator", open_when_disabled=True)],
)
def api_add_alert_case(body: Body):
    """Attach a case number to the open alert of one or more devices at a site.

    JSON body: ``site_id``, ``case_number`` and either ``device_ids`` (list)
    or the single ``device_id``.  All-or-nothing: if any device has no open
    alert, nothing is written and the response is 404 with
    ``missing_device_ids``, so a case is never applied to only part of the
    selection.
    """
    body = body or {}
    site_id = (body.get("site_id") or "").strip() if isinstance(body.get("site_id"), str) else ""
    case_number = (body.get("case_number") or "").strip() if isinstance(body.get("case_number"), str) else ""
    raw_ids = body.get("device_ids")
    if raw_ids is None:
        raw_ids = [body.get("device_id")] if body.get("device_id") else []
    if not isinstance(raw_ids, list) or not all(isinstance(value, str) for value in raw_ids):
        return deps.Json({"error": "device_ids must be a list of device ID strings"}, status_code=400)
    # De-duplicate while keeping the operator's order.
    device_ids = list(dict.fromkeys(value.strip() for value in raw_ids if value.strip()))
    if not site_id or not device_ids or not case_number:
        return deps.Json({"error": "site_id, device_ids and case_number are required"}, status_code=400)
    if len(device_ids) > MAX_CASE_DEVICES:
        return deps.Json({"error": f"At most {MAX_CASE_DEVICES} devices per request"}, status_code=400)

    conn = db.get_conn()
    if conn is None:
        return deps.Json({"error": "Persistence DB not configured"}, status_code=503)
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
                return deps.Json(
                    {
                        "error": "No active alert found for some devices; nothing was changed",
                        "missing_device_ids": missing,
                    },
                    status_code=404,
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
        return deps.Json(result)
    except Exception as exc:
        logger.exception("Could not add alert case: %s", exc)
        return deps.Json({"error": "Internal server error"}, status_code=500)
    finally:
        conn.close()
