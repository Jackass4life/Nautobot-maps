"""A site's alert history as CSV (#277): one row per incident, or per device.

An incident is one time a device went down and (maybe) came back.  Role and
IP are the device's current ones (incidents don't store them): Nautobot's
primary IP, else the address LibreNMS polls; blank once the device is gone.
"""

import csv
import io
import re
from datetime import UTC, datetime, timedelta

from nautobot_maps import db, inventory, timeutil

# The periods the History panel offers; "all" is everything the database keeps.
PERIOD_DAYS = {"7": 7, "30": 30, "90": 90, "all": None}
VIEWS = ("incidents", "devices")

INCIDENT_COLUMNS = (
    "site",
    "device",
    "role",
    "ip",
    "down_at_utc",
    "up_at_utc",
    "duration_min",
    "status",
    "level",
    "reason",
    "cases",
)
DEVICE_COLUMNS = (
    "site",
    "device",
    "role",
    "ip",
    "times_down",
    "total_down_min",
    "longest_min",
    "last_down_at_utc",
    "down_now",
)

# Excel runs a cell starting with one of these as a formula: a site or device
# name like "=HYPERLINK(...)" must stay text.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


# Role and IP come from the device as it is now; the LibreNMS fallback is
# reached through the device-cache row, so a device deleted from Nautobot
# (whose LibreNMS mapping may linger) exports neither (Copilot on #278).
_FROM = """
    FROM alert_instances i
    LEFT JOIN nautobot_device_cache d ON d.device_id = i.device_id
    LEFT JOIN librenms_device_map m ON m.nautobot_device_id = d.device_id
    LEFT JOIN librenms_device_status s ON s.device_id = m.librenms_device_id
"""
# Rows read from the database at a time while streaming the incidents.
STREAM_BATCH = 500


def _where(since: datetime | None) -> tuple[str, list]:
    p_site, p_since = db.placeholders(2).split(",")
    period = "" if since is None else f" AND (i.resolved_at IS NULL OR i.resolved_at >= {p_since})"
    return f"WHERE i.site_id = {p_site}{period}", ([] if since is None else [since])


def iter_site_incidents(conn, site_id: str, since: datetime | None):
    """The site's incidents that were down at any point since *since*, newest first.

    Read with a server-side cursor, a batch at a time: history is kept
    forever by default, so a site's export must not be held in memory.
    """
    where, params = _where(since)
    query = f"""
        SELECT i.id, i.site_name, i.device_id, i.device_name, i.alert_level, i.alert_reason, i.status,
               i.down_started_at, i.resolved_at, i.total_downtime_seconds,
               d.role, d.primary_ip, s.ip AS librenms_ip,
               (SELECT string_agg(c.case_number, ';' ORDER BY c.case_number)
                  FROM alert_cases c WHERE c.alert_instance_id = i.id) AS cases
        {_FROM}
        {where}
        ORDER BY i.down_started_at DESC, i.id DESC
    """
    # A named (server-side) cursor needs a transaction; connections autocommit.
    with conn.transaction(), conn.cursor(name="alert_history_export") as cursor:
        cursor.itersize = STREAM_BATCH
        cursor.execute(query, [site_id, *params])
        for row in cursor:
            yield db.row_to_dict(row)


def site_name(conn, site_id: str) -> str:
    """For the file name: the site as Nautobot names it now, else as its newest incident did."""
    name = inventory.read_location_name_map(conn).get(site_id)
    if name:
        return name
    row = conn.execute(
        f"SELECT site_name FROM alert_instances WHERE site_id = {db.placeholders(1)} ORDER BY id DESC LIMIT 1",
        (site_id,),
    ).fetchone()
    return db.row_to_dict(row).get("site_name") or site_id


def read_device_summary(conn, site_id: str, since: datetime | None, now: datetime) -> list[dict]:
    """One row per device, summed by the database: most often down first."""
    where, params = _where(since)
    p_now = db.placeholders(1)
    seconds = (
        f"CASE WHEN i.status = 'open' OR i.resolved_at IS NULL "
        f"THEN GREATEST(0, EXTRACT(EPOCH FROM ({p_now} - i.down_started_at)))::bigint "
        f"ELSE i.total_downtime_seconds END"
    )
    rows = conn.execute(
        f"""
        SELECT MAX(i.site_name) AS site_name, i.device_id, MAX(i.device_name) AS device_name,
               MAX(d.role) AS role, MAX(d.primary_ip) AS primary_ip, MAX(s.ip) AS librenms_ip,
               COUNT(*) AS times_down, SUM({seconds}) AS total_seconds, MAX({seconds}) AS longest_seconds,
               MAX(i.down_started_at) AS last_down_at, BOOL_OR(i.status = 'open') AS down_now
        {_FROM}
        {where}
        GROUP BY i.device_id
        ORDER BY COUNT(*) DESC, SUM({seconds}) DESC, LOWER(MAX(i.device_name))
        """,
        # In the order the placeholders appear: SELECT (2), WHERE, ORDER BY.
        [now, now, site_id, *params, now],
    ).fetchall()
    return [db.row_to_dict(row) for row in rows]


def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = timeutil.parse_iso_datetime(value) if value else None
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _utc_text(value) -> str:
    parsed = _as_datetime(value)
    return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M") if parsed else ""


def duration_seconds(row: dict, now: datetime) -> int:
    """How long the incident was down; until *now* while it still is."""
    started = _as_datetime(row.get("down_started_at"))
    if row.get("status") == "open" or row.get("resolved_at") is None:
        return max(0, int((now - started).total_seconds())) if started else 0
    return int(row.get("total_downtime_seconds") or 0)


def _minutes(seconds: int) -> int:
    return round(seconds / 60)


def _ip(row: dict) -> str:
    primary = (row.get("primary_ip") or "").strip()
    return primary.split("/", 1)[0] if primary else (row.get("librenms_ip") or "").strip()


def safe_cell(value) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_START) else text


def _line(values) -> str:
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerow([safe_cell(value) for value in values])
    return out.getvalue()


def _header(columns: tuple) -> str:
    # The byte-order mark makes Excel read UTF-8 (Danish letters and all).
    return "\ufeff" + _line(columns)


def incident_lines(incidents, now: datetime):
    """The incidents CSV, a line at a time, so it can be streamed."""
    yield _header(INCIDENT_COLUMNS)
    for row in incidents:
        yield _line(
            [
                row.get("site_name"),
                row.get("device_name") or row.get("device_id"),
                row.get("role"),
                _ip(row),
                _utc_text(row.get("down_started_at")),
                "" if row.get("status") == "open" else _utc_text(row.get("resolved_at")),
                _minutes(duration_seconds(row, now)),
                row.get("status"),
                row.get("alert_level"),
                row.get("alert_reason"),
                row.get("cases") or "",
            ]
        )


def devices_csv(summary: list[dict]) -> str:
    """The per-device CSV from ``read_device_summary`` (a row per device: small)."""
    return _header(DEVICE_COLUMNS) + "".join(
        _line(
            [
                row.get("site_name"),
                row.get("device_name") or row.get("device_id"),
                row.get("role"),
                _ip(row),
                row.get("times_down"),
                _minutes(int(row.get("total_seconds") or 0)),
                _minutes(int(row.get("longest_seconds") or 0)),
                _utc_text(row.get("last_down_at")),
                "yes" if row.get("down_now") else "no",
            ]
        )
        for row in summary
    )


def period_start(days: str, now: datetime) -> datetime | None:
    """The start of the period, or None for everything; raises ValueError for an unknown one."""
    if days not in PERIOD_DAYS:
        raise ValueError(days)
    count = PERIOD_DAYS[days]
    return None if count is None else now - timedelta(days=count)


def file_name(site_name: str, view: str, days: str, now: datetime) -> str:
    """e.g. ``london-hq-incidents-30d-2026-10-07.csv``: ASCII only, safe in a header."""
    slug = re.sub(r"[^a-z0-9]+", "-", (site_name or "site").lower()).strip("-") or "site"
    period = "all" if days == "all" else f"{days}d"
    return f"{slug[:60]}-{view}-{period}-{now.astimezone(UTC):%Y-%m-%d}.csv"
