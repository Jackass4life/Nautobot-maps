"""Which switch and port each access point is on (#524).

When an access point is down, the first question is which switch it is
plugged into; when many at a site are down behind one switch, that switch,
its uplink or its PoE is the likely problem, not the access points.

Two sources, read in bulk with their syncs, never while building the board:

1. **Catalyst Center** wins: its physical topology (CDP/LLDP), one request
   per instance with its access points (``wireless.sync_neighbours``),
   stored in ``wireless_ap_neighbours``; matched to Nautobot like the access
   points, by serial, else name.
2. Else **Nautobot**: a cable between the access point's interface and
   another device's, cached with the inventory (``nautobot_cable_cache``).

Called as ``neighbours.function()`` so tests can replace it on this module.
"""

import logging

from nautobot_maps import db, settings, wireless

logger = logging.getLogger(__name__)

NAUTOBOT_SOURCE = "Nautobot"
# "12 of 16 behind sw02": at least this many down behind one switch.
BEHIND_MIN_DOWN = 2


def empty_index() -> dict:
    return {"serial": {}, "name": {}, "cables": {}}


def read_index(conn, devices: list[dict] | None = None) -> dict:
    """Every access point's neighbour, in two queries: ``{"serial": {key:
    n}, "name": {key: n}, "cables": {device_id: n}}``, each *n* ``{"device",
    "port", "source"}`` (and ``device_id`` when Nautobot has the switch).
    *devices*: only these (one site's panel)."""
    index = empty_index()
    names = {wireless.controller_id(c): wireless.display_name(c) for c in wireless.configured()}
    serials, short_names = wireless.match_keys(devices) if devices is not None else (set(), set())
    ids = [device.get("id") for device in devices or [] if device.get("id")]
    if names:
        sql = (
            "SELECT controller, serial_key, name_key, switch_name, switch_port FROM wireless_ap_neighbours "
            "WHERE controller = ANY(%s)"
        )
        params: tuple = (list(names),)
        if devices is not None:
            sql += " AND (serial_key = ANY(%s) OR name_key = ANY(%s))"
            params += (list(serials), list(short_names))
        rows = conn.execute(sql + " ORDER BY seen_at", params).fetchall()
        for row in map(db.row_to_dict, rows):
            neighbour = {"device": row["switch_name"], "port": row["switch_port"], "source": names[row["controller"]]}
            # Ordered by when it was seen: the newest answer wins.
            if row["serial_key"]:
                index["serial"][row["serial_key"]] = neighbour
            if row["name_key"]:
                index["name"][row["name_key"]] = neighbour
    roles = list(settings.ACCESS_POINT_ROLES)
    rows = conn.execute(
        """
        SELECT c.a_device_id, c.a_port, c.b_device_id, c.b_port,
               coalesce(da.name, '') AS a_name, lower(coalesce(da.role, '')) AS a_role,
               coalesce(db.name, '') AS b_name, lower(coalesce(db.role, '')) AS b_role
        FROM nautobot_cable_cache c
        LEFT JOIN nautobot_device_cache da ON da.device_id = c.a_device_id
        LEFT JOIN nautobot_device_cache db ON db.device_id = c.b_device_id
        WHERE (lower(coalesce(da.role, '')) = ANY(%s) OR lower(coalesce(db.role, '')) = ANY(%s))
          AND (%s OR c.a_device_id = ANY(%s) OR c.b_device_id = ANY(%s))
        ORDER BY c.cable_id
        """,
        (roles, roles, devices is None, ids, ids),
    ).fetchall()
    for row in map(db.row_to_dict, rows):
        a_ap, b_ap = row["a_role"] in roles, row["b_role"] in roles
        if a_ap == b_ap:
            continue  # two access points, or neither: no switch to name
        ap, other = ("a", "b") if a_ap else ("b", "a")
        if not row[f"{other}_name"]:
            continue
        index["cables"].setdefault(
            row[f"{ap}_device_id"],
            {
                "device": row[f"{other}_name"],
                "device_id": row[f"{other}_device_id"],
                "port": row[f"{other}_port"],
                "source": NAUTOBOT_SOURCE,
            },
        )
    return index


def load_index(devices: list[dict] | None = None) -> dict:
    """``read_index`` (of *devices* only, when given) on its own connection;
    empty without a database or on an error."""
    conn = db.get_conn() if db.dialect() else None
    if conn is None:
        return empty_index()
    try:
        return read_index(conn, devices)
    except Exception as exc:
        logger.warning("Could not read the access points' switches: %s", exc)
        return empty_index()
    finally:
        conn.close()


def lookup(index: dict | None, device: dict) -> dict | None:
    """The switch and port this access point is on, or None: Catalyst Center
    by serial, else by name, else a Nautobot cable."""
    if not index:
        return None
    serial = wireless.serial_key(device.get("serial"))
    if serial and serial in index["serial"]:
        return index["serial"][serial]
    name = wireless.name_key(device.get("name"))
    if name and name in index["name"]:
        return index["name"][name]
    return index["cables"].get(device.get("id") or "")


def is_ap(device: dict) -> bool:
    return bool(device.get("wireless")) or wireless.is_access_point(device)


def behind(access_points: list[dict], down_ids: set[str], index: dict | None) -> dict | None:
    """The switch most of a site's down access points are behind, when at
    least two are: ``{"device", "down", "total"}`` ("12 of 16 behind sw02":
    of the 16 access points on it, 12 are down)."""
    if not index or len(down_ids) < BEHIND_MIN_DOWN:
        return None
    totals: dict[str, int] = {}
    downs: dict[str, int] = {}
    for device in access_points:
        neighbour = lookup(index, device)
        if not neighbour:
            continue
        switch = neighbour["device"]
        totals[switch] = totals.get(switch, 0) + 1
        if (device.get("id") or "") in down_ids:
            downs[switch] = downs.get(switch, 0) + 1
    if not downs:
        return None
    switch = max(sorted(downs), key=lambda name: downs[name])
    if downs[switch] < BEHIND_MIN_DOWN:
        return None
    return {"device": switch, "down": downs[switch], "total": totals[switch]}


def text(neighbour: dict) -> str:
    """'Connected to hel0008-acc-sw02 · Gi1/0/14'."""
    return " · ".join(part for part in (f"Connected to {neighbour['device']}", neighbour.get("port") or "") if part)
