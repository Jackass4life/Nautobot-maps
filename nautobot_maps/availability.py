"""Availability per site, tenant and period (#287, #341).

Availability = 1 − downtime ÷ the period (a month, or any days from–to;
up to now when it hasn't ended, UTC).  Downtime, by ``AVAILABILITY_BASIS``:

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

from nautobot_maps import alerts, inventory, settings, timeutil

BASES = ("any_down", "critical")
MONTH_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Longest from–to period (#341): a year, so a report stays a few quick queries.
MAX_PERIOD_DAYS = 366
# Below this a site or tenant is shown in red (#287) and counted as below target (#342).
TARGET_PCT = 99.9

Interval = tuple[datetime, datetime]


class ReportError(ValueError):
    """A request that can't become a report; ``message`` is safe to show."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def now() -> datetime:
    return timeutil.parse_iso_datetime(timeutil.iso_utc_now())


def _month_span(month: str) -> tuple[datetime, datetime]:
    """``(start, end)`` of the whole of *month* ("2026-10"), the end exclusive."""
    match = MONTH_PATTERN.match(month or "")
    if not match:
        raise ReportError("month must look like 2026-10")
    year, number = int(match.group(1)), int(match.group(2))
    try:
        start = datetime(year, number, 1, tzinfo=UTC)
        end = datetime(year + (number == 12), number % 12 + 1, 1, tzinfo=UTC)
    except ValueError:  # month 13, year 0, the month after 9999-12
        raise ReportError("month must look like 2026-10") from None
    return start, end


def _date(value: str, name: str) -> datetime:
    if not DATE_PATTERN.match(value or ""):
        raise ReportError(f"{name} must look like 2026-10-03")
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:  # 2026-02-30
        raise ReportError(f"{name} must be a real date, like 2026-10-03") from None


def resolve_period(month: str | None, from_date: str | None, to_date: str | None, at: datetime) -> dict:
    """The period asked for (#341): ``month`` ("2026-10"), or ``from`` and
    ``to`` ("2026-10-03", both included); default the current month.

    ``{start, end, from, to, month, label}``: *end* not after *at*; a from–to
    that is a whole calendar month is that month, so its CSV keeps the month.
    """
    if from_date or to_date:
        if month:
            raise ReportError("Give a month, or from and to, not both")
        if not (from_date and to_date):
            raise ReportError("Give both from and to")
        start = _date(from_date, "from")
        last = _date(to_date, "to")
    else:
        start, end = _month_span(month or current_month(at))
        last = end - timedelta(days=1)
    if last < start:
        raise ReportError("to must be on or after from")
    try:
        end = last + timedelta(days=1)
    except OverflowError:  # 9999-12-31
        raise ReportError("to must be a real date, like 2026-10-03") from None
    if (end - start).days > MAX_PERIOD_DAYS:
        raise ReportError(f"A period can be at most {MAX_PERIOD_DAYS} days")
    if start >= at:
        raise ReportError("That period hasn't started yet")
    first, final = f"{start:%Y-%m-%d}", f"{last:%Y-%m-%d}"
    whole_month = start.day == 1 and _month_span(first[:7])[1] == end
    month = first[:7] if whole_month else None
    return {
        "start": start,
        "end": min(end, at),
        "from": first,
        "to": final,
        "month": month,
        "label": month or (first if first == final else f"{first} to {final}"),
    }


def kept_from(at: datetime) -> datetime | None:
    """When the history kept starts (``ALERT_HISTORY_RETENTION_DAYS``), or None: all kept."""
    days = settings.ALERT_HISTORY_RETENTION_DAYS
    return at - timedelta(days=days) if days > 0 else None


def oldest_day(at: datetime) -> str:
    """The calendar's oldest day, "2024-10-15", or "" when nothing is deleted
    (#341): the day after the one the history starts in, so report() still
    accepts it a moment later, when the history has moved on."""
    start = kept_from(at)
    return f"{start + timedelta(days=1):%Y-%m-%d}" if start else ""


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
        # The same expression as idx_alert_instances_period.
        "WHERE COALESCE(resolved_at, 'infinity'::timestamptz) > %s AND down_started_at < %s",
        (start, end),
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
    # Only what the period needs: the changes in it, and per site the last
    # one before it and the first one after it (#287), not the whole history.
    columns = "site_id, site_name, from_level, to_level, changed_at, id"
    rows = conn.execute(
        f"""
        (SELECT {columns} FROM site_level_changes WHERE changed_at > %(start)s AND changed_at < %(end)s)
        UNION ALL
        (SELECT DISTINCT ON (site_id) {columns} FROM site_level_changes
          WHERE changed_at <= %(start)s ORDER BY site_id, changed_at DESC, id DESC)
        UNION ALL
        (SELECT DISTINCT ON (site_id) {columns} FROM site_level_changes
          WHERE changed_at >= %(end)s ORDER BY site_id, changed_at, id)
        ORDER BY site_id, changed_at, id
        """,
        {"start": start, "end": end},
    ).fetchall()
    by_site: dict[str, list] = {}
    for row in rows:
        by_site.setdefault(row["site_id"], []).append(row)
    sites = {}
    first_seen = {}
    for row in conn.execute(
        "SELECT site_id, site_name, alert_level, updated_at, first_seen_at FROM site_alert_levels"
    ).fetchall():
        first_seen[row["site_id"]] = _dt(row["first_seen_at"])
        if row["site_id"] in by_site or row["alert_level"] != "critical":
            continue
        # Critical since it was first seen, never changed.
        seen = max(_dt(row["first_seen_at"]) or _dt(row["updated_at"]) or start, start)
        if seen < end:
            sites[row["site_id"]] = {"name": row["site_name"] or "", "intervals": [(seen, end)]}
    for site_id, changes in by_site.items():
        before = [c for c in changes if _dt(c["changed_at"]) <= start]
        during = [c for c in changes if start < _dt(c["changed_at"]) < end]
        after = [c for c in changes if _dt(c["changed_at"]) >= end]
        intervals = []
        since = start
        if before:
            level = before[-1]["to_level"]
        else:
            # The level the first change came from, since the site was first
            # seen (not before it existed here).
            level = (during or after)[0]["from_level"]
            if first_seen.get(site_id) and first_seen[site_id] > start:
                since = min(first_seen[site_id], end)
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


def report(
    conn,
    month: str | None = None,
    basis: str | None = None,
    sites: list | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """The availability of *month*, or of the days *from_date* to *to_date*
    (#341); default: the current month.

    *sites*: the board's rows (``id``, ``name``, ``tenants``, ``device_count``),
    so a monitored site with no downtime is listed at 100 %; sites only in
    the history are added, sites with no monitored devices left out.
    """
    at = now()
    basis = basis or settings.AVAILABILITY_BASIS
    if basis not in BASES:
        raise ReportError(f"basis must be one of: {', '.join(BASES)}")
    asked = resolve_period(month, from_date, to_date, at)
    start, end = asked["start"], asked["end"]
    history_start = kept_from(at)
    if history_start and start < history_start:
        # Deleted history would read as no downtime: refuse rather than guess.
        raise ReportError(
            f"History before {history_start:%Y-%m-%d} is deleted (ALERT_HISTORY_RETENTION_DAYS="
            f"{settings.ALERT_HISTORY_RETENTION_DAYS}): {asked['label']} can't be reported in full"
        )
    period = int((end - start).total_seconds())
    site_windows, device_windows = read_maintenance(conn, start, end)

    rows: dict[str, dict] = {}
    # Every board site's name and tenants, also for one listed only because of its history.
    known: dict[str, dict] = {}
    for site in sites or []:
        if not site.get("id"):
            continue
        known[site["id"]] = {
            "site_id": site["id"],
            "name": site.get("name") or "",
            "tenants": list(site.get("tenants") or ([site["tenant"]] if site.get("tenant") else [])),
            "downtime_seconds": 0,
            "incidents": 0,
        }
        # A site without monitored devices has no availability to speak of
        # (it is listed if its history has something in the month).
        if site.get("device_count", 1) or site.get("maintenance_device_count"):
            rows[site["id"]] = known[site["id"]]

    # The incidents count, and list a site only in the history, whatever the basis.
    incidents = read_incidents(conn, start, end, at)
    for site_id, data in incidents.items():
        row = rows.setdefault(
            site_id,
            known.get(site_id)
            or {"site_id": site_id, "name": data["name"], "tenants": [], "downtime_seconds": 0, "incidents": 0},
        )
        row["incidents"] = data["incidents"]
    if basis == "any_down":
        for site_id, data in incidents.items():
            down = []
            for device_id, intervals in data["devices"].items():
                down += subtract(intervals, device_windows.get(device_id, []))
            down = subtract(clip(down, start, end), site_windows.get(site_id, []))
            rows[site_id]["downtime_seconds"] = seconds(down)
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
    # The descriptions of the tenants listed, for their (i) (#342).
    descriptions: dict[str, str] = {}
    for site in sites or []:
        descriptions.update(site.get("tenant_descriptions") or {})
    return {
        "month": asked["month"],
        "basis": basis,
        "period": {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "seconds": period,
            "complete": end < at,
            # The days asked for, both included (*end* may be cut at now).
            "from": asked["from"],
            "to": asked["to"],
            "label": asked["label"],
        },
        "summary": summarise(site_rows, tenant_rows),
        "sites": site_rows,
        "tenants": tenant_rows,
        "tenant_descriptions": {name: descriptions[name] for name in tenants if descriptions.get(name)},
    }


def summarise(site_rows: list[dict], tenant_rows: list[dict]) -> dict:
    """The page's cards (#342): every site counts the same, as for a tenant."""
    count = len(site_rows)
    # No tenant had downtime: no "worst" to name.
    worst = tenant_rows[0] if tenant_rows and tenant_rows[0]["downtime_seconds"] else None
    return {
        "sites": count,
        "availability": round(sum(r["availability"] for r in site_rows) / count, 3) if count else 100.0,
        "downtime_seconds": sum(r["downtime_seconds"] for r in site_rows),
        "incidents": sum(r["incidents"] for r in site_rows),
        "below_target": sum(1 for r in site_rows if r["availability"] < TARGET_PCT),
        "no_downtime": sum(1 for r in site_rows if not r["downtime_seconds"]),
        "target": TARGET_PCT,
        "worst_tenant": {"tenant": worst["tenant"], "availability": worst["availability"]} if worst else None,
    }


def format_downtime(seconds: int) -> str:
    """ "0 min", "14 min", "2 h 14 min", "3 d 4 h" (#342): minutes are rounded."""
    minutes = round((seconds or 0) / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if hours else f"{days} d"


def _safe(value) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def to_csv(data: dict, view: str) -> str:
    out = io.StringIO()
    out.write("\ufeff")  # Excel reads UTF-8
    writer = csv.writer(out, lineterminator="\r\n")
    if view == "tenants":
        columns = ["tenant", "sites", "availability_pct", "downtime_min"]
        rows = [
            (r["tenant"], r["sites"], r["availability"], round(r["downtime_seconds"] / 60)) for r in data["tenants"]
        ]
    else:
        columns = ["site", "tenants", "availability_pct", "downtime_min", "incidents"]
        rows = [
            (
                r["name"] or r["site_id"],
                "; ".join(r["tenants"]),
                r["availability"],
                round(r["downtime_seconds"] / 60),
                r["incidents"],
            )
            for r in data["sites"]
        ]
    # period_from and period_to (#341) last, so existing spreadsheets keep their columns.
    writer.writerow(["month", "basis", *columns, "period_from", "period_to"])
    before = (data["month"], data["basis"])
    after = (data["period"]["from"], data["period"]["to"])
    for row in rows:
        writer.writerow([_safe(v) for v in (*before, *row, *after)])
    return out.getvalue()


def board_sites() -> list[dict]:
    """The board's rows, for the site list and tenants.  Raises when the board
    can't be read: a report without them would miss sites and tenants.  So
    is an empty board before the first inventory sync has finished."""
    rows = alerts.cached_alert_board().get("alerts") or []
    if not rows and not inventory.snapshot_initialized():
        raise RuntimeError("the inventory snapshot is not ready yet")
    return rows
