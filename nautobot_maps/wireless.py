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

import requests

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


# Which kinds list devices onboarding with Plug and Play (#534), and how.
PNP_CLIENTS = {
    "catalyst_center": (
        lambda controller: catalyst.fetch_pnp_devices(controller),
        (contract.CATALYST_CENTER, "onboarding/pnp-device"),
        lambda record: catalyst.pnp_record(record),
    ),
}


def fetch_pnp(controller: dict) -> list[dict] | None:
    """The devices Plug and Play lists on this instance, or None for a kind without it."""
    if controller["kind"] not in PNP_CLIENTS:
        return None
    read, (source_name, path), as_pnp = PNP_CLIENTS[controller["kind"]]
    records = read(controller)
    contract.check_and_record(source_name, path, records)
    return [as_pnp(record) for record in records]


def write_pnp(conn, controller: dict, devices: list[dict], seen_at: str) -> int:
    """Replace this instance's Plug and Play rows with *devices*, one per serial."""
    rows = {}
    for device in devices:
        serial = serial_key(device.get("serial"))
        if not serial:
            continue
        last_contact = device.get("last_contact")
        rows[serial] = (
            controller_id(controller),
            serial,
            str(device.get("serial") or ""),
            str(device.get("hostname") or ""),
            str(device.get("pid") or ""),
            str(device.get("state") or ""),
            str(device.get("onb_state") or ""),
            str(device.get("error") or "")[:500],
            datetime.fromtimestamp(last_contact, UTC) if last_contact else None,
            seen_at,
        )
    conn.execute("DELETE FROM wireless_pnp_status WHERE controller = %s", (controller_id(controller),))
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO wireless_pnp_status (controller, serial_key, serial, hostname, pid, state, onb_state, error, "
            "last_contact, seen_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            list(rows.values()),
        )
    return len(rows)


def sync_pnp(conn, controller: dict) -> None:
    """Read and store what this instance's Plug and Play lists (#534).  A
    failure is logged and keeps its last answer, as its access points do; it
    never fails the access points' sync."""
    try:
        devices = fetch_pnp(controller)
        if devices is None:
            return
        with db.transaction(conn):
            kept = write_pnp(conn, controller, devices, timeutil.iso_utc_now())
        logger.info("%s: %s devices in Plug and Play", display_name(controller), kept)
    except Exception as exc:
        logger.warning(
            "Could not read %s's Plug and Play devices; keeping its last answer: %s", display_name(controller), exc
        )


def sync_neighbours(conn, controller: dict, aps: list[dict]) -> None:
    """Which switch and port each of this instance's APs is on (#524): its
    physical topology (CDP/LLDP), one request, matched to the APs just read
    by the topology's node id.  A failure keeps the last answer."""
    if controller["kind"] != "catalyst_center":
        return
    try:
        topology = catalyst.fetch_topology(controller)
        contract.check_and_record(contract.CATALYST_CENTER, "topology/physical-topology", topology.get("links") or [])
        by_id = {ap["id"]: ap for ap in aps if ap.get("id")}
        rows = {}
        seen_at = timeutil.iso_utc_now()
        for neighbour in catalyst.ap_neighbours(topology):
            ap = by_id.get(neighbour["ap_id"]) or {}
            serial, name = serial_key(ap.get("serial")), name_key(ap.get("name") or neighbour["ap_name"])
            if serial or name:
                rows[serial or f"name:{name}"] = (
                    controller_id(controller),
                    serial,
                    name,
                    neighbour["switch"],
                    neighbour["port"],
                    seen_at,
                )
        with db.transaction(conn):
            conn.execute("DELETE FROM wireless_ap_neighbours WHERE controller = %s", (controller_id(controller),))
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO wireless_ap_neighbours (controller, serial_key, name_key, switch_name, switch_port, "
                    "seen_at) VALUES (%s, %s, %s, %s, %s, %s)",
                    list(rows.values()),
                )
        logger.info("%s: %s access points' switches", display_name(controller), len(rows))
    except Exception as exc:
        logger.warning("Could not read %s's topology; keeping its last answer: %s", display_name(controller), exc)


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
        sync_pnp(conn, controller)
        sync_neighbours(conn, controller, aps)
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


def read_index(conn, now: datetime | None = None, devices: list[dict] | None = None) -> dict:
    """What the configured instances say: ``{"serial": {key: ap}, "name":
    {key: ap}}``, each *ap* ``{"up", "ip", "controller", "stale"}``
    (*controller* as ``display_name``).  One query.

    An instance that hasn't answered for ``stale_seconds()`` keeps its last
    answer, marked *stale* (#521): its APs stay down or up as they were, so a
    controller that can't be reached never clears the board.

    A name that two different APs carry (``ap-01`` at two sites) isn't in the
    name index: it would count the wrong one down.  The same AP in two
    instances (the same serial) is one AP.

    *devices*: only what these Nautobot devices need (one site's panel),
    not every AP of every instance."""
    names = {controller_id(controller): display_name(controller) for controller in configured()}
    if not names:
        return {"serial": {}, "name": {}, "pnp": {}}
    stale_before = ((now or datetime.now(UTC)) - timedelta(seconds=stale_seconds())).timestamp()
    sql = (
        "SELECT controller, serial_key, name_key, up, ip, extract(epoch FROM seen_at) AS seen "
        "FROM wireless_ap_status WHERE controller = ANY(%s)"
    )
    params: tuple = (list(names),)
    if devices is not None:
        # Every row of these serials and names: a name another AP carries too
        # must still be seen, to leave it out.
        serials, short_names = match_keys(devices)
        sql += " AND (serial_key = ANY(%s) OR name_key = ANY(%s))"
        params += (list(serials), list(short_names))
    rows = conn.execute(sql, params).fetchall()
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
    index = {kind: {key: ap for key, (_, ap) in keys.items()} for kind, keys in best.items()}
    index["pnp"] = read_pnp(conn, names, stale_before, None if devices is None else match_keys(devices)[0])
    return index


def read_pnp(conn, names: dict, stale_before: float, serials: set | None = None) -> dict:
    """The devices onboarding with Plug and Play (#534), by serial: ``{"state",
    "onb_state", "error", "controller", "stale"}``; the newest answer wins.
    *serials*: only these."""
    sql = (
        "SELECT controller, serial_key, state, onb_state, error, extract(epoch FROM seen_at) AS seen "
        "FROM wireless_pnp_status WHERE controller = ANY(%s)"
    )
    params: tuple = (list(names),)
    if serials is not None:
        sql += " AND serial_key = ANY(%s)"
        params += (list(serials),)
    found: dict[str, tuple] = {}
    for row in map(db.row_to_dict, conn.execute(sql, params).fetchall()):
        seen = float(row["seen"])
        if row["serial_key"] in found and found[row["serial_key"]][0] >= seen:
            continue
        found[row["serial_key"]] = (
            seen,
            {
                "state": row["state"],
                "onb_state": row["onb_state"],
                "error": row["error"],
                "controller": names[row["controller"]],
                "stale": seen < stale_before,
            },
        )
    return {serial: pnp for serial, (_, pnp) in found.items()}


def load_index(devices: list[dict] | None = None) -> dict:
    """``read_index`` (of *devices* only, when given) on its own connection,
    for one site's panel; {} when none is configured, without a database, or
    on an error."""
    if not configured():
        return {}
    conn = db.get_conn()
    if conn is None:
        return {}
    try:
        return read_index(conn, devices=devices)
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
    controller lists (#521): it is monitored without a primary IP.  One no
    controller lists, but Plug and Play does, gets ``onboarding`` instead
    (#534): not monitored yet, and the panel says why.  New dicts for those
    only; the (cached) inventory's are never changed."""
    if not index or not (index["serial"] or index["name"] or index.get("pnp")):
        return devices
    attached = []
    for device in devices:
        if ap := lookup(index, device):
            device = {**device, "wireless": ap}
        elif pnp := (index.get("pnp") or {}).get(serial_key(device.get("serial"))):
            device = {**device, "onboarding": pnp}
        attached.append(device)
    return attached


def onboarding_text(pnp: dict) -> str:
    """Where a device is in Plug and Play, for the site panel (#534):
    "onboarding (PnP: Unclaimed)", "PnP error: ..."."""
    state = pnp.get("state") or "unknown"
    if state.casefold() == "error" or pnp.get("error"):
        return f"PnP error: {pnp['error']}" if pnp.get("error") else "PnP error"
    return f"onboarding (PnP: {state})"


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


def fingerprint(conn, now: datetime | None = None) -> str:
    """Everything of the APs the board shows (#521): which are listed (now
    monitored), up or down, last known or not, and their addresses.  Hashed
    in the database: no 10,000 rows into Python twice a tick."""
    ids = [controller_id(controller) for controller in configured()]
    stale_before = (now or datetime.now(UTC)) - timedelta(seconds=stale_seconds())
    row = conn.execute(
        "SELECT md5(coalesce(string_agg(concat_ws('|', controller, serial_key, name_key, up, ip, seen_at < %s), ',' "
        "ORDER BY controller, serial_key, name_key), '')) AS hash FROM wireless_ap_status WHERE controller = ANY(%s)",
        (stale_before, ids),
    ).fetchone()
    return db.row_to_dict(row).get("hash") or ""


def prune(conn) -> None:
    """Forget instances no longer in the file (renamed or removed): their APs
    and sync state, so neither /metrics nor the top bar keeps them."""
    ids = [controller_id(controller) for controller in configured()]
    with db.transaction(conn):
        conn.execute("DELETE FROM wireless_ap_status WHERE NOT (controller = ANY(%s))", (ids,))
        conn.execute("DELETE FROM wireless_pnp_status WHERE NOT (controller = ANY(%s))", (ids,))
        conn.execute("DELETE FROM wireless_ap_neighbours WHERE NOT (controller = ANY(%s))", (ids,))
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
    """Read the instances that are due, a few at a time; returns whether what
    the board shows of the APs changed, so it is rebuilt only then."""
    conn = db.get_conn()
    if conn is None:
        return False
    try:
        prune(conn)
        todo = due(conn) if configured() else []
        if not todo:
            return False
        before = fingerprint(conn)
    finally:
        conn.close()
    list(pool().map(sync_controller, todo))
    conn = db.get_conn()
    if conn is None:
        return False
    try:
        return fingerprint(conn) != before
    finally:
        conn.close()


def check_failure(exc: Exception) -> dict:
    """Why reading an instance failed, in plain words (#529): whether it
    answered at all (``reachable``) and took the account (``signed_in``,
    None when that is unknown).  Never the credentials."""
    if isinstance(exc, requests.exceptions.SSLError):
        return {
            "reachable": False,
            "signed_in": False,
            "error": "its certificate isn't trusted (a company CA: verify_ssl with the CA file)",
        }
    if isinstance(exc, requests.exceptions.Timeout):
        return {
            "reachable": False,
            "signed_in": False,
            "error": "no answer in time (a firewall, or the server is down)",
        }
    if isinstance(exc, requests.exceptions.ConnectionError):
        return {
            "reachable": False,
            "signed_in": False,
            "error": "can't connect (the address, DNS, a firewall, or the server is down)",
        }
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403):
        return {
            "reachable": True,
            "signed_in": False,
            "error": f"the account was refused (HTTP {status}): check its user and password, or client id and secret",
        }
    if status is not None:
        return {"reachable": True, "signed_in": None, "error": f"answered HTTP {status}"}
    return {"reachable": True, "signed_in": None, "error": str(exc)[:300]}


def check() -> list[dict]:
    """For ``python -m nautobot_maps wireless-check`` and ``POST
    /api/wireless/check`` (#529): each instance read now, how many APs it
    lists and how many match a Nautobot device."""
    serials, names = match_keys(inventory.read_devices())
    results, seen = [], {}
    for controller in configured():
        result = {"controller": display_name(controller), "kind": controller["kind"], "url": controller["url"]}
        try:
            aps = fetch_aps(controller)
        except Exception as exc:
            result.update(check_failure(exc))
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
                "reachable": True,
                "signed_in": True,
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
