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

import re
from datetime import UTC, datetime, timedelta

from nautobot_maps import alerts, db, export, inventory, settings, status, timeutil

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


def resolve_period(month: str | None, from_date: str | None, to_date: str | None, at: datetime, zone=UTC) -> dict:
    """The period asked for (#341): ``month`` ("2026-10"), or ``from`` and
    ``to`` ("2026-10-03", both included); default the current month.

    ``{start, end, until, from, to, month, label}``: the days are *zone*'s
    (#353: the browser's for the maintenance history; UTC for the report);
    *until* is the end of the last day, *end* that but not after *at*.  A
    from–to that is a whole calendar month is that month, so its CSV keeps it.
    """
    if from_date or to_date:
        if month:
            raise ReportError("Give a month, or from and to, not both")
        if not (from_date and to_date):
            raise ReportError("Give both from and to")
        start = _date(from_date, "from")
        last = _date(to_date, "to")
    else:
        start, end = _month_span(month or current_month(at.astimezone(zone)))
        last = end - timedelta(days=1)
    if last < start:
        raise ReportError("to must be on or after from")
    try:
        end = last + timedelta(days=1)
    except OverflowError:  # 9999-12-31
        raise ReportError("to must be a real date, like 2026-10-03") from None
    if (end - start).days > MAX_PERIOD_DAYS:
        raise ReportError(f"A period can be at most {MAX_PERIOD_DAYS} days")
    first, final = f"{start:%Y-%m-%d}", f"{last:%Y-%m-%d}"
    whole_month = start.day == 1 and _month_span(first[:7])[1] == end
    # The same days, from midnight in *zone* (the dates above are worked out in UTC).
    start, end = start.replace(tzinfo=zone), end.replace(tzinfo=zone)
    if start >= at:
        raise ReportError("That period hasn't started yet")
    month = first[:7] if whole_month else None
    return {
        "start": start,
        "end": min(end, at),
        "until": end,
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
        window_start, window_end = timeutil.as_utc_datetime(row["starts_at"]), timeutil.as_utc_datetime(row["ends_at"])
        if row["ended_at"] is not None:
            window_end = min(window_end, timeutil.as_utc_datetime(row["ended_at"]))
        if window_end <= window_start:
            continue
        target = devices.setdefault(row["device_id"], []) if row["device_id"] else sites.setdefault(row["site_id"], [])
        target.append((window_start, window_end))
    return sites, devices


# Incidents down at any point in a period: the same expression as idx_alert_instances_period.
INCIDENTS_IN_PERIOD = "COALESCE(i.resolved_at, 'infinity'::timestamptz) > %s AND i.down_started_at < %s"


def _down_span(row, at: datetime) -> Interval | None:
    """An incident's down time, until *at* while it is open; None when it has none.
    The one rule for the report's figures and its incidents export (#354)."""
    down = timeutil.as_utc_datetime(row["down_started_at"])
    up = timeutil.as_utc_datetime(row["resolved_at"]) or at
    return None if down is None or up <= down else (down, up)


def read_incidents(conn, start: datetime, end: datetime, at: datetime) -> dict:
    """``{site_id: {"name", "incidents", "devices": {device_id: [interval]}}}`` of incidents
    down at any point in the period; an open one is down until *at*."""
    rows = conn.execute(
        "SELECT i.site_id, i.site_name, i.device_id, i.down_started_at, i.resolved_at "
        f"FROM alert_instances i WHERE {INCIDENTS_IN_PERIOD}",
        (start, end),
    ).fetchall()
    sites: dict[str, dict] = {}
    for row in rows:
        span = _down_span(row, at)
        if span is None:
            continue
        down, up = span
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
        # Each change's time parsed once, not in every filter below.
        by_site.setdefault(row["site_id"], []).append({**row, "at": timeutil.as_utc_datetime(row["changed_at"])})
    sites = {}
    first_seen = {}
    for row in conn.execute(
        "SELECT site_id, site_name, alert_level, updated_at, first_seen_at FROM site_alert_levels"
    ).fetchall():
        first_seen[row["site_id"]] = timeutil.as_utc_datetime(row["first_seen_at"])
        if row["site_id"] in by_site or row["alert_level"] != "critical":
            continue
        # Critical since it was first seen, never changed.
        seen = max(first_seen[row["site_id"]] or timeutil.as_utc_datetime(row["updated_at"]) or start, start)
        if seen < end:
            sites[row["site_id"]] = {"name": row["site_name"] or "", "intervals": [(seen, end)]}
    for site_id, changes in by_site.items():
        before = [c for c in changes if c["at"] <= start]
        during = [c for c in changes if start < c["at"] < end]
        after = [c for c in changes if c["at"] >= end]
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
            at = change["at"]
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
    with_incidents: bool = False,
) -> dict:
    """The availability of *month*, or of the days *from_date* to *to_date*
    (#341); default: the current month.  *with_incidents* (#354): also every
    device incident of the period, worked out with the same windows and time,
    so the incidents export adds up to the figures.

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
    # Every board site's name, tenants and details, also for one listed only because of its history.
    known: dict[str, dict] = {}
    for site in sites or []:
        if not site.get("id"):
            continue
        known[site["id"]] = _site_row(site["id"], site.get("name") or "", site)
        # A site without monitored devices has no availability to speak of
        # (it is listed if its history has something in the month).
        if site.get("device_count", 1) or site.get("maintenance_device_count"):
            rows[site["id"]] = known[site["id"]]

    # The incidents count, and list a site only in the history, whatever the basis.
    site_incidents = read_incidents(conn, start, end, at)
    for site_id, data in site_incidents.items():
        row = rows.setdefault(site_id, known.get(site_id) or _site_row(site_id, data["name"]))
        row["incidents"] = data["incidents"]
    if basis == "any_down":
        for site_id, data in site_incidents.items():
            down = []
            for device_id, intervals in data["devices"].items():
                down += subtract(intervals, device_windows.get(device_id, []))
            down = subtract(clip(down, start, end), site_windows.get(site_id, []))
            _set_downtime(rows[site_id], down, end)
    else:
        for site_id, data in read_critical(conn, start, end).items():
            down = subtract(clip(data["intervals"], start, end), site_windows.get(site_id, []))
            row = rows.setdefault(site_id, known.get(site_id) or _site_row(site_id, data["name"]))
            _set_downtime(row, down, end)

    site_rows = []
    for site_id, row in rows.items():
        # Time in a whole-site window: not downtime, shown on its own (#354).
        row["maintenance_seconds"] = seconds(clip(site_windows.get(site_id, []), start, end))
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
    descriptions = status.tenant_descriptions(sites or [])
    if with_incidents and _incidents_exceed(conn, start, end, INCIDENT_EXPORT_LIMIT):
        raise ReportError(f"More than {INCIDENT_EXPORT_LIMIT:,} incidents: choose a shorter period")
    result = {
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
        "tenant_descriptions": {name: descriptions[name] for name in tenants if name in descriptions},
    }
    if with_incidents:
        # Streamed when written (*conn* must stay open until then), with the report's
        # own time, windows and sites, so the export adds up to the figures.
        result["incidents"] = _incident_stream(conn, start, end, at, site_windows, device_windows, rows)
    return result


def _site_row(site_id: str, name: str = "", site: dict | None = None) -> dict:
    """A site's report row: who and where it is (#354: the board's details), and no downtime yet."""
    site = site or {}
    return {
        "site_id": site_id,
        "name": name,
        "tenants": status.site_tenants(site),
        "path": status.site_path(site),
        "address": status.site_address(site),
        "country": site.get("country") or "",
        "tier": site.get("tier") or "",
        "monitored_devices": site.get("device_count") or 0,
        # How much is there (#499): counts only, the roles left to Nautobot.
        "network_devices": site.get("network_device_count") or 0,
        "access_points": site.get("access_point_count") or 0,
        "downtime_seconds": 0,
        "incidents": 0,
        "outages": 0,
        "longest_outage_seconds": 0,
        "mean_outage_seconds": 0,
        "first_outage": None,
        "last_outage": None,
    }


def _set_downtime(row: dict, down: list[Interval], end: datetime) -> None:
    """*row*'s downtime and its outages (#354): the down time after maintenance,
    merged, so devices down together are one outage.  First and last are when
    they began.  The mean time to restore counts only outages restored in the
    period (*end*: its end, or now): one still down has no restore time yet."""
    outages = union(down)
    row["downtime_seconds"] = seconds(outages)
    row["outages"] = len(outages)
    if outages:
        lengths = [int((stop - begin).total_seconds()) for begin, stop in outages]
        restored = [length for (_, stop), length in zip(outages, lengths, strict=True) if stop < end]
        row["longest_outage_seconds"] = max(lengths)
        row["mean_outage_seconds"] = round(sum(restored) / len(restored)) if restored else None
        row["first_outage"] = db.serialize_value(outages[0][0])
        row["last_outage"] = db.serialize_value(outages[-1][0])


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
        "worst_tenant": worst,
    }


def format_downtime(seconds: int) -> str:
    """As the board shows durations (lib/format.js formatDuration, #311): "—", "14m",
    "2h 14m", and in days from two days ("3 days")."""
    total = int(seconds or 0)
    if total <= 0:
        return "—"
    hours, minutes = total // 3600, total % 3600 // 60
    if hours >= 48:
        return f"{hours // 24} days"
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


# The most incidents one export holds (#354); past that it asks for a shorter period.
INCIDENT_EXPORT_LIMIT = 200_000


def _incidents_exceed(conn, start: datetime, end: datetime, limit: int) -> bool:
    """Whether the period has more than *limit* incidents: one index probe, before streaming."""
    row = conn.execute(
        f"SELECT 1 FROM alert_instances i WHERE {INCIDENTS_IN_PERIOD} OFFSET %s LIMIT 1", (start, end, limit)
    ).fetchone()
    return row is not None


def _incident_stream(conn, start, end, at, site_windows, device_windows, site_rows):
    """Every device incident in the period (#354), oldest first, a row (dict by
    INCIDENT_COLUMNS) at a time from a server-side cursor: when it was down
    and up, the minutes in the period and those counted after maintenance,
    the cases and the likely cause.  Per site and per device the work is done
    once (*site_rows*: the report's, with name, path and tenants)."""
    sites: dict[str, tuple] = {}
    holes: dict[tuple, list] = {}
    with conn.transaction(), conn.cursor(name="availability_incidents_export") as cursor:
        cursor.itersize = 2000
        cursor.execute(
            "SELECT i.site_id, i.site_name, i.device_id, i.device_name, i.down_started_at, i.resolved_at, "
            f"i.probable_cause, {export.CASES_SQL} AS cases "
            f"FROM alert_instances i WHERE {INCIDENTS_IN_PERIOD} ORDER BY i.down_started_at, i.id",
            (start, end),
        )
        for row in cursor:
            span = _down_span(row, at)
            if span is None:
                continue
            site_id, device_id = row["site_id"], row["device_id"] or ""
            if site_id not in sites:
                site = site_rows.get(site_id) or {}
                sites[site_id] = (
                    site.get("name") or row["site_name"] or site_id,
                    site.get("path", ""),
                    "; ".join(site.get("tenants", [])),
                )
            key = (site_id, device_id)
            if key not in holes:
                holes[key] = union(device_windows.get(device_id, []) + site_windows.get(site_id, []))
            in_period = clip([span], start, end)
            total, kept = seconds(in_period), seconds(subtract(in_period, holes[key]))
            name, path, tenants = sites[site_id]
            yield {
                "site": name,
                "site_path": path,
                "tenants": tenants,
                "device": row["device_name"] or device_id,
                "down_at_utc": export.utc_text(row["down_started_at"]),
                "up_at_utc": export.utc_text(row["resolved_at"]),
                "minutes_in_period": export.minutes(total),
                "minutes_counted": export.minutes(kept),
                "maintenance": "none" if kept == total else ("full" if not kept else "partly"),
                "cases": row["cases"] or "",
                "probable_cause": row["probable_cause"] or "",
                "site_id": site_id,
                "device_id": device_id,
            }


# The columns of each export view; period_from and period_to (#341) and the
# details (#354) come after the first ones, so existing spreadsheets keep theirs.
SITE_DETAIL_COLUMNS = (
    "site_path",
    "address",
    "country",
    "tier",
    "monitored_devices",
    "outages",
    "longest_outage_min",
    "mean_time_to_restore_min",
    "first_outage_utc",
    "last_outage_utc",
    "maintenance_min",
    "site_id",
    # Last (#499), so existing spreadsheets keep their columns.
    "network_devices",
    "access_points",
)
INCIDENT_COLUMNS = (
    "site",
    "site_path",
    "tenants",
    "device",
    "down_at_utc",
    "up_at_utc",
    "minutes_in_period",
    "minutes_counted",
    "maintenance",
    "cases",
    "probable_cause",
    "site_id",
    "device_id",
)


def header_lines(data: dict, generated_by: str = "") -> list[tuple]:
    """The report's header (#354): what it covers and its totals, as name, value
    rows, then a blank line before the table."""
    summary, period = data["summary"], data["period"]
    worst = summary.get("worst_tenant") or {}
    return [
        ("report", "Nautobot Maps availability"),
        ("period", f"{period['from']} to {period['to']} (UTC){'' if period['complete'] else ', so far'}"),
        ("basis", data["basis"]),
        ("target_pct", summary["target"]),
        ("generated_at_utc", export.utc_text(now())),
        ("generated_by", generated_by),
        ("sites", summary["sites"]),
        ("availability_pct", summary["availability"]),
        ("downtime_min", export.minutes(summary["downtime_seconds"])),
        ("incidents", summary["incidents"]),
        ("sites_below_target", summary["below_target"]),
        ("sites_with_no_downtime", summary["no_downtime"]),
        ("worst_tenant", f"{worst['tenant']} ({worst['availability']} %)" if worst else ""),
        ("maintenance", "not counted as downtime"),
        (),
    ]


def _view(data: dict, view: str) -> tuple[tuple, object]:
    """``(columns, rows)`` of one export view: ``sites``, ``tenants`` or
    ``incidents`` (from ``report(with_incidents=True)``, streamed)."""
    if view == "incidents":
        return INCIDENT_COLUMNS, (tuple(r[c] for c in INCIDENT_COLUMNS) for r in data["incidents"])
    before = (data["month"] or "", data["basis"])
    after = (data["period"]["from"], data["period"]["to"])
    if view == "tenants":
        columns = ("month", "basis", "tenant", "sites", "availability_pct", "downtime_min", "period_from", "period_to")
        rows = (
            (*before, r["tenant"], r["sites"], r["availability"], export.minutes(r["downtime_seconds"]), *after)
            for r in data["tenants"]
        )
        return columns, rows
    columns = (
        "month",
        "basis",
        "site",
        "tenants",
        "availability_pct",
        "downtime_min",
        "incidents",
        "period_from",
        "period_to",
        *SITE_DETAIL_COLUMNS,
    )
    rows = (
        (
            *before,
            r["name"] or r["site_id"],
            "; ".join(r["tenants"]),
            r["availability"],
            export.minutes(r["downtime_seconds"]),
            r["incidents"],
            *after,
            r["path"],
            r["address"],
            r["country"],
            r["tier"],
            r["monitored_devices"],
            r["outages"],
            export.minutes(r["longest_outage_seconds"]),
            # Empty when no outage was restored in the period: none has a restore time.
            "" if r["mean_outage_seconds"] is None else export.minutes(r["mean_outage_seconds"]),
            export.utc_text(r["first_outage"]),
            export.utc_text(r["last_outage"]),
            export.minutes(r["maintenance_seconds"]),
            r["site_id"],
            r["network_devices"],
            r["access_points"],
        )
        for r in data["sites"]
    )
    return columns, rows


def csv_lines(data: dict, view: str, header: bool = False, generated_by: str = ""):
    """The report as CSV lines, to stream (formula-safe): one view (``_view``),
    with the report's header first when *header* (#354)."""
    columns, rows = _view(data, view)
    lines = [*(header_lines(data, generated_by) if header else []), columns]
    yield export.csv_header(lines[0])
    for line in lines[1:]:
        yield export.csv_line(line)
    for row in rows:
        yield export.csv_line(row)


def board_sites() -> list[dict]:
    """The board's rows, for the site list and tenants.  Raises when the board
    can't be read: a report without them would miss sites and tenants.  So
    is an empty board before the first inventory sync has finished."""
    rows = alerts.cached_alert_board().get("alerts") or []
    if not rows and not inventory.snapshot_initialized():
        raise RuntimeError("the inventory snapshot is not ready yet")
    return rows
