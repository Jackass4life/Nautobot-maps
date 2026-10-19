"""Circuit mapping (#385): the circuits without a termination at a location,
and the site their description's address points to.

The worklist for adding terminations in Nautobot: a circuit leaves it at the
next sync after it gets one.  Read from the stored circuits and locations
(#383); nothing is asked of Nautobot.
"""

from nautobot_maps import alerts, db, export, inventory

# Worst first: a choice to make, then nothing found, then ready to terminate.
GROUPS = ("several", "none", "one")
GROUP_LABELS = {"several": "Several sites", "none": "No site", "one": "One site"}


# Retired: not worth a termination, so not on the worklist.
RETIRED_STATUSES = {"decommissioned"}


def mapping(conn) -> dict:
    """``{"summary": {total, several, none, one}, "circuits": [...]}``.

    Each circuit: ``alerts.cached_circuit``'s fields, ``group`` and ``sites``
    (``[{id, name, on_map}]``, by name); by group, then cid.  Decommissioned
    circuits are left out.  Two queries, however many circuits.
    """
    columns = ", ".join(inventory.CIRCUIT_COLUMNS)
    rows = conn.execute(f"SELECT {columns} FROM nautobot_circuit_cache WHERE NOT terminated").fetchall()
    circuits = [
        circuit
        for circuit in map(db.row_to_dict, rows)
        if (circuit.get("status") or "").strip().lower() not in RETIRED_STATUSES
    ]
    keys = sorted({circuit["address_key"] for circuit in circuits if circuit["address_key"]})
    rows = conn.execute(
        "SELECT location_id, name, parent_id, address_key, latitude, longitude FROM nautobot_location_cache "
        f"WHERE address_key = ANY({db.placeholders(1)})",
        (keys,),
    ).fetchall()
    locations: dict[str, dict] = {}
    at_address: dict[str, dict[str, str]] = {}
    for loc in map(db.row_to_dict, rows):
        locations[loc["location_id"]] = loc
        at_address.setdefault(loc["address_key"], {})[loc["location_id"]] = loc.get("parent_id") or ""
    sites_by_key = {key: _sites(parents, locations) for key, parents in at_address.items()}
    result = []
    for circuit in circuits:
        sites = sites_by_key.get(circuit["address_key"], [])
        group = "one" if len(sites) == 1 else "several" if sites else "none"
        result.append({**alerts.cached_circuit(circuit), "group": group, "sites": sites})
    result.sort(key=lambda circuit: (GROUPS.index(circuit["group"]), circuit["cid"].lower(), circuit["id"]))
    summary = {group: sum(circuit["group"] == group for circuit in result) for group in GROUPS}
    return {"summary": {"total": len(result), **summary}, "circuits": result}


def _sites(parents: dict[str, str], locations: dict[str, dict]) -> list[dict]:
    """The sites at one address, by name: each the highest location of its
    family there, linked through the one that is on the map (has coordinates):
    itself, else a building of it at the address."""
    families: dict[str, list[str]] = {}
    for loc_id in sorted(parents):
        families.setdefault(inventory.address_site(loc_id, parents), []).append(loc_id)
    sites = []
    for site, members in families.items():
        on_map = [
            m
            for m in [site, *members]
            if locations[m].get("latitude") is not None and locations[m].get("longitude") is not None
        ]
        sites.append(
            {
                "id": on_map[0] if on_map else site,
                "name": locations[site].get("name") or site,
                "on_map": bool(on_map),
            }
        )
    return sorted(sites, key=lambda site: (site["name"].lower(), site["id"]))


CSV_COLUMNS = (
    "group",
    "cid",
    "provider",
    "type",
    "status",
    "tenant",
    "commit_rate",
    "description",
    "suggested_sites",
    "suggested_site_ids",
    "circuit_id",
)


def csv_lines(data: dict):
    """The mapping as CSV rows (#385), header first; sites joined with "; "."""
    yield export.csv_header(CSV_COLUMNS)
    for circuit in data["circuits"]:
        yield export.csv_line(
            (
                GROUP_LABELS[circuit["group"]],
                circuit["cid"],
                circuit["provider"],
                circuit["circuit_type"],
                circuit["status"],
                circuit["tenant"],
                circuit["commit_rate"],
                circuit["description"],
                "; ".join(site["name"] for site in circuit["sites"]),
                "; ".join(site["id"] for site in circuit["sites"]),
                circuit["id"],
            )
        )
