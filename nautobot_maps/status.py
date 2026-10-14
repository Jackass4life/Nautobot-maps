"""A tenant's status page (#291): its sites and their state, nothing else.

Built from the cached alert board.  Only what a customer may see: the site
name, a plain state, and since (or until) when; locations with nothing
monitored are left out.  No devices, addresses, IPs,
cases, acknowledgements, maintenance reasons or other tenants.
"""

from datetime import timedelta

from nautobot_maps import timeutil

# Board level -> what the page says.
STATES = {
    "ok": "operational",
    "low": "degraded",
    "medium": "degraded",
    "critical": "outage",
    "maintenance": "maintenance",
    "no_data": "unknown",
}
# Affected sites first.
STATE_ORDER = ("outage", "degraded", "maintenance", "unknown", "operational")


def site_path(row: dict) -> str:
    """Where a board row is: the path above it ("EMEA › DNK"), or its parent."""
    return row.get("ancestor_path") or row.get("parent") or ""


def site_tenants(row: dict) -> list[str]:
    """Every tenant of a board row: its own and those linked by a Relationship (#238)."""
    if row.get("tenants"):
        return list(row["tenants"])
    return [row["tenant"]] if row.get("tenant") else []


def _since(row: dict, checked_at: str) -> str | None:
    """When the site's current trouble started, from its longest current downtime."""
    seconds = int(row.get("current_downtime_seconds") or 0)
    checked = timeutil.parse_iso_datetime(checked_at) if checked_at else None
    if seconds <= 0 or checked is None:
        return None
    return (checked - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def tenant_status(board: dict, tenant: str) -> dict | None:
    """*tenant*'s sites on *board* (case-insensitive name); None when it has none."""
    wanted = tenant.strip().casefold()
    name = ""
    sites = []
    checked_at = board.get("checked_at") or ""
    for row in board.get("alerts") or []:
        match = next((t for t in site_tenants(row) if t.casefold() == wanted), None)
        if match is None:
            continue
        # Nothing monitored there (a region, a building): nothing to report.
        if row.get("alert_level") == "no_data" and not row.get("device_count"):
            continue
        name = name or match
        state = STATES.get(row.get("alert_level") or "", "unknown")
        site = {"name": row.get("name") or "", "state": state, "since": None, "until": None}
        if state in ("outage", "degraded"):
            site["since"] = _since(row, checked_at)
        elif state == "maintenance":
            site["until"] = (row.get("maintenance") or {}).get("until")
        sites.append(site)
    if not sites:
        return None
    sites.sort(key=lambda site: (STATE_ORDER.index(site["state"]), site["name"].lower()))
    return {
        "tenant": name,
        "checked_at": checked_at,
        "summary": {
            "sites": len(sites),
            "affected": sum(1 for site in sites if site["state"] in ("outage", "degraded")),
        },
        "sites": sites,
    }
