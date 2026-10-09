"""Maintenance windows (#283): planned work is not an alarm.

A window covers a whole site (``device_id`` empty) or one device, from
``starts_at`` to ``ends_at``.  While it is active the site shows
"maintenance" instead of its level, or the device is left out of the
site's level; the alert history is frozen (nothing opens, nothing
resolves) and nothing is notified.  Ending a window early sets
``ended_at``; a window ended before it started is a cancelled one.
"""

from datetime import UTC, datetime, timedelta

from nautobot_maps import db, timeutil

MAX_DURATION = timedelta(days=14)
MAX_REASON_LENGTH = 200
MAX_DEVICES = 200


class WindowError(ValueError):
    """A request that can't become a window; the message says why."""


def now() -> datetime:
    return timeutil.parse_iso_datetime(timeutil.iso_utc_now())


def _when(value, name: str) -> datetime:
    parsed = timeutil.parse_iso_datetime(value) if isinstance(value, str) and value.strip() else None
    if parsed is None:
        raise WindowError(f"{name} must be an ISO-8601 time")
    if parsed.tzinfo is None:
        raise WindowError(f"{name} needs a time zone (e.g. Z or +02:00)")
    return parsed.astimezone(UTC)


def read_active(conn, at: datetime) -> dict:
    """``{"sites": {site_id: window}, "devices": {device_id: window}}`` active at *at*.

    With several windows for the same site or device, the one ending last wins.
    """
    rows = conn.execute(
        "SELECT id, site_id, device_id, starts_at, ends_at, reason, created_by FROM maintenance_windows "
        "WHERE ended_at IS NULL AND starts_at <= %s AND ends_at > %s ORDER BY ends_at",
        (at, at),
    ).fetchall()
    active = {"sites": {}, "devices": {}}
    for window in map(db.row_to_dict, rows):
        if window["device_id"]:
            active["devices"][window["device_id"]] = window
        else:
            active["sites"][window["site_id"]] = window
    return active


def list_windows(conn, site_id: str = "", include_past: bool = False, at: datetime | None = None) -> list[dict]:
    """Windows with their state (``active``, ``upcoming``, ``ended``, ``cancelled``), soonest first."""
    at = at or now()
    conditions, params = [], []
    if site_id:
        conditions.append("site_id = %s")
        params.append(site_id)
    if not include_past:
        conditions.append("ended_at IS NULL AND ends_at > %s")
        params.append(at)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        "SELECT id, site_id, device_id, starts_at, ends_at, reason, created_by, created_at, ended_at, ended_by "
        f"FROM maintenance_windows {where} ORDER BY starts_at, id LIMIT 500",
        tuple(params),
    ).fetchall()
    return [{**window, "state": state(window, at)} for window in map(db.row_to_dict, rows)]


def state(window: dict, at: datetime) -> str:
    starts = timeutil.parse_iso_datetime(window["starts_at"])
    ends = timeutil.parse_iso_datetime(window["ends_at"])
    ended = timeutil.parse_iso_datetime(window["ended_at"]) if window.get("ended_at") else None
    if ended is not None:
        return "cancelled" if ended < starts else "ended"
    if at < starts:
        return "upcoming"
    return "active" if at < ends else "ended"


def create(conn, body: dict, created_by: str) -> list[dict]:
    """Validate *body* and store the window(s): one for the site, or one per device."""
    site_id = body.get("site_id")
    if not isinstance(site_id, str) or not site_id.strip():
        raise WindowError("site_id is required")
    reason = body.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise WindowError("reason is required")
    if len(reason) > MAX_REASON_LENGTH:
        raise WindowError(f"reason is longer than {MAX_REASON_LENGTH} characters")
    device_ids = body.get("device_ids")
    if device_ids is None:
        device_ids = []
    if not isinstance(device_ids, list) or not all(isinstance(item, str) and item.strip() for item in device_ids):
        raise WindowError("device_ids must be a list of device IDs (leave it out for the whole site)")
    device_ids = list(dict.fromkeys(item.strip() for item in device_ids))
    if len(device_ids) > MAX_DEVICES:
        raise WindowError(f"At most {MAX_DEVICES} devices per window")

    current = now()
    starts = _when(body["starts_at"], "starts_at") if body.get("starts_at") else current
    if body.get("ends_at") is not None:
        ends = _when(body["ends_at"], "ends_at")
    elif body.get("duration_minutes") is not None:
        minutes = body["duration_minutes"]
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
            raise WindowError("duration_minutes must be a positive whole number")
        ends = starts + timedelta(minutes=minutes)
    else:
        raise WindowError("ends_at or duration_minutes is required")
    if ends <= starts:
        raise WindowError("ends_at must be after starts_at")
    if ends <= current:
        raise WindowError("The window has already ended")
    if ends - starts > MAX_DURATION:
        raise WindowError(f"A window can last at most {MAX_DURATION.days} days")

    created = []
    with db.transaction(conn):
        for device_id in device_ids or [""]:
            row = conn.execute(
                "INSERT INTO maintenance_windows (site_id, device_id, starts_at, ends_at, reason, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (site_id.strip(), device_id, starts, ends, reason.strip(), created_by),
            ).fetchone()
            created.append(db.row_to_dict(row)["id"])
    rows = conn.execute(
        "SELECT id, site_id, device_id, starts_at, ends_at, reason, created_by, created_at, ended_at, ended_by "
        "FROM maintenance_windows WHERE id = ANY(%s) ORDER BY id",
        (created,),
    ).fetchall()
    return [{**window, "state": state(window, current)} for window in map(db.row_to_dict, rows)]


def end(conn, window_id: int, ended_by: str) -> dict | None:
    """End an active window now, or cancel an upcoming one.  None when it doesn't exist.

    Ending one that is already over changes nothing and returns it as it is.
    """
    current = now()
    with db.transaction(conn):
        row = conn.execute(
            "SELECT id, site_id, device_id, starts_at, ends_at, reason, created_by, created_at, ended_at, ended_by "
            "FROM maintenance_windows WHERE id = %s FOR UPDATE",
            (window_id,),
        ).fetchone()
        if row is None:
            return None
        window = db.row_to_dict(row)
        if state(window, current) in ("active", "upcoming"):
            conn.execute(
                "UPDATE maintenance_windows SET ended_at = %s, ended_by = %s WHERE id = %s",
                (current, ended_by, window_id),
            )
            window["ended_at"] = timeutil.iso_utc_now()
            window["ended_by"] = ended_by
    return {**window, "state": state(window, current)}
