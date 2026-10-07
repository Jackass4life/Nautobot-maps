"""A site's alert history as CSV (#277): one row per incident, or per device.

An incident is one time a device went down and (maybe) came back.  Role and
IP are the device's current ones (incidents don't store them): Nautobot's
primary IP, else the address LibreNMS polls; blank once the device is gone.
"""

import csv
import io
import re
from datetime import UTC, datetime, timedelta

from nautobot_maps import db, timeutil

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


def read_site_incidents(conn, site_id: str, since: datetime | None) -> list[dict]:
    """The site's incidents that were down at any point since *since*, newest first."""
    p_site, p_since = db.placeholders(2).split(",")
    period = "" if since is None else f"AND (i.resolved_at IS NULL OR i.resolved_at >= {p_since})"
    rows = conn.execute(
        f"""
        SELECT i.id, i.site_name, i.device_id, i.device_name, i.alert_level, i.alert_reason, i.status,
               i.down_started_at, i.resolved_at, i.total_downtime_seconds,
               d.role, d.primary_ip, s.ip AS librenms_ip,
               (SELECT string_agg(c.case_number, ';' ORDER BY c.case_number)
                  FROM alert_cases c WHERE c.alert_instance_id = i.id) AS cases
        FROM alert_instances i
        LEFT JOIN nautobot_device_cache d ON d.device_id = i.device_id
        LEFT JOIN librenms_device_map m ON m.nautobot_device_id = i.device_id
        LEFT JOIN librenms_device_status s ON s.device_id = m.librenms_device_id
        WHERE i.site_id = {p_site} {period}
        ORDER BY i.down_started_at DESC, i.id DESC
        """,
        (site_id,) if since is None else (site_id, since),
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


def _csv(columns: tuple, rows: list[list]) -> str:
    out = io.StringIO()
    # The byte-order mark makes Excel read UTF-8 (Danish letters and all).
    out.write("﻿")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(columns)
    writer.writerows([safe_cell(value) for value in row] for row in rows)
    return out.getvalue()


def incidents_csv(incidents: list[dict], now: datetime) -> str:
    return _csv(
        INCIDENT_COLUMNS,
        [
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
            for row in incidents
        ],
    )


def devices_csv(incidents: list[dict], now: datetime) -> str:
    """One row per device: how often, how long, and whether it is down now; most often first."""
    devices: dict[str, dict] = {}
    for row in incidents:
        key = row.get("device_id") or row.get("device_name") or ""
        device = devices.setdefault(
            key,
            {
                "site": row.get("site_name"),
                "device": row.get("device_name") or row.get("device_id"),
                "role": row.get("role"),
                "ip": _ip(row),
                "times": 0,
                "total": 0,
                "longest": 0,
                "last": None,
                "down_now": False,
            },
        )
        seconds = duration_seconds(row, now)
        device["times"] += 1
        device["total"] += seconds
        device["longest"] = max(device["longest"], seconds)
        started = _as_datetime(row.get("down_started_at"))
        if started and (device["last"] is None or started > device["last"]):
            device["last"] = started
        device["down_now"] = device["down_now"] or row.get("status") == "open"
    ordered = sorted(devices.values(), key=lambda d: (-d["times"], -d["total"], str(d["device"]).lower()))
    return _csv(
        DEVICE_COLUMNS,
        [
            [
                d["site"],
                d["device"],
                d["role"],
                d["ip"],
                d["times"],
                _minutes(d["total"]),
                _minutes(d["longest"]),
                _utc_text(d["last"]),
                "yes" if d["down_now"] else "no",
            ]
            for d in ordered
        ],
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
