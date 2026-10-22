"""Maintenance (#424): the page, windows now and planned, their history, and
planning and ending them."""

import logging
import re
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from nautobot_maps import (
    alerts,
    auth,
    availability,
    db,
    maintenance,
    scheduler,
    status,
)
from nautobot_maps.routes import common

logger = logging.getLogger(__name__)

bp = Blueprint("maintenance", __name__)


@bp.route("/api/maintenance", methods=["GET"])
def api_maintenance_list():
    """Maintenance windows (#283): active and upcoming, soonest first.

    Query parameters: ``site_id`` (one site), ``all=1`` (also ended and
    cancelled ones) and ``recent_days`` (also those that ended in the last
    that many days, at most 90; #343).  Each has a ``state``: active,
    upcoming, ended or cancelled; ``device_id`` is empty for a whole site.
    Each also has ``site_name``, ``site_path``, ``tenants`` and
    ``device_name``; ``tenant_descriptions`` has the tenants' descriptions.
    """
    try:
        recent_days = min(max(int(common.arg("recent_days") or 0), 0), maintenance.MAX_RECENT_DAYS)
    except ValueError:
        return jsonify({"error": "recent_days must be a whole number of days"}), 400
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        site_id = common.arg("site_id") or ""
        windows = maintenance.list_windows(
            conn,
            site_id=site_id,
            include_past=common.flag_arg("all"),
            recent_days=recent_days,
            limit=maintenance.LIST_LIMIT + 1,
        )
        truncated = len(windows) > maintenance.LIST_LIMIT
        windows = windows[: maintenance.LIST_LIMIT]
        # One site's panel on the board knows its names: no board read for it.
        if site_id:
            payload = {"windows": windows, "tenant_descriptions": {}}
        else:
            payload = maintenance.describe(conn, windows, _board_sites())
        return jsonify({**payload, "truncated": truncated})
    finally:
        conn.close()


def _board_sites() -> list[dict]:
    """The board's rows, for names and tenants; none when it can't be read (names stay empty)."""
    try:
        return alerts.cached_alert_board().get("alerts") or []
    except Exception as exc:
        logger.warning("Maintenance: could not read the alert board for names: %s", exc)
        return []


# The Maintenance page's "Ended" group (#343).
MAINTENANCE_PAGE_RECENT_DAYS = 7


@bp.route("/maintenance")
def maintenance_page():
    """Current and planned maintenance, and planning more (#343); the data comes from the API."""
    return render_template(
        "maintenance.html",
        recent_days=MAINTENANCE_PAGE_RECENT_DAYS,
        max_sites=maintenance.MAX_SITES,
        view="history" if common.arg("view") == "history" else "now",
        history_days=MAINTENANCE_HISTORY_DEFAULT_DAYS,
    )


# The history's default period (#353): the last 30 days, today included.
MAINTENANCE_HISTORY_DEFAULT_DAYS = 30


def _history_request() -> tuple[dict | None, tuple | None]:
    """``(request, None)`` or ``(None, error response)`` for the history's query
    string (#353): ``from`` and ``to`` (days, both included; default the last 30),
    ``tz`` (the browser's time zone, e.g. ``Europe/Copenhagen``: its days, not
    UTC's; default UTC), ``site_id`` and ``tenant``.  The days are checked as
    the availability report's (#341)."""
    try:
        zone = ZoneInfo(common.arg("tz") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return None, (jsonify({"error": "tz must be a time zone, like Europe/Copenhagen"}), 400)
    at = availability.now()
    from_date, to_date = common.arg("from"), common.arg("to")
    if not (from_date or to_date):
        today = at.astimezone(zone).date()
        to_date = today.isoformat()
        from_date = (today - timedelta(days=MAINTENANCE_HISTORY_DEFAULT_DAYS - 1)).isoformat()
    try:
        period = availability.resolve_period(None, from_date, to_date, at, zone)
    except availability.ReportError as exc:
        return None, (jsonify({"error": exc.message}), 400)
    site_id, tenant = common.arg("site_id") or "", common.arg("tenant") or ""
    sites = _board_sites()
    # One list of sites for both filters, chosen before any limit; a site no
    # longer on the board still has its history, unless a tenant rules it out.
    site_ids = None
    if site_id or tenant:
        candidates = {site_id: {}} if site_id else {s["id"]: s for s in sites if s.get("id")}
        if site_id:
            candidates[site_id] = next((s for s in sites if s.get("id") == site_id), {})
        site_ids = [i for i, s in candidates.items() if not tenant or tenant in status.site_tenants(s)]
    return {
        "period": period,
        # To the end of the last day, not now: a window starting later today is in it.
        "start": period["start"],
        "end": period["until"],
        "site_id": site_id,
        "site_ids": site_ids,
        "sites": sites,
        "zone": str(zone),
    }, None


@bp.route("/api/maintenance/history", methods=["GET"])
def api_maintenance_history():
    """Every maintenance window that ran in a period, or was planned for it and
    cancelled (#353), newest first: ``from`` and ``to`` (``2026-10-01``, both
    days included; default the last 30 days; at most 366), optional ``tz``
    (whose days; default UTC), ``site_id`` and ``tenant``.  Names and tenants
    as ``/api/maintenance``; ``truncated`` past 5,000 windows."""
    req, error = _history_request()
    if error:
        return error
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        limit = maintenance.HISTORY_LIMIT
        windows = maintenance.history(conn, req["start"], req["end"], req["site_ids"], limit=limit + 1)
        described = maintenance.describe(conn, windows[:limit], req["sites"])
    finally:
        conn.close()
    period = req["period"]
    return jsonify(
        {
            **described,
            "period": {"from": period["from"], "to": period["to"], "label": period["label"], "tz": req["zone"]},
            "site_id": req["site_id"],
            "truncated": len(windows) > limit,
        }
    )


@bp.route("/api/maintenance/history.csv", methods=["GET"])
def api_maintenance_history_csv():
    """The history as CSV (#353): the same ``from``, ``to``, ``tz``, ``site_id``
    and ``tenant``; one row per window, times in UTC, streamed.  Every window,
    or a 400 past 100,000: never a silently shorter file."""
    req, error = _history_request()
    if error:
        return error
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    limit = maintenance.HISTORY_EXPORT_LIMIT
    try:
        too_many = maintenance.history_exceeds(conn, req["start"], req["end"], req["site_ids"], limit)
    except Exception:
        conn.close()
        raise
    if too_many:
        conn.close()
        return jsonify({"error": f"More than {limit:,} windows: choose a shorter period, a site or a tenant"}), 400

    def lines():
        try:
            yield from maintenance.history_csv_lines(conn, req["start"], req["end"], req["site_ids"], req["sites"])
        finally:
            conn.close()

    name = req["period"]["label"].replace(" to ", "_")
    if req["site_id"]:
        name = f"{name}-{re.sub(r'[^A-Za-z0-9_.-]+', '-', req['site_id'])}"
    return Response(
        stream_with_context(lines()),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="maintenance-{name}.csv"'},
    )


@bp.route("/api/maintenance/sites", methods=["GET"])
def api_maintenance_sites():
    """The sites maintenance can be planned for (#343), for the page's site
    picker: ``id``, ``name``, ``path``, ``tenants``, ``devices`` (monitored)
    and ``maintenance`` (in a window now).  From the stored board; no query
    per site."""
    return jsonify(
        {
            "sites": [
                {
                    "id": site["id"],
                    "name": site.get("name") or site["id"],
                    "path": status.site_path(site),
                    "tenants": status.site_tenants(site),
                    "devices": site.get("device_count") or 0,
                    "maintenance": site.get("alert_level") == "maintenance",
                }
                for site in _board_sites()
                if site.get("id") and (site.get("device_count") or site.get("maintenance_device_count"))
            ]
        }
    )


@bp.route("/api/maintenance/devices", methods=["GET"])
def api_maintenance_devices():
    """The devices that can be put in maintenance at a site (#283).

    Query parameters: ``site_id`` (a row of the alert board) and
    ``include_non_operational=1`` (as on the board).  The devices the board
    counts there: with a primary IP, not of an excluded status, and those
    below the site when rows are rolled up to a location type.
    """
    site_id = (request.args.get("site_id") or "").strip()
    if not site_id:
        return jsonify({"error": "site_id is required"}), 400
    include_non_operational = (request.args.get("include_non_operational") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    return jsonify({"devices": alerts.site_monitored_devices(site_id, include_non_operational=include_non_operational)})


def _maintenance_changed() -> None:
    """The board and the map show maintenance: drop their cached copies."""
    scheduler.board_changed()  # also the map's marker colours


@bp.route("/api/maintenance", methods=["POST"])
@auth.require_role("operator")
def api_maintenance_create():
    """Put a site, or some of its devices, in maintenance (#283).

    JSON body: ``site_id`` (or ``site_ids``, up to 200 sites at once, #343),
    ``reason``, optional ``device_ids`` (one site only; leave it out
    for the whole site), optional ``starts_at`` (ISO-8601 with a time zone;
    default now), and ``ends_at`` or ``duration_minutes``.  At most 14 days.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        conn.close()
        return jsonify({"error": "Expected a JSON object"}), 400
    try:
        windows = maintenance.create(conn, body, auth.get_current_user().get("username") or "")
    except maintenance.WindowError as exc:
        return jsonify({"error": exc.message}), 400
    finally:
        conn.close()
    _maintenance_changed()
    return jsonify({"windows": windows}), 201


@bp.route("/api/maintenance/end", methods=["POST"])
@auth.require_role("operator")
def api_maintenance_end_many():
    """End or cancel several windows at once (#343): JSON ``{"ids": [1, 2, ...]}``,
    at most ``maintenance.MAX_END_IDS``; ``not_found`` lists the unknown ones."""
    body = request.get_json(silent=True)
    ids = body.get("ids") if isinstance(body, dict) else None
    if (
        not isinstance(ids, list)
        or not ids
        or len(ids) > maintenance.MAX_END_IDS
        # Window IDs are BIGSERIAL: anything else would fail in the query, not here.
        or not all(isinstance(i, int) and not isinstance(i, bool) and 0 < i < 2**63 for i in ids)
    ):
        return jsonify({"error": f"ids must be a list of 1 to {maintenance.MAX_END_IDS} window IDs"}), 400
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        result = maintenance.end_many(conn, ids, auth.get_current_user().get("username") or "")
    finally:
        conn.close()
    # Nothing ended (already over, or unknown): the board and map stay cached.
    if result["changed"]:
        _maintenance_changed()
    found = {window["id"] for window in result["windows"]}
    return jsonify({"windows": result["windows"], "not_found": [i for i in ids if i not in found]})


@bp.route("/api/maintenance/<int:window_id>/end", methods=["POST"])
@auth.require_role("operator")
def api_maintenance_end(window_id: int):
    """End a maintenance window now, or cancel an upcoming one (#283)."""
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        window = maintenance.end(conn, window_id, auth.get_current_user().get("username") or "")
    finally:
        conn.close()
    if window is None:
        return jsonify({"error": "No such maintenance window"}), 404
    _maintenance_changed()
    return jsonify({"window": window})
