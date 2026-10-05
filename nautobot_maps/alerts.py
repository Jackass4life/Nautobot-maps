"""Alert logic (#165): severity scoring, the LibreNMS status merge, alert
history, the alert board build and the location detail for the map.

Called as ``alerts.function()`` so tests can replace it on this module.
"""

import json
import logging
from datetime import UTC, datetime

import requests

from nautobot_maps import caching, db, inventory, librenms, nautobot, settings, timeutil

logger = logging.getLogger(__name__)


# In a status exclusion list, this keyword matches a missing or empty status.
NULL_STATUS_KEYWORD = "null"


def status_is_excluded(status: str | None, excluded: set[str]) -> bool:
    """Return whether *status* is in *excluded* (lower-cased; ``null`` matches no status)."""
    normalized = (status or "").strip().lower()
    if not normalized:
        return NULL_STATUS_KEYWORD in excluded
    return normalized in excluded


# ---------------------------------------------------------------------------
# NOC alert helpers
# ---------------------------------------------------------------------------

# Device statuses that count as "down" for alert purposes
DOWN_STATUSES: frozenset = frozenset({"offline", "failed", "decommissioning"})

# A site is "medium" when more than this share of its monitored devices is down.
MEDIUM_DOWN_RATIO = 0.25

# Tier definitions shown in the alert-board summary tile (i) tooltips.  They
# describe compute_alert_level() and must be kept in sync with it.
ALERT_STATUS_TIER_DEFINITIONS = {
    "alarms": "Sites at Critical, Medium or Low: the ones that need attention. The board opens here.",
    "critical": (
        "At least one core device is down (its role matches a critical keyword, "
        "or it is marked critical by an override), or every monitored device is down."
    ),
    "medium": (
        f"More than {MEDIUM_DOWN_RATIO:.0%} of the site's monitored devices are down and no core device is down."
    ),
    "low": (f"At least one monitored device is down, but {MEDIUM_DOWN_RATIO:.0%} or fewer and no core device."),
    "no_data": ("The site has no monitored devices, or its state could not be computed. Not counted as an alert."),
    "ok": "The site has monitored devices and none of them is down.",
    "total": ("All sites on the board. Only devices with a Nautobot primary IP are monitored."),
}

# Board order, most severe first (#124).
ALERT_LEVEL_ORDER = ("critical", "medium", "low", "no_data", "ok")
# Levels that count as an active alert in summary["non_ok"].
ALERT_LEVELS_NON_OK = ("critical", "medium", "low")


def device_has_primary_ip(device: dict) -> bool:
    return bool((device.get("primary_ip") or "").strip())


def device_display_ip(device: dict) -> str:
    """Return the address to show for *device* on the alert board.

    Prefers the Nautobot primary IP cached at sync (IPv4 before IPv6), without
    its prefix length.  Falls back to the address LibreNMS polls (its
    ``overwrite_ip`` or ``ip``), then to the matched LibreNMS hostname when that
    is itself an IP address.  Returns ``""`` when none is known.
    """
    primary_ip = (device.get("primary_ip") or "").strip()
    if primary_ip:
        return primary_ip.split("/", 1)[0]
    librenms_ip = (device.get("librenms_ip") or "").strip()
    if librenms_ip:
        return librenms_ip
    librenms_hostname = (device.get("librenms_hostname") or "").strip()
    if inventory.is_ip_literal(librenms_hostname):
        return inventory.librenms_ip_key(librenms_hostname)
    return ""


def nautobot_inventory_primary_ip_backfill_pending(conn=None) -> bool:
    """Treat primary-IP backfill as pending until Nautobot has synced the current cache version."""
    state = inventory.get_sync_state("nautobot_inventory", conn=conn)
    return inventory.cache_version_mismatch(state) or not bool((state or {}).get("last_successful_sync"))


def location_is_excluded_from_alert_board(location: dict) -> bool:
    name = (location.get("name") or "").strip().lower()
    location_type = (location.get("location_type") or "").strip().lower()
    tags = set()
    for tag in location.get("tags") or []:
        if isinstance(tag, dict):
            tag_name = nautobot.nested_str(tag, "name", "display", "label", "value")
        else:
            tag_name = str(tag).strip()
        if tag_name:
            tags.add(tag_name.strip().lower())
    return bool(
        (name and name in settings.ALERT_BOARD_EXCLUDED_LOCATION_NAMES)
        or status_is_excluded(location.get("status"), settings.ALERT_BOARD_EXCLUDED_LOCATION_STATUSES)
        or (location_type and location_type in settings.ALERT_BOARD_EXCLUDED_LOCATION_TYPES)
        or (tags & settings.ALERT_BOARD_EXCLUDED_LOCATION_TAGS)
    )


# ---------------------------------------------------------------------------
# Configurable critical-role keyword system
# ---------------------------------------------------------------------------
# Built-in defaults – used when no overrides are configured.
DEFAULT_CORE_ROLE_KEYWORDS: tuple = ("core", "spine", "distribution", "router", "gateway")

# CRITICAL_ROLE_KEYWORDS env var (comma-separated) replaces the built-in
# defaults for every location type that has no specific rule in the JSON file.
env_keywords_raw = settings.CRITICAL_ROLE_KEYWORDS
ENV_CORE_ROLE_KEYWORDS: tuple = (
    tuple(kw.strip().lower() for kw in env_keywords_raw.split(",") if kw.strip())
    if env_keywords_raw
    else DEFAULT_CORE_ROLE_KEYWORDS
)

# Per-location-type rules loaded from the JSON file (if configured).
# Schema: {"<location_type_lower>": ["kw1", "kw2", ...], "default": [...]}
CRITICALITY_RULES: dict = {}
if settings.CRITICALITY_RULES_FILE:
    try:
        with open(settings.CRITICALITY_RULES_FILE, encoding="utf-8") as _f:
            _loaded = json.load(_f)
        if isinstance(_loaded, dict):
            CRITICALITY_RULES = {k.lower(): [kw.lower() for kw in v] for k, v in _loaded.items() if isinstance(v, list)}
            logger.info(
                "Loaded criticality rules from %s: %s",
                settings.CRITICALITY_RULES_FILE,
                list(CRITICALITY_RULES.keys()),
            )
        else:
            logger.warning(
                "Criticality rules file %s must contain a JSON object; ignoring.",
                settings.CRITICALITY_RULES_FILE,
            )
    except Exception as exc:
        logger.warning("Could not load criticality rules from %s: %s", settings.CRITICALITY_RULES_FILE, exc)


def get_critical_keywords(location_type: str | None = None) -> tuple:
    """Return the critical-role keyword set for *location_type*.

    Resolution order:
    1. Per-location-type entry in *CRITICALITY_RULES* (from the JSON file).
    2. ``"default"`` entry in *CRITICALITY_RULES*.
    3. *ENV_CORE_ROLE_KEYWORDS* (from ``CRITICAL_ROLE_KEYWORDS`` env var, or
       the built-in defaults if the env var is not set).
    """
    if location_type and CRITICALITY_RULES:
        lt_key = location_type.lower()
        if lt_key in CRITICALITY_RULES:
            return tuple(CRITICALITY_RULES[lt_key])
        if "default" in CRITICALITY_RULES:
            return tuple(CRITICALITY_RULES["default"])
    return ENV_CORE_ROLE_KEYWORDS


def read_criticality_overrides(conn, device_ids: list | None = None) -> dict:
    """Return ``{device_id: is_critical}`` for *device_ids*, or for every device when ``None``."""
    query = "SELECT nautobot_device_id, is_critical FROM device_criticality_override"
    params: list = []
    if device_ids is not None:
        query += f" WHERE nautobot_device_id IN ({db.placeholders(len(device_ids))})"
        params = list(device_ids)
    overrides = {}
    for row in conn.execute(query, params).fetchall():
        data = db.row_to_dict(row)
        if data.get("nautobot_device_id"):
            overrides[data["nautobot_device_id"]] = bool(data.get("is_critical"))
    return overrides


def compute_alert_level(
    devices: list,
    location_type: str | None = None,
    override_map: dict | None = None,
) -> dict:
    """Return the NOC alert level for a location based on its device list.

    Returns a dict::

        {"level": "critical" | "medium" | "low" | "no_data" | "ok", "reason": "<human-readable text>"}

    Rules (#124):
    * **critical** – at least one device whose role contains a core-network
      keyword has a down status, or every device is down.  The keyword set is
      resolved from ``CRITICAL_ROLE_KEYWORDS`` / ``CRITICALITY_RULES_FILE`` /
      the per-device ``is_critical`` override stored in the database.
    * **medium**   – more than 25 % of all devices have a down status.
    * **low**      – at least one device is down, 25 % or fewer.
    * **no_data**  – there are no devices to judge.
    * **ok**       – devices present, none down.

    The optional *location_type* parameter selects the matching keyword set
    when location-type-scoped rules are configured (e.g. "datacenter" vs
    "office").

    *override_map* (``{device_id: is_critical}``) skips the database read of
    the per-device overrides; the alert board passes one it loaded for all
    sites at once (#149).
    """
    if not devices:
        return {"level": "no_data", "reason": "No monitored devices"}

    core_keywords = get_critical_keywords(location_type)

    if override_map is None:
        override_map = {}
        conn = db.get_conn()
        if conn is not None:
            try:
                ids = [d.get("id") for d in devices if d.get("id")]
                if ids:
                    override_map = read_criticality_overrides(conn, ids)
            except Exception as exc:
                logger.debug("Could not read criticality overrides: %s", exc)
            finally:
                conn.close()

    down_names: list = []
    core_down_names: list = []

    for device in devices:
        status = (device.get("status") or "").lower().strip()
        if status not in DOWN_STATUSES:
            continue
        name = device.get("name") or "Unknown"
        down_names.append(name)
        device_id = device.get("id") or ""
        # Check per-device override first; fall back to keyword matching
        if device_id in override_map:
            is_critical = override_map[device_id]
        else:
            role = (device.get("role") or "").lower()
            is_critical = any(kw in role for kw in core_keywords)
        if is_critical:
            core_down_names.append(name)

    if core_down_names:
        listed = ", ".join(core_down_names[:3])
        suffix = f" (+{len(core_down_names) - 3} more)" if len(core_down_names) > 3 else ""
        return {
            "level": "critical",
            "reason": f"Core device(s) offline: {listed}{suffix}",
        }

    total = len(devices)
    down_count = len(down_names)
    if down_count == total:
        return {
            "level": "critical",
            "reason": f"All {total} monitored device{'s' if total != 1 else ''} offline",
        }
    pct = round(down_count / total * 100)
    if down_count / total > MEDIUM_DOWN_RATIO:
        return {
            "level": "medium",
            "reason": f"{down_count}/{total} devices offline ({pct}%)",
        }
    if down_count:
        return {
            "level": "low",
            "reason": f"{down_count}/{total} devices offline ({pct}%)",
        }

    return {"level": "ok", "reason": ""}


def load_librenms_id_map() -> dict:
    """Load persisted Nautobot UUID → LibreNMS device mapping."""
    lnms_id_map: dict = {}
    conn = db.get_conn()
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT nautobot_device_id, librenms_device_id, librenms_hostname FROM librenms_device_map"
            ).fetchall()
            lnms_id_map = {
                r["nautobot_device_id"]: {
                    "device_id": r["librenms_device_id"],
                    "hostname": r["librenms_hostname"],
                }
                for r in rows
            }
        except Exception as exc:
            logger.debug("Could not read librenms_device_map: %s", exc)
        finally:
            conn.close()
    return lnms_id_map


def librenms_index(lnms_devices: list) -> dict:
    """Look-ups into the LibreNMS inventory by device id, hostname and IP (#240).

    Built once per alert-board build, not once per site.  Hostnames and IPs
    are kept apart so IP-valued hostnames are never short-name normalized
    into ambiguous keys such as "10".
    """
    by_id: dict = {}
    by_hostname: dict = {}
    by_ip: dict = {}
    for ld in lnms_devices:
        if ld.get("device_id") is not None:
            by_id.setdefault(ld.get("device_id"), ld)
        hostname = (ld.get("hostname") or "").strip()
        if hostname:
            if inventory.is_ip_literal(hostname):
                by_ip[inventory.librenms_ip_key(hostname)] = ld
            else:
                by_hostname[inventory.librenms_host_key(hostname)] = ld
    return {"by_id": by_id, "by_hostname": by_hostname, "by_ip": by_ip}


def enrich_with_librenms(
    devices: list,
    lnms_devices: list | None = None,
    lnms_id_map: dict | None = None,
    snapshot_only: bool = False,
    lnms_index: dict | None = None,
    new_librenms_maps: dict | None = None,
) -> list:
    """Merge live LibreNMS status into *devices* (in-place copy returned).

    Each device is matched to a LibreNMS device by its persisted mapping,
    then by hostname, then by primary IP.  New hostname/IP matches are
    persisted in ``librenms_device_map``: added to *new_librenms_maps* when
    given (an alert-board build writes them all at once, #240), otherwise
    written at the end of this call on one connection.

    LibreNMS ``status`` field: ``1`` = up, ``0`` = down.  When LibreNMS
    reports a device as down but Nautobot has it as active, the status is
    set to ``"offline"`` so ``compute_alert_level`` counts it as down.

    The enrichment is *additive*: Nautobot status is never upgraded (a device
    already offline in Nautobot stays offline regardless of LibreNMS).
    """
    if not (settings.LIBRENMS_URL or "").strip() or not (settings.LIBRENMS_API_TOKEN or "").strip():
        return devices

    if lnms_index is None:
        if lnms_devices is None:
            lnms_devices = inventory.read_librenms_devices()
        if not lnms_devices and not snapshot_only:
            inventory.ensure_snapshot()
            lnms_devices = inventory.read_librenms_devices()
        if not lnms_devices and not snapshot_only:
            try:
                lnms_devices = librenms.fetch_inventory()
            except Exception as exc:
                logger.warning("LibreNMS enrichment failed (could not fetch devices): %s", exc)
                return devices
        if not lnms_devices:
            return devices
        lnms_index = librenms_index(lnms_devices)
    if not lnms_index["by_id"] and not lnms_index["by_hostname"] and not lnms_index["by_ip"]:
        return devices

    # Load Nautobot UUID → LibreNMS device ID overrides from DB
    if lnms_id_map is None:
        lnms_id_map = load_librenms_id_map()

    new_maps = new_librenms_maps if new_librenms_maps is not None else {}
    enriched = []
    for device in devices:
        device = dict(device)
        nautobot_id = device.get("id", "")
        lnms_record = None

        # 1. The persisted ID mapping first
        if nautobot_id in lnms_id_map:
            lnms_record = lnms_index["by_id"].get(lnms_id_map[nautobot_id]["device_id"])

        # 2. Fall back to hostname, then IP
        if lnms_record is None:
            lnms_record = lnms_index["by_hostname"].get(inventory.librenms_host_key(device.get("name") or ""))
            if lnms_record is None:
                primary_ip = inventory.librenms_ip_key(device.get("primary_ip") or "")
                if primary_ip:
                    lnms_record = lnms_index["by_ip"].get(primary_ip)

        if lnms_record is not None:
            device["librenms_hostname"] = lnms_record.get("hostname") or ""
            device["librenms_ip"] = inventory.librenms_polled_ip(lnms_record)
            lnms_status = lnms_record.get("status")
            if lnms_status == 0:
                # LibreNMS says down – mark as offline if not already a down status
                current = (device.get("status") or "").lower()
                if current not in DOWN_STATUSES:
                    device["status"] = "offline"
                    # Nautobot still says up, so its last_updated is not when this outage began (#166).
                    device["down_source"] = "librenms"
                    logger.debug(
                        "LibreNMS enrichment: device %s marked offline (LibreNMS status=0)",
                        device.get("name"),
                    )
            # Remember a mapping resolved by hostname or IP
            if nautobot_id and nautobot_id not in lnms_id_map:
                lnms_id = lnms_record.get("device_id")
                if lnms_id:
                    new_maps[nautobot_id] = (lnms_id, lnms_record.get("hostname", ""))

        enriched.append(device)
    if new_librenms_maps is None and new_maps:
        store_librenms_maps(new_maps)
    return enriched


def store_librenms_maps(maps: dict) -> None:
    """Upsert ``{nautobot_device_id: (librenms_device_id, hostname)}`` in one transaction (#240)."""
    if not maps:
        return
    conn = db.get_conn()
    if conn is None:
        return
    try:
        with db.transaction(conn):
            p0, p1, p2 = db.placeholders(3).split(",")
            conn.cursor().executemany(
                f"""
                INSERT INTO librenms_device_map (nautobot_device_id, librenms_device_id, librenms_hostname)
                VALUES ({p0}, {p1}, {p2})
                ON CONFLICT(nautobot_device_id) DO UPDATE SET
                    librenms_device_id = excluded.librenms_device_id,
                    librenms_hostname   = excluded.librenms_hostname
                """,
                [(nautobot_id, lnms_id, hostname or "") for nautobot_id, (lnms_id, hostname) in maps.items()],
            )
    except Exception as exc:
        logger.warning("Could not store %d LibreNMS device mappings: %s", len(maps), exc)
    finally:
        conn.close()


def fetch_live_location_devices(location_id: str) -> list:
    """Fetch devices for one location, handling Nautobot filter-key variants."""
    try:
        return nautobot.fetch_all_pages("dcim/devices/", {"location_id": location_id, "depth": 1})
    except requests.HTTPError as exc:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code != 400:
            raise
    return nautobot.fetch_all_pages("dcim/devices/", {"location": location_id, "depth": 1})


def get_location_devices_and_alert(
    location_id: str,
    location_type: str | None = None,
    devices_data: list | None = None,
    devices_already_normalized: bool = False,
    lookup_maps: dict | None = None,
    lnms_devices: list | None = None,
    lnms_id_map: dict | None = None,
    snapshot_only: bool = False,
    require_primary_ip: bool = False,
    override_map: dict | None = None,
    excluded_device_statuses: set[str] | None = None,
    lnms_index: dict | None = None,
    new_librenms_maps: dict | None = None,
) -> tuple[list, dict]:
    """Return ``(devices, alert)`` for a location.

    Devices whose Nautobot status is in *excluded_device_statuses* are dropped
    before scoring and before the LibreNMS merge (#151).
    """
    use_normalized_devices = devices_already_normalized
    if devices_data is None:
        devices_data = inventory.read_devices(location_id)
        if devices_data:
            use_normalized_devices = True
            if not snapshot_only:
                inventory.ensure_snapshot()
        elif not snapshot_only:
            inventory.ensure_snapshot()
            devices_data = inventory.read_devices(location_id)
            if devices_data:
                use_normalized_devices = True
            else:
                devices_data = fetch_live_location_devices(location_id)

    devices = (
        [
            {
                "id": d.get("id") or "",
                "name": d.get("name") or "Unknown",
                "device_type": d.get("device_type", ""),
                "manufacturer": d.get("manufacturer", ""),
                "role": d.get("role", ""),
                "status": d.get("status", ""),
                "primary_ip": d.get("primary_ip", ""),
                "platform": d.get("platform", ""),
                "serial": d.get("serial", ""),
                "tenant": d.get("tenant", ""),
                "location_path": d.get("location_path", ""),
                "last_updated": d.get("last_updated", ""),
            }
            for d in devices_data
        ]
        if use_normalized_devices
        else inventory.normalize_devices(devices_data, lookup_maps=lookup_maps)
    )
    if require_primary_ip:
        devices = [device for device in devices if device_has_primary_ip(device)]
    if excluded_device_statuses:
        devices = [
            device for device in devices if not status_is_excluded(device.get("status"), excluded_device_statuses)
        ]

    enriched = enrich_with_librenms(
        devices,
        lnms_devices=lnms_devices,
        lnms_id_map=lnms_id_map,
        snapshot_only=snapshot_only,
        lnms_index=lnms_index,
        new_librenms_maps=new_librenms_maps,
    )
    return enriched, compute_alert_level(enriched, location_type, override_map=override_map)


def alert_sort_key(level: str) -> int:
    level = (level or "").lower()
    return ALERT_LEVEL_ORDER.index(level) if level in ALERT_LEVEL_ORDER else len(ALERT_LEVEL_ORDER)


def insert_alert_event(
    conn,
    instance_id: int,
    event_type: str,
    event_at: str,
    alert_level: str,
    alert_reason: str,
    snapshot: dict,
) -> None:
    now_sql = db.sql_now()
    placeholders = db.placeholders(6)
    conn.execute(
        f"""
        INSERT INTO alert_events
            (alert_instance_id, event_type, event_at, alert_level, alert_reason, snapshot_json, created_at)
        VALUES ({placeholders}, {now_sql})
        """,
        (
            instance_id,
            event_type,
            event_at,
            alert_level,
            alert_reason,
            json.dumps(snapshot, separators=(",", ":"), sort_keys=True),
        ),
    )


def resolve_open_alert_instances_for_site(
    conn,
    site_id: str,
    open_alert_keys: set[str],
    checked_at: str,
) -> None:
    marker = db.placeholders(1)
    open_rows = conn.execute(
        f"""
        SELECT id, alert_key, site_id, site_name, device_id, device_name, down_started_at
        FROM alert_instances
        WHERE site_id = {marker} AND status = 'open'
        """,
        (site_id,),
    ).fetchall()
    for row in open_rows:
        row_data = db.row_to_dict(row)
        alert_key = row_data.get("alert_key", "")
        if alert_key in open_alert_keys:
            continue
        started = timeutil.parse_iso_datetime(row_data.get("down_started_at"))
        resolved = timeutil.parse_iso_datetime(checked_at)
        elapsed = 0
        if started and resolved:
            elapsed = max(0, int((resolved - started).total_seconds()))
        now_sql = db.sql_now()
        p0, p1, p2, p3 = db.placeholders(4).split(",")
        cur = conn.execute(
            f"""
            UPDATE alert_instances
            SET status = 'resolved',
                resolved_at = {p0},
                total_downtime_seconds = COALESCE(total_downtime_seconds, 0) + {p1},
                updated_at = {now_sql}
            WHERE id = {p2} AND status = 'open' AND down_started_at <= {p3}
            """,
            (checked_at, elapsed, row_data["id"], checked_at),
        )
        if cur.rowcount == 0:
            continue
        insert_alert_event(
            conn=conn,
            instance_id=row_data["id"],
            event_type="resolved",
            event_at=checked_at,
            alert_level="ok",
            alert_reason="Recovered",
            snapshot={
                "site_id": row_data.get("site_id") or "",
                "site_name": row_data.get("site_name") or "",
                "device_id": row_data.get("device_id") or "",
                "device_name": row_data.get("device_name") or "",
                "status": "resolved",
            },
        )


def down_since(device: dict, checked_at: str) -> str:
    """When *device* went down, as far as we can tell (#166).

    If Nautobot's status makes it down, that status was set at or before the
    device's ``last_updated``; use it when it is earlier than *checked_at*
    (when this build saw the outage).  ``last_updated`` moves on any edit, so
    the earlier of the two is the safe choice.  Devices only LibreNMS reports
    down keep *checked_at*.
    """
    if device.get("down_source") == "librenms":
        return checked_at
    changed = timeutil.parse_iso_datetime(device.get("last_updated"))
    observed = timeutil.parse_iso_datetime(checked_at)
    if changed is None or observed is None:
        return checked_at
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=UTC)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    if changed < observed:
        return changed.astimezone(UTC).isoformat()
    return checked_at


def upsert_alert_lifecycle_for_site(
    site: dict,
    devices: list[dict],
    alert: dict,
    checked_at: str,
    conn=None,
) -> bool:
    """Record the site's down devices as alert instances and resolve recovered ones.

    Returns ``False`` when the write failed (the caller should not reuse *conn*).
    """
    owns_conn = conn is None
    if conn is None:
        conn = db.get_conn()
    if conn is None:
        return False
    site_id = (site.get("id") or "").strip()
    if not site_id:
        if owns_conn:
            conn.close()
        return True
    open_alert_keys: set[str] = set()
    try:
        with db.transaction(conn):
            for device in devices:
                status = (device.get("status") or "").lower().strip()
                if status not in DOWN_STATUSES:
                    continue
                device_id = (device.get("id") or "").strip()
                if not device_id:
                    continue
                level = (alert.get("level") or "no_data").lower()
                reason = alert.get("reason") or ""
                alert_key = db.build_alert_key(site_id, device_id)
                open_alert_keys.add(alert_key)
                marker = db.placeholders(1)
                latest_row = conn.execute(
                    f"""
                    SELECT id, status, down_started_at, alert_level, alert_reason
                    FROM alert_instances
                    WHERE alert_key = {marker} AND status = 'open'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (alert_key,),
                ).fetchone()
                if latest_row is None:
                    now_sql = db.sql_now()
                    p = db.placeholders(10).split(",")
                    conflict_sql = "ON CONFLICT (alert_key) WHERE status = 'open' DO NOTHING"
                    inserted = conn.execute(
                        f"""
                        INSERT INTO alert_instances
                            (alert_key, site_id, site_name, device_id, device_name, alert_level, alert_reason,
                             status, down_started_at, last_seen_down_at, resolved_at, total_downtime_seconds,
                             created_at, updated_at)
                        VALUES ({p[0]}, {p[1]}, {p[2]}, {p[3]}, {p[4]}, {p[5]}, {p[6]}, {p[7]}, {p[8]}, {p[9]},
                                NULL, 0, {now_sql}, {now_sql})
                        {conflict_sql}
                        RETURNING id
                        """,
                        (
                            alert_key,
                            site_id,
                            site.get("name") or "",
                            device_id,
                            device.get("name") or "",
                            level,
                            reason,
                            "open",
                            down_since(device, checked_at),
                            checked_at,
                        ),
                    ).fetchone()
                    inserted_id = db.row_to_dict(inserted).get("id")
                    if inserted_id is not None:
                        insert_alert_event(
                            conn=conn,
                            instance_id=int(inserted_id),
                            event_type="opened",
                            event_at=checked_at,
                            alert_level=level,
                            alert_reason=reason,
                            snapshot={
                                "site_id": site_id,
                                "site_name": site.get("name") or "",
                                "device_id": device_id,
                                "device_name": device.get("name") or "",
                                "status": status,
                            },
                        )
                        continue
                    latest_row = conn.execute(
                        f"""
                        SELECT id, status, down_started_at, alert_level, alert_reason
                        FROM alert_instances
                        WHERE alert_key = {marker} AND status = 'open'
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (alert_key,),
                    ).fetchone()
                    if latest_row is None:
                        continue
                current = db.row_to_dict(latest_row)
                now_sql = db.sql_now()
                p = db.placeholders(7).split(",")
                # down_started_at only ever moves earlier: this also corrects
                # alerts opened before #166 on their next build.
                conn.execute(
                    f"""
                    UPDATE alert_instances
                    SET site_name = {p[0]},
                        device_name = {p[1]},
                        alert_level = {p[2]},
                        alert_reason = {p[3]},
                        last_seen_down_at = {p[4]},
                        down_started_at = LEAST(down_started_at, {p[5]}::timestamptz),
                        updated_at = {now_sql}
                    WHERE id = {p[6]}
                    """,
                    (
                        site.get("name") or "",
                        device.get("name") or "",
                        level,
                        reason,
                        checked_at,
                        down_since(device, checked_at),
                        current["id"],
                    ),
                )
                if current.get("alert_level") != level or current.get("alert_reason") != reason:
                    insert_alert_event(
                        conn=conn,
                        instance_id=current["id"],
                        event_type="updated",
                        event_at=checked_at,
                        alert_level=level,
                        alert_reason=reason,
                        snapshot={
                            "site_id": site_id,
                            "site_name": site.get("name") or "",
                            "device_id": device_id,
                            "device_name": device.get("name") or "",
                            "status": status,
                        },
                    )
            resolve_open_alert_instances_for_site(conn, site_id, open_alert_keys, checked_at)
        return True
    except Exception as exc:
        logger.warning("Could not persist alert lifecycle for site %s: %s", site_id, exc, exc_info=True)
        return False
    finally:
        if owns_conn:
            conn.close()


def get_case_numbers_for_instance(conn, instance_id: int) -> list[str]:
    marker = db.placeholders(1)
    rows = conn.execute(
        f"""
        SELECT case_number
        FROM alert_cases
        WHERE alert_instance_id = {marker}
        ORDER BY created_at DESC
        """,
        (instance_id,),
    ).fetchall()
    return [
        (db.row_to_dict(row).get("case_number") or "").strip()
        for row in rows
        if (db.row_to_dict(row).get("case_number") or "").strip()
    ]


def get_case_numbers_for_instances(conn, instance_ids: list[int]) -> dict[int, list[str]]:
    if not instance_ids:
        return {}
    markers = db.placeholders(len(instance_ids))
    rows = conn.execute(
        f"""
        SELECT alert_instance_id, case_number
        FROM alert_cases
        WHERE alert_instance_id IN ({markers})
        ORDER BY created_at DESC, id DESC
        """,
        tuple(instance_ids),
    ).fetchall()
    case_numbers_by_instance: dict[int, list[str]] = {instance_id: [] for instance_id in instance_ids}
    for row in rows:
        data = db.row_to_dict(row)
        instance_id = int(data.get("alert_instance_id") or 0)
        case_number = (data.get("case_number") or "").strip()
        if instance_id and case_number:
            case_numbers_by_instance.setdefault(instance_id, []).append(case_number)
    return case_numbers_by_instance


def empty_alert_context() -> dict:
    return {
        "active_alert_instance_count": 0,
        "historical_downtime_seconds": 0,
        "current_downtime_seconds": 0,
        "latest_down_at": None,
        "active_cases": [],
        "down_devices": [],
    }


def summarize_alert_context(
    historical_downtime_seconds: int,
    open_rows: list[dict],
    case_numbers_by_instance: dict[int, list[str]],
    checked_at: str,
) -> dict:
    """Build a site's alert context from its alert-instance data.

    *historical_downtime_seconds* is the sum of ``total_downtime_seconds``
    over all the site's instances; *open_rows* are its open instances, newest
    first.  Open instances add the time they have been down so far.
    """
    now_dt = timeutil.parse_iso_datetime(checked_at) or datetime.now(UTC)
    historical_seconds = historical_downtime_seconds
    active_cases: set[str] = set()
    down_devices = []
    current_downtime_seconds = 0
    latest_down_at = None
    for data in open_rows:
        started = timeutil.parse_iso_datetime(data.get("down_started_at"))
        if started is not None:
            elapsed = max(0, int((now_dt - started).total_seconds()))
            historical_seconds += elapsed
            current_downtime_seconds = max(current_downtime_seconds, elapsed)
        latest_down_at = timeutil.max_iso_datetime_value(latest_down_at, data.get("down_started_at"))
        case_numbers = case_numbers_by_instance.get(int(data["id"]), [])
        active_cases.update(case_numbers)
        down_devices.append(
            {
                "device_id": data.get("device_id") or "",
                "device_name": data.get("device_name") or "",
                # When it went down, for "Newest down first" and Copy (#227, #228).
                "down_started_at": data.get("down_started_at") or None,
                "case_numbers": case_numbers,
            }
        )
    return {
        "active_alert_instance_count": len(down_devices),
        "historical_downtime_seconds": historical_seconds,
        "current_downtime_seconds": current_downtime_seconds,
        # The newest open alert's start: the board's default sort (#228).
        "latest_down_at": latest_down_at,
        "active_cases": sorted(active_cases),
        "down_devices": down_devices,
    }


def read_alert_context(conn, site_id: str, checked_at: str) -> dict:
    """Query one site's alert context on *conn*; raises on database errors."""
    site_marker = db.placeholders(1)
    rows = conn.execute(
        f"""
        SELECT id, device_id, device_name, status, down_started_at, total_downtime_seconds
        FROM alert_instances
        WHERE site_id = {site_marker}
        ORDER BY id DESC
        """,
        (site_id,),
    ).fetchall()
    historical_seconds = 0
    open_rows = []
    for row in rows:
        data = db.row_to_dict(row)
        historical_seconds += int(data.get("total_downtime_seconds") or 0)
        if data.get("status") == "open":
            open_rows.append(data)
    case_numbers_by_instance = get_case_numbers_for_instances(
        conn,
        [int(data["id"]) for data in open_rows if data.get("id") is not None],
    )
    return summarize_alert_context(historical_seconds, open_rows, case_numbers_by_instance, checked_at)


def get_alert_context_for_site(site_id: str, checked_at: str, conn=None) -> dict:
    owns_conn = conn is None
    if conn is None:
        conn = db.get_conn()
    if conn is None:
        return empty_alert_context()
    try:
        return read_alert_context(conn, site_id, checked_at)
    except Exception as exc:
        logger.debug("Could not load alert context for site %s: %s", site_id, exc)
        return empty_alert_context()
    finally:
        if owns_conn:
            conn.close()


# A "running" sync state older than this is treated as abandoned (e.g. the
# worker that started it was restarted) rather than still in progress.
SYNC_RUNNING_STALE_SECONDS = 900


def nautobot_sync_in_progress() -> bool:
    """Return whether a Nautobot inventory sync is currently running.

    Reads the shared ``inventory_sync_state`` row, so every worker process
    sees syncs started by any other worker.
    """
    if not db.dialect():
        return False
    state = inventory.get_sync_state("nautobot_inventory")
    if state.get("status") != "running":
        return False
    started_at = timeutil.parse_iso_datetime(state.get("last_started_at"))
    if started_at is None:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    age_seconds = (datetime.now(UTC) - started_at).total_seconds()
    return age_seconds < SYNC_RUNNING_STALE_SECONDS


def inventory_update_schedule() -> tuple[bool, int | None]:
    """Return ``(due, next_update_in_seconds)`` for the configured inventory syncs.

    *due* is true when a sync should start now (by the same rules as
    ``inventory.ensure_snapshot``).  *next_update_in_seconds* is how long
    until the next sync is due: ``0`` when one is due, ``None`` when unknown
    (no persistence, no sources configured, or a sync is running).  The
    board counts down to it (#152).
    """
    sources = []
    if settings.NAUTOBOT_URL and settings.NAUTOBOT_TOKEN:
        sources.append(("nautobot_inventory", settings.INVENTORY_SYNC_INTERVAL_SECONDS))
    if (settings.LIBRENMS_URL or "").strip() and (settings.LIBRENMS_API_TOKEN or "").strip():
        sources.append(("librenms_inventory", settings.LIBRENMS_SYNC_INTERVAL_SECONDS))
    if not sources or not db.dialect():
        return False, None
    conn = db.get_conn()
    if conn is None:
        return False, None
    try:
        now = datetime.now(UTC)
        due = False
        remaining = []
        for source, interval_seconds in sources:
            state = inventory.get_sync_state(source, conn=conn)
            if inventory.sync_due(source, interval_seconds, conn=conn) or (
                source == "nautobot_inventory" and inventory.cache_version_mismatch(state)
            ):
                due = True
                continue
            completed_at = timeutil.parse_iso_datetime(state.get("last_completed_at"))
            if completed_at is None:
                continue  # running
            if completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=UTC)
            elapsed = (now - completed_at).total_seconds()
            remaining.append(max(0, int(interval_seconds - elapsed)))
        if due:
            return True, 0
        return False, (min(remaining) if remaining else None)
    finally:
        conn.close()


def apply_alert_board_freshness(
    payload: dict,
    sync_enqueued: bool = False,
    next_update_in_seconds: int | None = None,
) -> dict:
    """Attach freshness and sync-progress metadata to an alert-board payload.

    ``sync_pending`` is true while an inventory sync is running (or was just
    enqueued by this request); the UI polls until it clears.
    ``next_update_in_seconds`` is relative so a skewed browser clock does not
    shift the board's countdown.
    """
    checked_at = payload.get("checked_at")
    age_seconds = 0
    if checked_at:
        try:
            parsed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
            age_seconds = max(0, int((datetime.now(UTC) - parsed).total_seconds()))
        except ValueError:
            age_seconds = 0
    result = dict(payload)
    stale_after_seconds = int(result.get("stale_after_seconds", settings.CACHE_TTL))
    result["age_seconds"] = age_seconds
    result["stale"] = age_seconds > stale_after_seconds
    result["sync_pending"] = bool(sync_enqueued) or nautobot_sync_in_progress()
    # The board reads only the persisted snapshot, so without a database it is
    # always empty; the UI uses this flag to say why (#136).
    result["persistence_configured"] = bool(db.dialect())
    result["next_update_in_seconds"] = next_update_in_seconds
    return result


PATH_SEPARATOR = " › "
logged_rollup_orphans: set[str] = set()


def location_chain(location_id: str, by_id: dict[str, dict]) -> list[dict]:
    """*location_id* and its ancestors, nearest first (stops at unknown ids and cycles)."""
    chain, seen = [], set()
    while location_id and location_id in by_id and location_id not in seen:
        seen.add(location_id)
        chain.append(by_id[location_id])
        location_id = by_id[location_id].get("parent_id") or ""
    return chain


def ancestor_path(loc: dict, by_id: dict[str, dict]) -> str:
    """The Nautobot location path above *loc*, e.g. ``"EMEA › DNK"``."""
    ancestors = location_chain(loc.get("parent_id") or "", by_id)
    return PATH_SEPARATOR.join(a.get("name", "") for a in reversed(ancestors))


def with_ancestor_paths(locations: list[dict], all_locations: list[dict]) -> list[dict]:
    """*locations*, each with its ``ancestor_path`` (#178)."""
    by_id = {loc.get("id"): loc for loc in all_locations if loc.get("id")}
    return [{**loc, "ancestor_path": ancestor_path(loc, by_id)} for loc in locations]


def roll_up_to_site_locations(
    locations: list[dict],
    devices_by_location: dict[str, list[dict]],
    site_type: str,
    include_non_operational: bool = False,
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Group the board by locations of *site_type*, rolling up descendants (#158).

    Returns ``(rows, devices_by_row)``.  Rows are the locations of
    *site_type* (with ``ancestor_path``, e.g. ``"EMEA › DNK"``), plus any other
    location that holds devices but has no ancestor of that type.  Each
    device is assigned to its nearest such ancestor, with ``location_path``
    naming where below the site it sits (``"Bygning A › Etage 2"``).  Unless
    *include_non_operational*, excluded sites are dropped, and so are devices
    below an excluded location.
    """
    by_id = {loc.get("id"): loc for loc in locations if loc.get("id")}

    def is_site(loc: dict) -> bool:
        return (loc.get("location_type") or "").strip().lower() == site_type

    def is_excluded(loc: dict) -> bool:
        return not include_non_operational and location_is_excluded_from_alert_board(loc)

    rows = []
    row_ids = set()
    for loc in locations:
        if is_site(loc) and not is_excluded(loc):
            rows.append({**loc, "ancestor_path": ancestor_path(loc, by_id)})
            row_ids.add(loc["id"])

    devices_by_row: dict[str, list[dict]] = {}
    for location_id, devices in devices_by_location.items():
        chain = location_chain(location_id, by_id)
        site_index = next((i for i, loc in enumerate(chain) if is_site(loc)), None)
        if site_index is None:
            # No site above it: the location keeps its own row.
            owner = chain[0] if chain else None
            if owner is None or is_excluded(owner):
                continue
            if owner["id"] not in row_ids:
                rows.append({**owner, "ancestor_path": ""})
                row_ids.add(owner["id"])
                if owner["id"] not in logged_rollup_orphans:
                    logged_rollup_orphans.add(owner["id"])
                    logger.info(
                        "Alert board: location %r has devices but no %r above it; it keeps its own row",
                        owner.get("name"),
                        site_type,
                    )
            below_site = []
            row_id = owner["id"]
        else:
            below_site = chain[:site_index]
            row_id = chain[site_index]["id"]
            if row_id not in row_ids or any(is_excluded(loc) for loc in below_site):
                continue
        location_path = PATH_SEPARATOR.join(loc.get("name", "") for loc in reversed(below_site))
        devices_by_row.setdefault(row_id, []).extend({**device, "location_path": location_path} for device in devices)
    return rows, devices_by_row


def read_alert_board_data(conn) -> dict:
    """Read what an alert-board build needs for every site, in a few queries (#149).

    Replaces per-site reads of cached devices, criticality overrides, the
    primary-IP backfill state and the alert context.  Raises on database
    errors; the caller then falls back to reading per site.
    """
    devices_by_location: dict[str, list] = {}
    for device in inventory.read_devices(conn=conn):
        devices_by_location.setdefault(device.get("location_id") or "", []).append(device)

    # Closed instances are summed in SQL: the table grows with every outage.
    historical_downtime_by_site = {}
    for row in conn.execute(
        """
        SELECT site_id, SUM(total_downtime_seconds) AS total
        FROM alert_instances
        GROUP BY site_id
        """
    ).fetchall():
        data = db.row_to_dict(row)
        historical_downtime_by_site[data.get("site_id") or ""] = int(data.get("total") or 0)

    open_rows_by_site: dict[str, list[dict]] = {}
    for row in conn.execute(
        """
        SELECT id, site_id, device_id, device_name, down_started_at
        FROM alert_instances
        WHERE status = 'open'
        ORDER BY id DESC
        """
    ).fetchall():
        data = db.row_to_dict(row)
        open_rows_by_site.setdefault(data.get("site_id") or "", []).append(data)

    case_numbers_by_instance: dict[int, list[str]] = {}
    for row in conn.execute(
        """
        SELECT ac.alert_instance_id, ac.case_number
        FROM alert_cases ac
        JOIN alert_instances ai ON ai.id = ac.alert_instance_id
        WHERE ai.status = 'open'
        ORDER BY ac.created_at DESC, ac.id DESC
        """
    ).fetchall():
        data = db.row_to_dict(row)
        instance_id = int(data.get("alert_instance_id") or 0)
        case_number = (data.get("case_number") or "").strip()
        if instance_id and case_number:
            case_numbers_by_instance.setdefault(instance_id, []).append(case_number)

    return {
        "devices_by_location": devices_by_location,
        "override_map": read_criticality_overrides(conn),
        "primary_ip_backfill_pending": nautobot_inventory_primary_ip_backfill_pending(conn=conn),
        "historical_downtime_by_site": historical_downtime_by_site,
        "open_rows_by_site": open_rows_by_site,
        "case_numbers_by_instance": case_numbers_by_instance,
    }


def record_site_level_changes(levels: dict[str, tuple[str, str]], checked_at: str) -> int:
    """Remember each site's alert level and log the changes, for the alert feed (#180).

    *levels* maps site id to ``(site_name, alert_level)``.  A site seen for the
    first time only gets its level stored: a new database or a newly added site
    is not a change.  The rows are locked in site order, so two builds running
    at once neither deadlock nor log the same change twice.  Returns the number
    of changes logged.
    """
    if not levels:
        return 0
    conn = db.get_conn()
    if conn is None:
        return 0
    changes = 0
    try:
        with db.transaction(conn):
            rows = conn.execute(
                "SELECT site_id, alert_level FROM site_alert_levels "
                "WHERE site_id = ANY(%s) ORDER BY site_id FOR UPDATE",
                (sorted(levels),),
            ).fetchall()
            previous = {row["site_id"]: row["alert_level"] for row in map(db.row_to_dict, rows)}
            for site_id in sorted(levels):
                site_name, level = levels[site_id]
                if site_id not in previous:
                    conn.execute(
                        "INSERT INTO site_alert_levels (site_id, site_name, alert_level, updated_at) "
                        "VALUES (%s, %s, %s, %s) ON CONFLICT (site_id) DO NOTHING",
                        (site_id, site_name, level, checked_at),
                    )
                elif previous[site_id] != level:
                    conn.execute(
                        "UPDATE site_alert_levels SET site_name = %s, alert_level = %s, updated_at = %s "
                        "WHERE site_id = %s",
                        (site_name, level, checked_at, site_id),
                    )
                    conn.execute(
                        "INSERT INTO site_level_changes (site_id, site_name, from_level, to_level, changed_at) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (site_id, site_name, previous[site_id], level, checked_at),
                    )
                    changes += 1
    finally:
        conn.close()
    return changes


HISTORY_RETENTION_SOURCE = "alert_history_retention"
RETENTION_BATCH_SIZE = 1000


def prune_alert_history(conn, retention_days: int) -> dict:
    """Delete alert history older than *retention_days* (#194).

    Resolved alert instances whose ``resolved_at`` is older go, with their
    events and cases (``ON DELETE CASCADE``), and so do site severity
    changes.  Open alerts are never deleted.  Deletes in batches, each its
    own transaction, so the tables aren't locked for long.  Returns the
    number of rows deleted per table.
    """
    deleted = {"alert_instances": 0, "site_level_changes": 0}
    if retention_days <= 0:
        return deleted
    batches = {
        "alert_instances": "DELETE FROM alert_instances WHERE id IN ("
        "SELECT id FROM alert_instances WHERE status = 'resolved' "
        "AND resolved_at < now() - make_interval(days => %s) LIMIT %s)",
        "site_level_changes": "DELETE FROM site_level_changes WHERE id IN ("
        "SELECT id FROM site_level_changes WHERE changed_at < now() - make_interval(days => %s) LIMIT %s)",
    }
    for table, query in batches.items():
        while True:
            with db.transaction(conn):
                count = conn.execute(query, (retention_days, RETENTION_BATCH_SIZE)).rowcount
            deleted[table] += count
            if count < RETENTION_BATCH_SIZE:
                break
    return deleted


def maybe_prune_alert_history(conn) -> dict | None:
    """Prune once a day when ALERT_HISTORY_RETENTION_DAYS is set (scheduler, #194)."""
    days = settings.ALERT_HISTORY_RETENTION_DAYS
    if days <= 0 or not inventory.sync_due(HISTORY_RETENTION_SOURCE, 24 * 3600, conn=conn):
        return None
    started_at = timeutil.iso_utc_now()
    deleted = prune_alert_history(conn, days)
    with db.transaction(conn):
        inventory.record_sync_state(
            conn,
            HISTORY_RETENTION_SOURCE,
            last_started_at=started_at,
            last_completed_at=timeutil.iso_utc_now(),
            status="idle",
        )
    if any(deleted.values()):
        logger.info(
            "Alert history retention (%d days): deleted %d resolved alerts and %d severity changes",
            days,
            deleted["alert_instances"],
            deleted["site_level_changes"],
        )
    return deleted


FEED_KINDS = ("down", "up", "severity")


def read_alert_feed(conn, limit: int, since: str | None = None, kinds=FEED_KINDS) -> list[dict]:
    """What changed on the board, newest first (#180).

    ``down`` (an alert opened) and ``up`` (resolved) come from the alert
    events; ``severity`` from the logged site level changes.  *since* keeps
    only entries after that time.
    """
    branches, params = [], []
    event_types = [event for kind, event in (("down", "opened"), ("up", "resolved")) if kind in kinds]
    if event_types:
        branches.append(
            f"""
            SELECT CASE e.event_type WHEN 'opened' THEN 'down' ELSE 'up' END AS kind,
                   e.event_at AS at, e.id AS seq, i.site_id, i.site_name, i.device_id, i.device_name,
                   e.alert_level AS level, i.down_started_at AS down_since,
                   '' AS from_level, '' AS to_level
            FROM alert_events e
            JOIN alert_instances i ON i.id = e.alert_instance_id
            WHERE e.event_type = ANY(%s) {"AND e.event_at > %s" if since else ""}
            """
        )
        params += [event_types] + ([since] if since else [])
    if "severity" in kinds:
        branches.append(
            f"""
            SELECT 'severity' AS kind, c.changed_at AS at, c.id AS seq, c.site_id, c.site_name,
                   '' AS device_id, '' AS device_name, c.to_level AS level,
                   NULL::timestamptz AS down_since, c.from_level, c.to_level
            FROM site_level_changes c
            {"WHERE c.changed_at > %s" if since else ""}
            """
        )
        params += [since] if since else []
    if not branches:
        return []
    rows = conn.execute(
        # At the same moment a site's severity change sits above the device
        # events that caused it (newest first).
        f"SELECT * FROM ({' UNION ALL '.join(branches)}) feed "
        "ORDER BY at DESC, kind = 'severity' DESC, seq DESC LIMIT %s",
        (*params, limit),
    ).fetchall()
    events = []
    for row in rows:
        event = db.row_to_dict(row)
        event.pop("seq", None)
        if event["kind"] != "down":
            event.pop("down_since", None)
        if event["kind"] != "severity":
            event.pop("from_level", None)
            event.pop("to_level", None)
        events.append(event)
    return events


def build_alert_board_payload(
    snapshot_only: bool = False,
    include_non_operational: bool = False,
) -> dict:
    """Build and return a fresh alert-board payload."""
    locations = inventory.get_locations(
        include_without_coordinates=True,
        snapshot_only=snapshot_only,
    )
    all_locations = locations
    if not include_non_operational:
        locations = [loc for loc in locations if not location_is_excluded_from_alert_board(loc)]
    alerts = []
    summary = dict.fromkeys(ALERT_LEVEL_ORDER, 0)
    # Site levels this build actually observed, for the alert feed (#180).
    observed_levels: dict[str, tuple[str, str]] = {}
    lnms_devices = None
    lnms_id_map = None
    lnms_index = None
    # Device mappings found by hostname/IP, written once after the build (#240).
    new_librenms_maps: dict = {}
    if (settings.LIBRENMS_URL or "").strip() and (settings.LIBRENMS_API_TOKEN or "").strip():
        try:
            lnms_devices = inventory.read_librenms_devices()
            if not lnms_devices and not snapshot_only:
                inventory.ensure_snapshot(force=True, wait=True)
                lnms_devices = inventory.read_librenms_devices()
            lnms_id_map = load_librenms_id_map()
        except Exception as exc:
            logger.warning("Could not refresh LibreNMS inventory for alert board: %s", exc)
            lnms_devices = []
            lnms_id_map = {}
        lnms_index = librenms_index(lnms_devices)

    # Read everything that can be read for all sites at once (#149).  A
    # failed connect disables persistence for this build; a failed bulk read
    # falls back to reading per site.
    persistence_unavailable = False
    board_data = None
    read_conn = None
    try:
        read_conn = db.get_conn()
    except Exception as exc:
        logger.warning("Could not connect to persistence DB for alert board: %s", exc)
    if read_conn is None:
        persistence_unavailable = True
    else:
        try:
            board_data = read_alert_board_data(read_conn)
        except Exception as exc:
            logger.warning("Could not bulk-read alert board data; reading per site instead: %s", exc, exc_info=True)
        finally:
            read_conn.close()

    # One row per site, with the devices of its whole subtree (#158).
    devices_by_row = None
    if settings.ALERT_BOARD_SITE_LOCATION_TYPE:
        if board_data is not None:
            devices_by_location = board_data["devices_by_location"]
        else:
            devices_by_location = {}
            for device in inventory.read_devices():
                devices_by_location.setdefault(device.get("location_id") or "", []).append(device)
        locations, devices_by_row = roll_up_to_site_locations(
            all_locations,
            devices_by_location,
            settings.ALERT_BOARD_SITE_LOCATION_TYPE,
            include_non_operational=include_non_operational,
        )
    else:
        # The full Nautobot location path above each row, as with the roll-up (#178).
        locations = with_ancestor_paths(locations, all_locations)

    # Writes share one connection, opened on first use.  After a failure it is
    # closed and the next site opens a fresh one, so a broken connection
    # cannot fail every later site (#88).
    write_conn = None
    try:
        for loc in locations:
            site_id = loc.get("id") or ""
            observation_succeeded = True
            bulk_kwargs = {}
            if board_data is not None:
                bulk_kwargs = {
                    # A site missing from the snapshot has no cached devices.
                    "devices_data": board_data["devices_by_location"].get(site_id, [] if snapshot_only else None),
                    "devices_already_normalized": True,
                    "override_map": board_data["override_map"],
                }
            if devices_by_row is not None:
                bulk_kwargs["devices_data"] = devices_by_row.get(site_id, [])
                bulk_kwargs["devices_already_normalized"] = True
            try:
                devices, alert = get_location_devices_and_alert(
                    loc["id"],
                    loc.get("location_type") or None,
                    lnms_devices=lnms_devices,
                    lnms_id_map=lnms_id_map,
                    lnms_index=lnms_index,
                    new_librenms_maps=new_librenms_maps,
                    snapshot_only=snapshot_only,
                    require_primary_ip=True,
                    excluded_device_statuses=settings.ALERT_BOARD_EXCLUDED_DEVICE_STATUSES,
                    **bulk_kwargs,
                )
            except Exception as exc:
                logger.warning(
                    "Could not compute alert summary for location %s: %s",
                    loc.get("id"),
                    exc,
                )
                observation_succeeded = False
                devices = []
                alert = {"level": "no_data", "reason": "Could not compute alert state"}

            down_devices = [d for d in devices if (d.get("status") or "").lower().strip() in DOWN_STATUSES]
            checked_at = timeutil.iso_utc_now()
            alert_context = empty_alert_context()
            if board_data is not None:
                primary_ip_backfill_pending = board_data["primary_ip_backfill_pending"]
                # Otherwise the lifecycle step has nothing to open, update or resolve.
                needs_write = (
                    observation_succeeded
                    and not primary_ip_backfill_pending
                    and (bool(down_devices) or site_id in board_data["open_rows_by_site"])
                )
            else:
                primary_ip_backfill_pending = None  # read per site below
                needs_write = True

            wrote = False
            if needs_write and not persistence_unavailable:
                if write_conn is None:
                    try:
                        write_conn = db.get_conn()
                    except Exception as exc:
                        logger.warning("Could not connect to persistence DB for alert board: %s", exc)
                    if write_conn is None:
                        persistence_unavailable = True
                if write_conn is not None:
                    try:
                        if primary_ip_backfill_pending is None:
                            primary_ip_backfill_pending = nautobot_inventory_primary_ip_backfill_pending(
                                conn=write_conn
                            )
                        if observation_succeeded and not primary_ip_backfill_pending:
                            if (
                                upsert_alert_lifecycle_for_site(loc, devices, alert, checked_at, conn=write_conn)
                                is False
                            ):
                                raise RuntimeError("alert lifecycle write failed")
                        alert_context = read_alert_context(write_conn, site_id, checked_at)
                        wrote = True
                    except Exception as exc:
                        logger.warning(
                            "Alert board persistence failed for site %s; the next site reconnects: %s",
                            site_id,
                            exc,
                        )
                        write_conn.close()
                        write_conn = None
            if not wrote and board_data is not None:
                alert_context = summarize_alert_context(
                    board_data["historical_downtime_by_site"].get(site_id, 0),
                    board_data["open_rows_by_site"].get(site_id, []),
                    board_data["case_numbers_by_instance"],
                    checked_at,
                )
            if primary_ip_backfill_pending:
                alert_context = {
                    **alert_context,
                    "active_alert_instance_count": 0,
                    "current_downtime_seconds": 0,
                    "latest_down_at": None,
                    "active_cases": [],
                    "down_devices": [],
                }
            device_ip_by_key = {
                (device.get("id") or device.get("name") or ""): device_display_ip(device) for device in devices
            }
            current_down_devices = [
                {
                    "device_id": device.get("id") or "",
                    "device_name": device.get("name") or "Unknown",
                    "device_ip": device_display_ip(device),
                    "status": device.get("status") or "",
                    "role": device.get("role") or "",
                    "case_numbers": [],
                    # Only with ALERT_BOARD_SITE_LOCATION_TYPE, for devices below the site (#158).
                    **({"location_path": device["location_path"]} if device.get("location_path") else {}),
                }
                for device in down_devices
            ]
            current_down_device_map = {item["device_id"] or item["device_name"]: item for item in current_down_devices}
            merged_down_devices = []
            seen_down_device_keys: set[str] = set()
            for item in alert_context["down_devices"]:
                item_key = item.get("device_id") or item.get("device_name") or ""
                merged = dict(item)
                current_item = current_down_device_map.get(item_key, {})
                merged.update(
                    {
                        "device_id": current_item.get("device_id", merged.get("device_id", "")),
                        "device_name": current_item.get("device_name", merged.get("device_name", "")),
                        "status": current_item.get("status", merged.get("status", "")),
                        "role": current_item.get("role", merged.get("role", "")),
                    }
                )
                if current_item.get("location_path"):
                    merged["location_path"] = current_item["location_path"]
                merged["device_ip"] = device_ip_by_key.get(item_key, "")
                merged.setdefault("status", "")
                merged.setdefault("role", "")
                merged.setdefault("case_numbers", [])
                merged_down_devices.append(merged)
                if item_key:
                    seen_down_device_keys.add(item_key)
            for item in current_down_devices:
                item_key = item["device_id"] or item["device_name"]
                if item_key in seen_down_device_keys:
                    continue
                merged_down_devices.append(item)
                if item_key:
                    seen_down_device_keys.add(item_key)
            level = (alert.get("level") or "ok").lower()
            summary[level] = summary.get(level, 0) + 1
            if observation_succeeded and site_id:
                observed_levels[site_id] = (loc.get("name") or "", level)
            alerts.append(
                {
                    **loc,
                    # Its own tenant first, then those linked by a Relationship (#238).
                    "tenants": loc.get("tenants") or inventory.site_tenants(loc.get("tenant") or "", []),
                    "alert_level": level,
                    "alert_reason": alert.get("reason", ""),
                    "device_count": len(devices),
                    "down_device_count": len(down_devices),
                    "current_downtime_seconds": alert_context["current_downtime_seconds"],
                    "latest_down_at": alert_context.get("latest_down_at"),
                    "historical_downtime_seconds": alert_context["historical_downtime_seconds"],
                    "active_alert_instance_count": max(
                        int(alert_context["active_alert_instance_count"]),
                        len(merged_down_devices),
                    ),
                    "active_cases": alert_context["active_cases"],
                    "down_devices": merged_down_devices,
                }
            )
    finally:
        if write_conn is not None:
            write_conn.close()

    store_librenms_maps(new_librenms_maps)

    if not persistence_unavailable:
        try:
            record_site_level_changes(observed_levels, timeutil.iso_utc_now())
        except Exception as exc:
            logger.warning("Could not record site alert level changes: %s", exc, exc_info=True)

    alerts.sort(
        key=lambda item: (
            alert_sort_key(item.get("alert_level", "ok")),
            -item.get("down_device_count", 0),
            item.get("name", "").lower(),
        )
    )

    return {
        "checked_at": timeutil.iso_utc_now(),
        "stale_after_seconds": settings.CACHE_TTL,
        "summary": {
            "total": len(alerts),
            **{level: summary.get(level, 0) for level in ALERT_LEVEL_ORDER},
            # No data is not an alert (#124).
            "non_ok": sum(summary.get(level, 0) for level in ALERT_LEVELS_NON_OK),
        },
        "alerts": alerts,
    }


def get_alert_board_data(
    force_refresh: bool = False,
    include_non_operational: bool = False,
) -> dict:
    """Return alert summaries for all locations."""
    cache_key = "alert-board-data:v3"
    if include_non_operational:
        cache_key = f"{cache_key}:include-non-operational"
    sync_enqueued = False
    if force_refresh:
        # "Sync now": incremental, not a full reconcile (#135).  Deletions are
        # still caught by the scheduled full reconcile.
        sync_enqueued = inventory.ensure_snapshot(force=True, full=False, wait=False)
    sync_due, next_update_in_seconds = inventory_update_schedule()
    if not sync_enqueued and sync_due:
        # A sync is due (or none has run yet): start it in the background, so
        # an open board keeps itself up to date (#152).  This request still
        # makes no upstream calls itself.
        sync_enqueued = inventory.ensure_snapshot(wait=False)
    cached = caching.get(cache_key)
    if cached is not None:
        return apply_alert_board_freshness(
            cached, sync_enqueued=sync_enqueued, next_update_in_seconds=next_update_in_seconds
        )

    payload = build_alert_board_payload(
        snapshot_only=True,
        include_non_operational=include_non_operational,
    )
    should_cache = bool(payload.get("alerts")) or inventory.snapshot_initialized()
    if should_cache:
        caching.set(cache_key, payload, timeout=settings.CACHE_TTL)
    return apply_alert_board_freshness(
        payload, sync_enqueued=sync_enqueued, next_update_in_seconds=next_update_in_seconds
    )


LOCATION_ALERTS_CACHE_KEY = "location-alert-levels:v1"


def build_location_alert_levels() -> dict:
    """Score every mapped location from the inventory snapshot (#234).

    The same scoring as the map's site panel (``get_location_detail``): each
    location's own devices, criticality overrides and the LibreNMS status,
    read for all locations at once.  Makes no upstream calls.  Only
    locations with an alert (critical, medium, low) are returned.
    """
    locations = inventory.get_locations(snapshot_only=True)
    devices_by_location: dict[str, list] = {}
    override_map: dict = {}
    conn = db.get_conn()
    if conn is not None:
        try:
            for device in inventory.read_devices(conn=conn):
                devices_by_location.setdefault(device.get("location_id") or "", []).append(device)
            override_map = read_criticality_overrides(conn)
        finally:
            conn.close()
    lnms_devices = None
    lnms_id_map = None
    lnms_index = None
    new_librenms_maps: dict = {}
    if (settings.LIBRENMS_URL or "").strip() and (settings.LIBRENMS_API_TOKEN or "").strip():
        lnms_devices = inventory.read_librenms_devices()
        lnms_id_map = load_librenms_id_map()
        lnms_index = librenms_index(lnms_devices)

    levels = {}
    for loc in locations:
        location_id = loc.get("id") or ""
        devices = devices_by_location.get(location_id)
        if not location_id or not devices:
            continue
        _, alert = get_location_devices_and_alert(
            location_id,
            loc.get("location_type") or None,
            devices_data=devices,
            devices_already_normalized=True,
            lnms_devices=lnms_devices,
            lnms_id_map=lnms_id_map,
            lnms_index=lnms_index,
            new_librenms_maps=new_librenms_maps,
            snapshot_only=True,
            override_map=override_map,
        )
        if alert["level"] in ALERT_LEVELS_NON_OK:
            levels[location_id] = {"level": alert["level"], "reason": alert.get("reason", "")}
    store_librenms_maps(new_librenms_maps)
    return {"checked_at": timeutil.iso_utc_now(), "levels": levels}


def get_location_alert_levels() -> dict:
    """``build_location_alert_levels``, cached like the alert board (``CACHE_TTL``)."""
    cached = caching.get(LOCATION_ALERTS_CACHE_KEY)
    if cached is not None:
        return cached
    payload = build_location_alert_levels()
    if payload["levels"] or inventory.snapshot_initialized():
        caching.set(LOCATION_ALERTS_CACHE_KEY, payload, timeout=settings.CACHE_TTL)
    return payload


def location_field_asns(location_id: str) -> list:
    """Return the ASN stored directly on a location as an ASN-list entry.

    Nautobot 3.x core keeps a single integer ``asn`` field on each Location
    instead of the ``ipam/asns/`` endpoint.  The cached inventory is preferred;
    otherwise the location is looked up once.  Upstream errors propagate.
    """
    asn = None
    cached = [loc for loc in inventory.read_locations(include_without_coordinates=True) if loc.get("id") == location_id]
    if cached:
        asn = cached[0].get("asn")
    else:
        asn = nautobot.get(f"dcim/locations/{location_id}/").get("asn")
    if asn in (None, ""):
        return []
    return [{"asn": asn, "description": "", "tenant": ""}]


def get_location_detail(location_id: str, location_type: str | None = None) -> dict:
    """Fetch detailed info (devices, ASNs, circuits) for a single location.

    Upstream failures raise so the caller can report them.  The one exception
    is a 404 from ``ipam/asns/``: that endpoint does not exist on Nautobot 3.x
    without the BGP Models plugin, so the location's own ``asn`` field is used.
    """
    devices, alert = get_location_devices_and_alert(location_id, location_type)
    try:
        asns_data = nautobot.fetch_all_pages("ipam/asns/", {"location_id": location_id})
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise
        asns = location_field_asns(location_id)
    else:
        asns = [
            {
                "asn": a.get("asn"),
                "description": a.get("description", ""),
                "tenant": nautobot.nested_str(a.get("tenant"), "name", "display"),
            }
            for a in asns_data
        ]
    circuits, circuits_error = location_circuits(location_id)
    return {
        "devices": devices,
        "alert": alert,
        "asns": asns,
        "circuits": circuits,
        "circuits_error": circuits_error,
    }


def circuit_speed(kbps) -> str:
    """Nautobot circuit speeds are in Kbps; show them as ``10 Gbps`` / ``500 Mbps``."""
    try:
        value = int(kbps)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    for unit, size in (("Tbps", 1_000_000_000), ("Gbps", 1_000_000), ("Mbps", 1_000)):
        if value >= size:
            amount = value / size
            return f"{amount:g} {unit}" if amount == int(amount) else f"{amount:.1f} {unit}"
    return f"{value} Kbps"


def location_circuits(location_id: str) -> tuple[list[dict], str]:
    """Return ``(circuits, error)``: every circuit termination at *location_id* (#235).

    Circuits are optional: a failed call gives an empty list and an error
    message, so the rest of the site panel still loads.  A 404 means Nautobot
    has no circuits app; that is not an error.
    """
    try:
        terminations = nautobot.fetch_all_pages("circuits/circuit-terminations/", {"location": location_id, "depth": 2})
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return [], ""
        logger.warning("Could not fetch circuits for location %s: %s", location_id, exc)
        return [], "Circuit information unavailable"
    except Exception as exc:
        logger.warning("Could not fetch circuits for location %s: %s", location_id, exc)
        return [], "Circuit information unavailable"

    circuits = []
    for term in terminations:
        circuit = term.get("circuit") if isinstance(term.get("circuit"), dict) else {}
        circuits.append(
            {
                "id": circuit.get("id") or "",
                "cid": circuit.get("cid") or nautobot.nested_str(circuit, "display") or "Unnamed circuit",
                "provider": nautobot.nested_str(circuit.get("provider"), "name", "display"),
                "circuit_type": nautobot.nested_str(circuit.get("circuit_type"), "name", "display"),
                "status": nautobot.nested_str(circuit.get("status"), "name", "label", "display"),
                "tenant": nautobot.nested_str(circuit.get("tenant"), "name", "display"),
                "commit_rate": circuit_speed(circuit.get("commit_rate")),
                "term_side": term.get("term_side") or "",
                "port_speed": circuit_speed(term.get("port_speed")),
                "upstream_speed": circuit_speed(term.get("upstream_speed")),
                "xconnect_id": term.get("xconnect_id") or "",
                "pp_info": term.get("pp_info") or "",
                "description": term.get("description") or circuit.get("description") or "",
            }
        )
    circuits.sort(key=lambda item: (item["cid"].lower(), item["term_side"]))
    return circuits, ""
