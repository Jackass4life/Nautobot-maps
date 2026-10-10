"""Availability per site, tenant and month (#287).

Availability = 1 − downtime ÷ the period (the month; up to now for the
current one, UTC).  Downtime, by ``AVAILABILITY_BASIS``:

* ``any_down``: the time any monitored device of the site was down, from
  the incident history (overlapping devices count once);
* ``critical``: the time the site was at Critical, from its level changes.

Time inside maintenance windows is not downtime (#303): a site window
removes it from the site, a device window from that device; a window ended
early counts until ``ended_at``, a cancelled one not at all.  A tenant's
availability is the average of its sites' (each site counts the same).
"""

import csv
import io
import re
from datetime import UTC, datetime, timedelta

from nautobot_maps import alerts, settings, timeutil

BASES = ("any_down", "critical")
MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")

Interval = tuple[datetime, datetime]


class ReportError(ValueError):
    """A request that can't become a report; ``message`` is safe to show."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def now() -> datetime:
    return timeutil.parse_iso_datetime(timeutil.iso_utc_now())


def month_bounds(month: str, at: datetime) -> tuple[datetime, datetime]:
    """``(start, end)`` of *month* ("2026-10"), the end not after *at*."""
    match = MONTH_PATTERN.match(month or "")
    if not match:
        raise ReportError("month must look like 2026-10")
    year, number = int(match.group(1)), int(match.group(2))
    try:
        start = datetime(year, number, 1, tzinfo=UTC)
        end = datetime(year + (number == 12), number % 12 + 1, 1, tzinfo=UTC)
    except ValueError:  # month 13, year 0, the month after 9999-12
        raise ReportError("month must look like 2026-10") from None
    if start >= at:
        raise ReportError("That month hasn't started yet")
    return start, min(end, at)


def current_month(at: datetime) -> str:
    return f"{at.year:04d}-{at.month:02d}"


# --- intervals ------------------------------------------------------------


def union(intervals: list[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subtract(intervals: list[Interval], holes: list[Interval]) -> list[Interval]:
    result = []
    holes = union(holes)
    for start, end in union(intervals):
        pieces = [(start, end)]
        for hole_start, hole_end in holes:
            next_pieces = []
            for piece_start, piece_end in pieces:
                if hole_end <= piece_start or hole_start >= piece_end:
                    next_pieces.append((piece_start, piece_end))
                    continue
                if hole_start > piece_start:
                    next_pieces.append((piece_start, hole_start))
                if hole_end < piece_end:
                    next_pieces.append((hole_end, piece_end))
            pieces = next_pieces
        result += pieces
    return result


def clip(intervals: list[Interval], start: datetime, end: datetime) -> list[Interval]:
    return [(max(s, start), min(e, end)) for s, e in intervals if e > start and s < end]


def seconds(intervals: list[Interval]) -> int:
    return int(sum((end - start).total_seconds() for start, end in union(intervals)))


# --- reading --------------------------------------------------------------


def _dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = timeutil.parse_iso_datetime(value) if value else None
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def read_maintenance(conn, start: datetime, end: datetime) -> tuple[dict, dict]:
    """``({site_id: [interval]}, {device_id: [interval]})`` of the windows in the period."""
    rows = conn.execute(
        "SELECT site_id, device_id, starts_at, ends_at, ended_at FROM maintenance_windows "
        "WHERE starts_at < %s AND ends_at > %s AND (ended_at IS NULL OR ended_at > starts_at)",
        (end, start),
    ).fetchall()
    sites: dict[str, list] = {}
    devices: dict[str, list] = {}
    for row in rows:
        window_start, window_end = _dt(row["starts_at"]), _dt(row["ends_at"])
        if row["ended_at"] is not None:
            window_end = min(window_end, _dt(row["ended_at"]))
        if window_end <= window_start:
            continue
        target = devices.setdefault(row["device_id"], []) if row["device_id"] else sites.setdefault(row["site_id"], [])
        target.append((window_start, window_end))
    return sites, devices


def read_incidents(conn, start: datetime, end: datetime, at: datetime) -> dict:
    """``{site_id: {"name", "incidents", "devices": {device_id: [interval]}}}`` of incidents
    down at any point in the period; an open one is down until *at*."""
    rows = conn.execute(
        "SELECT site_id, site_name, device_id, down_started_at, resolved_at FROM alert_instances "
        "WHERE down_started_at < %s AND (resolved_at IS NULL OR resolved_at > %s)",
        (end, start),
    ).fetchall()
    sites: dict[str, dict] = {}
    for row in rows:
        down = _dt(row["down_started_at"])
        up = _dt(row["resolved_at"]) or at
        if down is None or up <= down:
            continue
        site = sites.setdefault(row["site_id"], {"name": row["site_name"] or "", "incidents": 0, "devices": {}})
        site["incidents"] += 1
        site["devices"].setdefault(row["device_id"] or "", []).append((down, up))
    return sites


def read_critical(conn, start: datetime, end: datetime) -> dict:
    """``{site_id: {"name", "intervals"}}``: when each site was at Critical in the period.

    The level at the start is the one the last earlier change set; with no
    earlier change, the one the first (also later) change came from; with
    no change at all, the site's level since it was first seen
    (``site_alert_levels``: a site's first level is not a change).
    """
    rows = conn.execute(
        "SELECT site_id, site_name, from_level, to_level, changed_at FROM site_level_changes "
        "ORDER BY site_id, changed_at, id"
    ).fetchall()
    by_site: dict[str, list] = {}
    for row in rows:
        by_site.setdefault(row["site_id"], []).append(row)
    sites = {}
    for row in conn.execute("SELECT site_id, site_name, alert_level, updated_at FROM site_alert_levels").fetchall():
        if row["site_id"] in by_site or row["alert_level"] != "critical":
            continue
        # Critical since it was first seen, never changed.
        seen = max(_dt(row["updated_at"]) or start, start)
        if seen < end:
            sites[row["site_id"]] = {"name": row["site_name"] or "", "intervals": [(seen, end)]}
    for site_id, changes in by_site.items():
        before = [c for c in changes if _dt(c["changed_at"]) <= start]
        during = [c for c in changes if start < _dt(c["changed_at"]) < end]
        after = [c for c in changes if _dt(c["changed_at"]) >= end]
        if before:
            level = before[-1]["to_level"]
        else:
            level = (during or after)[0]["from_level"]
        intervals = []
        since = start
        for change in during:
            at = _dt(change["changed_at"])
            if level == "critical":
                intervals.append((since, at))
            level, since = change["to_level"], at
        if level == "critical":
            intervals.append((since, end))
        if intervals:
            sites[site_id] = {"name": changes[-1]["site_name"] or "", "intervals": intervals}
    return sites


# --- the report -----------------------------------------------------------


def report(conn, month: str | None = None, basis: str | None = None, sites: list | None = None) -> dict:
    """The availability of *month* (default: the current one).

    *sites*: the board's rows (``id``, ``name``, ``tenants``, ``device_count``),
    so a monitored site with no downtime is listed at 100 %; sites only in
    the history are added, sites with no monitored devices left out.
    """
    at = now()
    month = month or current_month(at)
    basis = basis or settings.AVAILABILITY_BASIS
    if basis not in BASES:
        raise ReportError(f"basis must be one of: {', '.join(BASES)}")
    start, end = month_bounds(month, at)
    if settings.ALERT_HISTORY_RETENTION_DAYS > 0:
        # Deleted history would read as no downtime: refuse rather than guess.
        kept_from = at - timedelta(days=settings.ALERT_HISTORY_RETENTION_DAYS)
        if start < kept_from:
            raise ReportError(
                f"History before {kept_from:%Y-%m-%d} is deleted (ALERT_HISTORY_RETENTION_DAYS="
                f"{settings.ALERT_HISTORY_RETENTION_DAYS}): {month} can't be reported in full"
            )
    period = int((end - start).total_seconds())
    site_windows, device_windows = read_maintenance(conn, start, end)

    rows: dict[str, dict] = {}
    for site in sites or []:
        # A site without monitored devices has no availability to speak of
        # (it is listed if its history has something in the month).
        monitored = site.get("device_count", 1) or site.get("maintenance_device_count")
        if site.get("id") and monitored:
            rows[site["id"]] = {
                "site_id": site["id"],
                "name": site.get("name") or "",
                "tenants": list(site.get("tenants") or ([site["tenant"]] if site.get("tenant") else [])),
                "downtime_seconds": 0,
                "incidents": 0,
            }

    if basis == "any_down":
        for site_id, data in read_incidents(conn, start, end, at).items():
            down = []
            for device_id, intervals in data["devices"].items():
                down += subtract(intervals, device_windows.get(device_id, []))
            down = subtract(clip(down, start, end), site_windows.get(site_id, []))
            row = rows.setdefault(
                site_id,
                {"site_id": site_id, "name": data["name"], "tenants": [], "downtime_seconds": 0, "incidents": 0},
            )
            row["downtime_seconds"] = seconds(down)
            row["incidents"] = data["incidents"]
    else:
        for site_id, data in read_critical(conn, start, end).items():
            down = subtract(clip(data["intervals"], start, end), site_windows.get(site_id, []))
            row = rows.setdefault(
                site_id,
                {"site_id": site_id, "name": data["name"], "tenants": [], "downtime_seconds": 0, "incidents": 0},
            )
            row["downtime_seconds"] = seconds(down)

    site_rows = []
    for row in rows.values():
        row["availability"] = round(100 * (1 - row["downtime_seconds"] / period), 3) if period else 100.0
        site_rows.append(row)
    site_rows.sort(key=lambda r: (r["availability"], (r["name"] or "").lower()))

    tenants: dict[str, list] = {}
    for row in site_rows:
        for tenant in row["tenants"]:
            tenants.setdefault(tenant, []).append(row)
    tenant_rows = sorted(
        (
            {
                "tenant": tenant,
                "sites": len(members),
                "availability": round(sum(r["availability"] for r in members) / len(members), 3),
                "downtime_seconds": sum(r["downtime_seconds"] for r in members),
            }
            for tenant, members in tenants.items()
        ),
        key=lambda r: (r["availability"], r["tenant"].lower()),
    )
    return {
        "month": month,
        "basis": basis,
        "period": {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "seconds": period,
            "complete": end < at,
        },
        "sites": site_rows,
        "tenants": tenant_rows,
    }


def _safe(value) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def to_csv(data: dict, view: str) -> str:
    out = io.StringIO()
    out.write("﻿")  # Excel reads UTF-8
    writer = csv.writer(out, lineterminator="\r\n")
    if view == "tenants":
        writer.writerow(["month", "basis", "tenant", "sites", "availability_pct", "downtime_min"])
        for row in data["tenants"]:
            writer.writerow(
                [
                    _safe(v)
                    for v in (
                        data["month"],
                        data["basis"],
                        row["tenant"],
                        row["sites"],
                        row["availability"],
                        round(row["downtime_seconds"] / 60),
                    )
                ]
            )
    else:
        writer.writerow(["month", "basis", "site", "tenants", "availability_pct", "downtime_min", "incidents"])
        for row in data["sites"]:
            writer.writerow(
                [
                    _safe(v)
                    for v in (
                        data["month"],
                        data["basis"],
                        row["name"] or row["site_id"],
                        "; ".join(row["tenants"]),
                        row["availability"],
                        round(row["downtime_seconds"] / 60),
                        row["incidents"],
                    )
                ]
            )
    return out.getvalue()


def board_sites() -> list[dict]:
    """The board's rows, for the site list and tenants; [] without a board."""
    try:
        return alerts.cached_alert_board().get("alerts") or []
    except Exception:
        return []
