"""Access point status from the wireless controllers (#508): Cisco Catalyst
Center and HPE Aruba Networking New Central.

Each instance in ``settings.WIRELESS_CONTROLLERS`` is read on its own, in
bulk, every ``WIRELESS_SYNC_INTERVAL_SECONDS``: its APs replace its rows in
``wireless_ap_status``, and its sync state (``wireless:<kind>:<name>``) says
when it last answered.  One slow or unreachable instance holds up or wipes
nobody else's APs; after ``stale_seconds()`` its rows are its last known
answer (#521), kept until it answers again or is removed from the file.

The board matches a Nautobot device to a controller AP by serial, else (an
access point only, and a name only one AP carries) by name (``lookup``).  A
matched device is monitored without a primary IP (APs get theirs by DHCP),
shown with the controller's address, and down when the controller says so
(#521): its site at ``WIRELESS_AP_LEVEL``, never notified (``alerts``).  The
same AP in two instances: the newest report wins; on a tie, up wins, so a
stale copy never shows it down.

Called as ``wireless.function()`` so tests can replace it on this module.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from nautobot_maps import arubacentral, catalyst, contract, db, inventory, metrics, settings, timeutil

logger = logging.getLogger(__name__)

KIND_NAMES = {"catalyst_center": "Catalyst Center", "aruba_central": "Aruba Central"}  # one per settings kind
# Instances read at once: enough for a few slow ones, gentle on the app.
MAX_PARALLEL = 4


def controller_id(controller: dict) -> str:
    return f"{controller['kind']}:{controller['name']}"


def source(controller: dict) -> str:
    """Its key in inventory_sync_state."""
    return f"wireless:{controller_id(controller)}"


def display_name(controller: dict) -> str:
    """'Catalyst Center dk', as the top bar and the check say it."""
    return f"{KIND_NAMES.get(controller['kind'], controller['kind'])} {controller['name']}"


def configured() -> tuple[dict, ...]:
    return tuple(settings.WIRELESS_CONTROLLERS or ())


def stale_seconds() -> int:
    """How long an instance's last answer counts: as the top bar's staleness (#419)."""
    return max(metrics.STALE_INTERVALS * settings.WIRELESS_SYNC_INTERVAL_SECONDS, metrics.STALE_FLOOR_SECONDS)


def serial_key(value) -> str:
    return str(value or "").strip().upper()


def name_key(value) -> str:
    """As a LibreNMS host: lower case, the part before the first dot."""
    return inventory.librenms_host_key(str(value or ""))


# Each kind's client: (read its records, the data contract endpoint, one record as an AP).
CLIENTS = {
    "catalyst_center": (
        lambda controller: catalyst.fetch_devices(controller),
        (contract.CATALYST_CENTER, "network-device?family=Unified AP"),
        lambda record: catalyst.ap_record(record),
    ),
    "aruba_central": (
        lambda controller: arubacentral.fetch_aps(controller),
        (contract.ARUBA_CENTRAL, "network-monitoring/v1/aps"),
        lambda record: arubacentral.ap_record(record),
    ),
}


def fetch_aps(controller: dict) -> list[dict]:
    if controller["kind"] not in CLIENTS:
        raise RuntimeError(f"Unknown wireless controller kind {controller['kind']!r}")
    read, (source_name, path), as_ap = CLIENTS[controller["kind"]]
    records = read(controller)
    contract.check_and_record(source_name, path, records)
    return [as_ap(record) for record in records]


def write_aps(conn, controller: dict, aps: list[dict], seen_at: str) -> int:
    """Replace this instance's rows with *aps*; returns how many were kept
    (one per serial, else per name: an AP listed twice counts once)."""
    rows = {}
    for ap in aps:
        serial, name = serial_key(ap.get("serial")), name_key(ap.get("name"))
        if not serial and not name:
            continue
        rows[serial or f"name:{name}"] = (
            controller_id(controller),
            serial,
            name,
            str(ap.get("serial") or ""),
            str(ap.get("mac") or ""),
            str(ap.get("name") or ""),
            str(ap.get("ip") or ""),
            bool(ap.get("up")),
            seen_at,
        )
    conn.execute("DELETE FROM wireless_ap_status WHERE controller = %s", (controller_id(controller),))
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO wireless_ap_status (controller, serial_key, name_key, serial, mac, name, ip, up, seen_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            list(rows.values()),
        )
    return len(rows)


def sync_controller(controller: dict) -> None:
    """Read one instance and store what it says; a failure is recorded on its
    sync state (the top bar shows it) and leaves its last answer to age out."""
    conn = db.get_conn()
    if conn is None:
        return
    key = source(controller)
    started_at = timeutil.iso_utc_now()
    try:
        with db.transaction(conn):
            inventory.record_sync_state(conn, key, last_started_at=started_at, status="running")
        aps = fetch_aps(controller)
        existing = conn.execute(
            "SELECT count(*) AS n FROM wireless_ap_status WHERE controller = %s", (controller_id(controller),)
        ).fetchone()
        if not aps and int(db.row_to_dict(existing).get("n") or 0):
            raise RuntimeError("it listed no access points; keeping its last answer")
        with db.transaction(conn):
            completed_at = timeutil.iso_utc_now()
            kept = write_aps(conn, controller, aps, completed_at)
            inventory.record_sync_state(
                conn, key, last_started_at=started_at, last_completed_at=completed_at, status="idle"
            )
        logger.info("%s: %s access points", display_name(controller), kept)
    except Exception as exc:
        # The exception's text names the URL at most, never the password or token.
        logger.warning("Could not read %s: %s", display_name(controller), exc)
        with db.transaction(conn):
            inventory.record_sync_state(
                conn,
                key,
                last_started_at=started_at,
                last_completed_at=timeutil.iso_utc_now(),
                status="error",
                error_message=str(exc)[:500],
            )
    finally:
        conn.close()


def due(conn) -> list[dict]:
    return [
        controller
        for controller in configured()
        if inventory.state_due(
            inventory.get_sync_state(source(controller), conn=conn), settings.WIRELESS_SYNC_INTERVAL_SECONDS
        )
    ]


def read_index(conn, now: datetime | None = None) -> dict:
    """What the configured instances say: ``{"serial": {key: ap}, "name":
    {key: ap}}``, each *ap* ``{"up", "ip", "controller", "stale"}``
    (*controller* as ``display_name``).  One query.

    An instance that hasn't answered for ``stale_seconds()`` keeps its last
    answer, marked *stale* (#521): its APs stay down or up as they were, so a
    controller that can't be reached never clears the board.

    A name that two different APs carry (``ap-01`` at two sites) isn't in the
    name index: it would count the wrong one down.  The same AP in two
    instances (the same serial) is one AP."""
    names = {controller_id(controller): display_name(controller) for controller in configured()}
    if not names:
        return {"serial": {}, "name": {}}
    stale_before = ((now or datetime.now(UTC)) - timedelta(seconds=stale_seconds())).timestamp()
    rows = conn.execute(
        "SELECT controller, serial_key, name_key, up, ip, extract(epoch FROM seen_at) AS seen "
        "FROM wireless_ap_status WHERE controller = ANY(%s)",
        (list(names),),
    ).fetchall()
    best: dict[str, dict] = {"serial": {}, "name": {}}  # key: (rank, ap)
    carriers: dict[str, set] = {}  # name: the APs that carry it
    for row in map(db.row_to_dict, rows):
        # The newest answer wins; on a tie, up.  Seconds, not text: a time's
        # text drops zero microseconds and wouldn't sort.
        rank = (float(row["seen"]), bool(row["up"]))
        ap = {
            "up": bool(row["up"]),
            "ip": row["ip"] or "",
            "controller": names[row["controller"]],
            "stale": float(row["seen"]) < stale_before,
        }
        for kind in ("serial", "name"):
            key = row[f"{kind}_key"]
            if key and (key not in best[kind] or rank > best[kind][key][0]):
                best[kind][key] = (rank, ap)
        if row["name_key"]:
            carriers.setdefault(row["name_key"], set()).add(
                row["serial_key"] or f"{row['controller']}:{row['name_key']}"
            )
    for name, aps in carriers.items():
        if len(aps) > 1:
            best["name"].pop(name, None)
    return {kind: {key: ap for key, (_, ap) in keys.items()} for kind, keys in best.items()}


def load_index() -> dict:
    """``read_index`` on its own connection, for one site's panel; {} when none
    is configured, without a database, or on an error."""
    if not configured():
        return {}
    conn = db.get_conn()
    if conn is None:
        return {}
    try:
        return read_index(conn)
    except Exception as exc:
        logger.warning("Could not read the wireless controllers' access points: %s", exc)
        return {}
    finally:
        conn.close()


def is_access_point(device: dict) -> bool:
    return (device.get("role") or "").strip().casefold() in settings.ACCESS_POINT_ROLES


def lookup(index: dict | None, device: dict) -> dict | None:
    """What a controller says of this Nautobot device: by its serial, else,
    for an access point (ACCESS_POINT_ROLES) only, by its name; None when no
    controller lists it."""
    if not index:
        return None
    serial = serial_key(device.get("serial"))
    if serial and serial in index["serial"]:
        return index["serial"][serial]
    name = name_key(device.get("name"))
    if name and name in index["name"] and is_access_point(device):
        return index["name"][name]
    return None


def attach(devices: list[dict], index: dict | None) -> list[dict]:
    """*devices* with ``wireless`` (``lookup``'s answer) on each one a
    controller lists (#521): it is monitored without a primary IP.  New dicts
    for those only; the (cached) inventory's are never changed."""
    if not index or not (index["serial"] or index["name"]):
        return devices
    return [{**device, "wireless": ap} if (ap := lookup(index, device)) else device for device in devices]


def reason(ap: dict) -> str:
    """Who says the access point is down, on its board row (#521)."""
    if ap.get("stale"):
        return f"last known per {ap['controller']}, which can't be reached"
    return f"per {ap['controller']}"


def match_keys(devices: list[dict]) -> tuple[set, set]:
    """The Nautobot devices' serials, and their access points' names, to match APs against."""
    serials = {serial_key(d.get("serial")) for d in devices} - {""}
    names = {name_key(d.get("name")) for d in devices if is_access_point(d)} - {""}
    return serials, names


def matches(ap_serial_key: str, ap_name_key: str, serials: set, names: set) -> bool:
    return (bool(ap_serial_key) and ap_serial_key in serials) or (bool(ap_name_key) and ap_name_key in names)


def down_fingerprint(conn) -> tuple:
    """The APs down, and whether each is only last known: the board shows both."""
    index = read_index(conn)
    return tuple(
        sorted(
            (kind, key, ap["stale"]) for kind in ("serial", "name") for key, ap in index[kind].items() if not ap["up"]
        )
    )


def prune(conn) -> None:
    """Forget instances no longer in the file (renamed or removed): their APs
    and sync state, so neither /metrics nor the top bar keeps them."""
    ids = [controller_id(controller) for controller in configured()]
    with db.transaction(conn):
        conn.execute("DELETE FROM wireless_ap_status WHERE NOT (controller = ANY(%s))", (ids,))
        conn.execute(
            "DELETE FROM inventory_sync_state WHERE source LIKE 'wireless:%%' AND NOT (source = ANY(%s))",
            ([f"wireless:{controller_id}" for controller_id in ids],),
        )


# Kept between syncs: its threads keep their HTTP sessions, so each instance's
# connection is reused rather than opened anew every interval.
_pool: ThreadPoolExecutor | None = None


def pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL, thread_name_prefix="wireless")
    return _pool


def sync_due() -> bool:
    """Read the instances that are due, a few at a time; returns whether the
    APs down changed, so the board is rebuilt only then."""
    conn = db.get_conn()
    if conn is None:
        return False
    try:
        prune(conn)
        todo = due(conn) if configured() else []
        if not todo:
            return False
        before = down_fingerprint(conn)
    finally:
        conn.close()
    list(pool().map(sync_controller, todo))
    conn = db.get_conn()
    if conn is None:
        return False
    try:
        return down_fingerprint(conn) != before
    finally:
        conn.close()


def check() -> list[dict]:
    """For ``python -m nautobot_maps wireless-check``: each instance read now,
    how many APs it lists and how many match a Nautobot device."""
    serials, names = match_keys(inventory.read_devices())
    results, seen = [], {}
    for controller in configured():
        result = {"controller": display_name(controller), "url": controller["url"]}
        try:
            aps = fetch_aps(controller)
        except Exception as exc:
            result["error"] = str(exc)[:300]
            results.append(result)
            continue
        matched, unmatched = 0, []
        for ap in aps:
            if matches(serial_key(ap["serial"]), name_key(ap["name"]), serials, names):
                matched += 1
            elif len(unmatched) < 10:
                unmatched.append(ap["name"] or ap["serial"])
            if serial_key(ap["serial"]):
                seen.setdefault(serial_key(ap["serial"]), set()).add(display_name(controller))
        result.update(
            {
                "access_points": len(aps),
                "down": sum(1 for ap in aps if not ap["up"]),
                "matched": matched,
                "unmatched_examples": unmatched,
            }
        )
        results.append(result)
    duplicates = {serial: sorted(where) for serial, where in seen.items() if len(where) > 1}
    if duplicates:
        results.append({"duplicates": dict(list(sorted(duplicates.items()))[:20]), "duplicate_count": len(duplicates)})
    return results
