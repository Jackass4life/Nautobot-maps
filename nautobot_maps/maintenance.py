"""Maintenance windows (#283): planned work is not an alarm.

A window covers a whole site (``device_id`` empty) or one device, from
``starts_at`` to ``ends_at``.  While it is active the site shows
"maintenance" instead of its level, or the device is left out of the
site's level; the alert history is frozen (nothing opens, nothing
resolves) and nothing is notified.  Ending a window early sets
``ended_at``; a window ended before it started is a cancelled one.
"""

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta

from nautobot_maps import db, export, status, timeutil

MAX_DURATION = timedelta(days=14)
MAX_REASON_LENGTH = 200
MAX_DEVICES = 200
# Sites in one request (#343): a core change across a region, in one transaction.
MAX_SITES = 200
# How far back the page's "Ended" group reaches (#343).
MAX_RECENT_DAYS = 90
# The page's list: the most it reads; open windows (active, planned) come first.
LIST_LIMIT = 2000
# The history (#353): the most windows the page gets (then "truncated"), and
# the most an export holds (past that it asks for a shorter period).
HISTORY_LIMIT = 5000
HISTORY_EXPORT_LIMIT = 100_000
# Windows ended in one request (#343).
MAX_END_IDS = 500
# What a window is, as read and returned (#343: batch_id, the request it was planned in).
COLUMNS = "id, site_id, device_id, starts_at, ends_at, reason, created_by, created_at, ended_at, ended_by, batch_id"


class WindowError(ValueError):
    """A request that can't become a window; ``message`` says why (written here, safe to show)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


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
    """``{"sites": {site_id: window}, "devices": {device_id: window}, "next_change": iso | None}``.

    The windows active at *at* (with several for the same site or device,
    the one ending last wins), and when the next one starts or ends: a
    cached board or map must not outlive that.
    """
    rows = conn.execute(
        "SELECT id, site_id, device_id, starts_at, ends_at, reason, created_by FROM maintenance_windows "
        "WHERE ended_at IS NULL AND starts_at <= %s AND ends_at > %s ORDER BY ends_at",
        (at, at),
    ).fetchall()
    next_change = conn.execute(
        "SELECT LEAST("
        "  (SELECT MIN(starts_at) FROM maintenance_windows WHERE ended_at IS NULL AND starts_at > %s),"
        "  (SELECT MIN(ends_at) FROM maintenance_windows WHERE ended_at IS NULL AND ends_at > %s)"
        ") AS next_change",
        (at, at),
    ).fetchone()
    active = {"sites": {}, "devices": {}, "next_change": db.row_to_dict(next_change).get("next_change")}
    for window in map(db.row_to_dict, rows):
        if window["device_id"]:
            active["devices"][window["device_id"]] = window
        else:
            active["sites"][window["site_id"]] = window
    return active


def cache_seconds(next_change: str | None, default: int) -> int:
    """How long a cached board or map may live: until *next_change* (the next
    window start or end, from ``read_active``), at most *default*."""
    upcoming = timeutil.parse_iso_datetime(next_change) if next_change else None
    if upcoming is None:
        return default
    # At least 1: a cache timeout of 0 means "never expires".
    return max(1, min(default, int((upcoming - now()).total_seconds()) + 1))


def list_windows(
    conn,
    site_id: str = "",
    include_past: bool = False,
    at: datetime | None = None,
    recent_days: int = 0,
    limit: int = 500,
) -> list[dict]:
    """Windows with their state (``active``, ``upcoming``, ``ended``, ``cancelled``):
    the open ones first (#343, so ended ones never crowd them out), soonest first.

    *recent_days* (#343): also those that ended or were cancelled in the last that many days.
    At most *limit*.
    """
    at = at or now()
    conditions, params = [], []
    if site_id:
        conditions.append("site_id = %s")
        params.append(site_id)
    if recent_days and not include_past:
        # Not over before *since*: ended_at is never after ends_at, so the
        # indexes on each serve it (a COALESCE would read the whole table).
        since = at - timedelta(days=recent_days)
        conditions.append("(ended_at IS NULL AND ends_at > %s OR ended_at > %s)")
        params += [since, since]
    elif not include_past:
        conditions.append("ended_at IS NULL AND ends_at > %s")
        params.append(at)
    order = "(ended_at IS NOT NULL OR ends_at <= %s), starts_at, id"
    return _read(conn, conditions, [*params, at], order, limit, at)


def _read(conn, conditions: list[str], params: list, order: str, limit: int, at: datetime) -> list[dict]:
    """The windows *conditions* select, in *order*, at most *limit*, each with its state at *at*."""
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM maintenance_windows {where} ORDER BY {order} LIMIT %s",
        (*params, limit),
    ).fetchall()
    return [{**window, "state": state(window, at)} for window in map(db.row_to_dict, rows)]


def history(
    conn,
    start: datetime,
    end: datetime,
    site_ids: list[str] | None = None,
    limit: int = HISTORY_LIMIT,
    at: datetime | None = None,
) -> list[dict]:
    """Every window that ran in *start*–*end*, or was planned for it and
    cancelled (#353), newest first: the history view, one site's (*site_ids*:
    those sites only) and the export.  A window ended early, before *start*,
    didn't run in it.  The (ends_at, starts_at) and ended_at indexes serve it."""
    conditions = [
        "starts_at < %s",
        "ends_at > %s",
        # Still on, ended in the period, or cancelled (ended before it began).
        "(ended_at IS NULL OR ended_at > %s OR ended_at < starts_at)",
    ]
    params: list = [end, start, start]
    if site_ids is not None:
        conditions.append("site_id = ANY(%s)")
        params.append(site_ids)
    return _read(conn, conditions, params, "starts_at DESC, id DESC", limit, at or now())


HISTORY_COLUMNS = (
    "site",
    "site_path",
    "tenants",
    "device",
    "state",
    "planned_start_utc",
    "planned_end_utc",
    "ended_at_utc",
    "minutes",
    "reason",
    "planned_by",
    "planned_at_utc",
    "ended_by",
    "batch",
    "site_id",
    "device_id",
)


def history_minutes(window: dict, at: datetime) -> int:
    """How long it ran: from its start to when it ended (early, or as planned),
    up to *at* while it runs; 0 when cancelled or not started."""
    starts = timeutil.parse_iso_datetime(window["starts_at"])
    stop = min(timeutil.parse_iso_datetime(window.get("ended_at") or window["ends_at"]), at)
    return max(0, round((stop - starts).total_seconds() / 60))


def history_csv(windows: list[dict]) -> str:
    """The history (from ``describe``) as CSV (#353): one row per window,
    times in UTC, formula-safe like the other exports."""
    at = now()
    out = io.StringIO()
    out.write("\ufeff")  # Excel reads UTF-8
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(HISTORY_COLUMNS)
    for w in windows:
        row = (
            w.get("site_name") or w["site_id"],
            w.get("site_path", ""),
            "; ".join(w.get("tenants") or []),
            w.get("device_name") or w.get("device_id") or "",
            w["state"],
            export.utc_text(w["starts_at"]),
            export.utc_text(w["ends_at"]),
            export.utc_text(w.get("ended_at")),
            history_minutes(w, at),
            w.get("reason", ""),
            w.get("created_by", ""),
            export.utc_text(w.get("created_at")),
            w.get("ended_by", ""),
            w.get("batch_id", ""),
            w["site_id"],
            w.get("device_id", ""),
        )
        writer.writerow([export.safe_cell(value) for value in row])
    return out.getvalue()


def state(window: dict, at: datetime) -> str:
    starts = timeutil.parse_iso_datetime(window["starts_at"])
    ends = timeutil.parse_iso_datetime(window["ends_at"])
    ended = timeutil.parse_iso_datetime(window["ended_at"]) if window.get("ended_at") else None
    if ended is not None:
        return "cancelled" if ended < starts else "ended"
    if at < starts:
        return "upcoming"
    return "active" if at < ends else "ended"


def _site_ids(body: dict) -> list[str]:
    """``site_id``, or ``site_ids`` for several sites at once (#343)."""
    if body.get("site_ids") is not None:
        site_ids = body["site_ids"]
        if (
            not isinstance(site_ids, list)
            or not site_ids
            or not all(isinstance(i, str) and i.strip() for i in site_ids)
        ):
            raise WindowError("site_ids must be a list of site IDs")
        site_ids = list(dict.fromkeys(item.strip() for item in site_ids))
        if len(site_ids) > MAX_SITES:
            raise WindowError(f"At most {MAX_SITES} sites at once")
        return site_ids
    site_id = body.get("site_id")
    if not isinstance(site_id, str) or not site_id.strip():
        raise WindowError("site_id is required")
    return [site_id.strip()]


def create(conn, body: dict, created_by: str) -> list[dict]:
    """Validate *body* and store the window(s), in one transaction: one per
    site (``site_id`` or ``site_ids``), or one per device of one site."""
    site_ids = _site_ids(body)
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
    if device_ids and len(site_ids) > 1:
        # A device belongs to one site: its window says which.
        raise WindowError("Choose devices for one site at a time")

    current = now()
    starts = _when(body["starts_at"], "starts_at") if body.get("starts_at") else current
    if body.get("ends_at") is not None:
        ends = _when(body["ends_at"], "ends_at")
    elif body.get("duration_minutes") is not None:
        minutes = body["duration_minutes"]
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
            raise WindowError("duration_minutes must be a positive whole number")
        # Checked before building the timedelta: a huge number would overflow it.
        if minutes > MAX_DURATION.total_seconds() / 60:
            raise WindowError(f"A window can last at most {MAX_DURATION.days} days")
        ends = starts + timedelta(minutes=minutes)
    else:
        raise WindowError("ends_at or duration_minutes is required")
    if ends <= starts:
        raise WindowError("ends_at must be after starts_at")
    if ends <= current:
        raise WindowError("The window has already ended")
    if ends - starts > MAX_DURATION:
        raise WindowError(f"A window can last at most {MAX_DURATION.days} days")

    targets = [(site_ids[0], device_id) for device_id in device_ids] or [(site_id, "") for site_id in site_ids]
    with db.transaction(conn):
        # One statement for every site or device (#343), not one per row; one batch.
        rows = conn.execute(
            "INSERT INTO maintenance_windows (site_id, device_id, starts_at, ends_at, reason, created_by, batch_id) "
            "SELECT site_id, device_id, %s, %s, %s, %s, %s "
            "FROM UNNEST(%s::text[], %s::text[]) AS t(site_id, device_id) "
            f"RETURNING {COLUMNS}",
            (
                starts,
                ends,
                reason.strip(),
                created_by,
                uuid.uuid4().hex,
                [t[0] for t in targets],
                [t[1] for t in targets],
            ),
        ).fetchall()
    windows = sorted(map(db.row_to_dict, rows), key=lambda window: window["id"])
    return [{**window, "state": state(window, current)} for window in windows]


def end_many(conn, window_ids: list[int], ended_by: str) -> dict:
    """End or cancel several windows at once (#343): a batch planned together.
    ``{"windows", "changed"}``: unknown IDs are left out, ones already over
    returned as they are; *changed* counts those ended or cancelled now."""
    current = now()
    with db.transaction(conn):
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM maintenance_windows WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
            (window_ids,),
        ).fetchall()
        windows = list(map(db.row_to_dict, rows))
        open_ids = {w["id"] for w in windows if state(w, current) in ("active", "upcoming")}
        if open_ids:
            conn.execute(
                "UPDATE maintenance_windows SET ended_at = %s, ended_by = %s WHERE id = ANY(%s)",
                (current, ended_by, list(open_ids)),
            )
        for window in windows:
            if window["id"] in open_ids:
                window["ended_at"] = db.serialize_value(current)
                window["ended_by"] = ended_by
    return {"windows": [{**window, "state": state(window, current)} for window in windows], "changed": len(open_ids)}


def describe(conn, windows: list[dict], sites: list[dict]) -> dict:
    """Names for the page (#343): each window's site name and tenants (from
    the board's rows, *sites*) and device name, in one query for all of
    them; and the tenants' descriptions, once."""
    by_site = {site.get("id"): site for site in sites if site.get("id")}
    device_ids = sorted({w["device_id"] for w in windows if w.get("device_id")})
    names = {}
    if device_ids:
        rows = conn.execute(
            "SELECT device_id, name FROM nautobot_device_cache WHERE device_id = ANY(%s)", (device_ids,)
        ).fetchall()
        names = {row["device_id"]: row["name"] for row in map(db.row_to_dict, rows)}
    descriptions: dict[str, str] = {}
    described = []
    for window in windows:
        site = by_site.get(window["site_id"]) or {}
        tenants = status.site_tenants(site)
        for tenant in tenants:
            description = (site.get("tenant_descriptions") or {}).get(tenant)
            if description:
                descriptions[tenant] = description
        described.append(
            {
                **window,
                "site_name": site.get("name") or "",
                "site_path": status.site_path(site),
                "tenants": tenants,
                "device_name": names.get(window.get("device_id") or "", ""),
            }
        )
    return {"windows": described, "tenant_descriptions": descriptions}


def end(conn, window_id: int, ended_by: str) -> dict | None:
    """End an active window now, or cancel an upcoming one.  None when it doesn't exist.

    Ending one that is already over changes nothing and returns it as it is.
    """
    windows = end_many(conn, [window_id], ended_by)["windows"]
    return windows[0] if windows else None
