"""A tenant's status page (#291): its sites and their state, nothing else.

Built from the cached alert board.  Only what a customer may see: the
tenant's own description from Nautobot (#482, shown to everyone by
decision), the site name, where it is (address and coordinates, #493,
also by decision), a plain state, and since (or until) when; locations
with nothing monitored are left out.  No devices, IPs,
cases, acknowledgements, maintenance reasons or other tenants (nor their
descriptions).
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


def site_address(row: dict) -> str:
    """A board row's street address, or its facility (#350)."""
    return row.get("physical_address") or row.get("facility") or ""


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


def _place(row: dict) -> dict:
    """Where a site is (#493): its address (as the board's, on one line) and
    its coordinates, each None when Nautobot has none or they're impossible."""
    address = ", ".join(line.strip() for line in site_address(row).splitlines() if line.strip())
    latitude, longitude = row.get("latitude"), row.get("longitude")
    # A real point only: coordinates.py flags the rest for the admins.
    located = latitude is not None and longitude is not None and abs(latitude) <= 90 and abs(longitude) <= 180
    return {
        "address": address or None,
        "latitude": latitude if located else None,
        "longitude": longitude if located else None,
    }


def tenant_status(board: dict, tenant: str) -> dict | None:
    """*tenant*'s sites on *board* (case-insensitive name); None when it has none."""
    wanted = tenant.strip().casefold()
    name = ""
    description = ""
    sites = []
    checked_at = board.get("checked_at") or ""
    for row in board.get("alerts") or []:
        match = next((t for t in site_tenants(row) if t.casefold() == wanted), None)
        if match is None:
            continue
        # Nothing monitored there (a region, a building), or no role matched this tenant.
        scoped = (row.get("tenant_alerts") or {}).get(match.casefold())
        if scoped is not None and not scoped.get("device_count"):
            continue
        if scoped is None and row.get("alert_level") == "no_data" and not row.get("device_count"):
            continue
        name = name or match
        # Only this tenant's, never a shared site's other tenants' (#482).
        description = description or next(
            (text for owner, text in (row.get("tenant_descriptions") or {}).items() if owner.casefold() == wanted), ""
        )
        state = STATES.get((scoped or {}).get("alert_level") or row.get("alert_level") or "", "unknown")
        site = {"name": row.get("name") or "", "state": state, "since": None, "until": None, **_place(row)}
        if state in ("outage", "degraded"):
            site["since"] = _since({**row, **(scoped or {})}, checked_at)
        elif state == "maintenance":
            site["until"] = (row.get("maintenance") or {}).get("until")
        sites.append(site)
    if not sites:
        return None
    sites.sort(key=lambda site: (STATE_ORDER.index(site["state"]), site["name"].lower()))
    return {
        "tenant": name,
        "description": description or None,
        "checked_at": checked_at,
        "summary": {
            "sites": len(sites),
            "affected": sum(1 for site in sites if site["state"] in ("outage", "degraded")),
        },
        "sites": sites,
    }
