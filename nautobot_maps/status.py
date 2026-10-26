"""A tenant's status page (#291): its sites and their state, nothing else.

Built from the cached alert board.  Only what a customer may see: the
tenant's own description from Nautobot (#482, shown to everyone by
decision), the site name, where it is (address and coordinates, #493,
also by decision), a plain state, and since (or until) when, and how many
network devices and access points it has and how many are down (#503, counts
only); locations with nothing monitored are left out.  No device names, IPs, roles,
cases, acknowledgements, maintenance reasons or other tenants (nor their
descriptions).
"""

from datetime import timedelta

from nautobot_maps import inventory, timeutil

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


def tenant_descriptions(rows: list[dict]) -> dict[str, str]:
    """Every tenant's description, from board rows that each carry their own tenants' (#497)."""
    return {name: text for row in rows for name, text in (row.get("tenant_descriptions") or {}).items() if text}


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


def _devices(counts: dict) -> dict:
    """How many devices of each kind, and down (#503): from the site's board
    row, or a tenant's own on a shared site (never the others')."""
    return {
        "network_devices": int(counts.get("network_device_count") or 0),
        "access_points": int(counts.get("access_point_count") or 0),
        "network_devices_down": int(counts.get("down_network_device_count") or 0),
        "access_points_down": int(counts.get("down_access_point_count") or 0),
    }


def site_circuits(rows: list[dict], circuits: list[dict], parents: dict[str, str]) -> dict[str, list[dict]]:
    """``{row id: its circuits}`` for the tenant's site rows (#511): circuit ID,
    type and bandwidth, never the provider.  In one pass, whatever the number
    of sites and circuits.

    A circuit that ends at a site, or at a building, floor or room below it
    (*parents*: ``{location_id: parent_id}``), is the site's.  One without a
    termination whose description is a site's address (#383) is that site's;
    a building at the same address below it is the same site, not another."""
    row_ids = {row.get("id") or "" for row in rows} - {""}

    def row_of(location_id: str, skip_self: bool = False) -> str | None:
        current = parents.get(location_id, "") if skip_self else location_id
        for _ in range(32):
            if not current:
                return None
            if current in row_ids:
                return current
            current = parents.get(current, "")
        return None

    keys = {row.get("id") or "": inventory.address_key(row.get("physical_address") or "") for row in rows}
    by_key: dict[str, set] = {}  # address key: the rows its circuits go to (two sites at one address: both)
    for row_id, key in keys.items():
        if key and keys.get(row_of(row_id, skip_self=True) or "") != key:
            by_key.setdefault(key, set()).add(row_id)
    placed: dict[str, list[dict]] = {}
    for circuit in circuits:
        if circuit.get("terminated"):
            where = {row_of(location_id) for location_id in circuit.get("location_ids") or []} - {None}
        else:
            where = by_key.get(circuit.get("address_key") or "", set())
        for row_id in sorted(where):
            placed.setdefault(row_id, []).append(
                {
                    "cid": inventory.circuit_cid(circuit),
                    "type": circuit.get("circuit_type") or "",
                    "bandwidth": inventory.circuit_speed(circuit.get("commit_rate")),
                }
            )
    return placed


def tenant_status(
    board: dict, tenant: str, circuits: list[dict] | None = None, parents: dict[str, str] | None = None
) -> dict | None:
    """*tenant*'s sites on *board* (case-insensitive name); None when it has none.
    *circuits*: the tenant's active circuits (``inventory.read_tenant_circuits``),
    *parents* the locations above where they end (``inventory.read_location_parents``)."""
    wanted = tenant.strip().casefold()
    name = ""
    description = ""
    sites = []
    rows = []
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
        if (scoped if scoped is not None else row).get("wireless_only"):
            # Only access points down (#521): operational, with them counted, as before.
            state = "operational"
        site = {
            "name": row.get("name") or "",
            "state": state,
            "since": None,
            "until": None,
            **_place(row),
            **_devices(scoped if scoped is not None else row),
            "circuits": [],
        }
        if state in ("outage", "degraded"):
            site["since"] = _since({**row, **(scoped or {})}, checked_at)
        elif state == "maintenance":
            site["until"] = (row.get("maintenance") or {}).get("until")
        sites.append(site)
        rows.append(row)
    if not sites:
        return None
    placed = site_circuits(rows, circuits or [], parents or {})
    for row, site in zip(rows, sites, strict=True):
        site["circuits"] = placed.get(row.get("id") or "", [])
    sites.sort(key=lambda site: (STATE_ORDER.index(site["state"]), site["name"].lower()))
    return {
        "tenant": name,
        "description": description or None,
        "checked_at": checked_at,
        "summary": {
            "sites": len(sites),
            "affected": sum(1 for site in sites if site["state"] in ("outage", "degraded")),
            # What the sites' lines add up to: not in maintenance or without
            # data; an operational site's down access points too (#508).
            "devices_down": sum(
                site["network_devices_down"] + site["access_points_down"]
                for site in sites
                if site["state"] not in ("maintenance", "unknown")
            ),
        },
        "sites": sites,
    }
