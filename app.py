import json
import logging
import re
import threading
from datetime import UTC, datetime
from functools import wraps

import requests
from flask import Flask, g, jsonify, render_template, request
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from werkzeug.exceptions import HTTPException

from nautobot_maps import caching, db, inventory, librenms, nautobot, settings, timeutil
from nautobot_maps.caching import cache

app = Flask(__name__)
app.secret_key = settings.FLASK_SECRET_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

caching.init_app(app)
nautobot.configure_ssl_warnings()
# Create/migrate the database (no-op without persistence).  Called here, after
# logging is configured, so its startup messages are shown; modules never
# touch the database when they are imported.
db.init_db()


_AUTH_ROLE_LEVELS = {"viewer": 1, "operator": 2, "admin": 3}
_SUPPORTED_AUTH_MODES = {"disabled", "header"}


def _format_set_for_log(values: set[str]) -> str:
    return "{" + ",".join(sorted(values)) + "}"


# In a status exclusion list, this keyword matches a missing or empty status.
_NULL_STATUS_KEYWORD = "null"


def _status_is_excluded(status: str | None, excluded: set[str]) -> bool:
    """Return whether *status* is in *excluded* (lower-cased; ``null`` matches no status)."""
    normalized = (status or "").strip().lower()
    if not normalized:
        return _NULL_STATUS_KEYWORD in excluded
    return normalized in excluded


def _log_alert_board_exclusions() -> None:
    """Log the alert-board configuration once at startup."""
    if settings.LEGACY_SQLITE_DB:
        logger.error(
            "NAUTOBOT_MAPS_DB is set, but SQLite support was removed: the alert board "
            "needs PostgreSQL. Set NAUTOBOT_MAPS_DATABASE_URL=postgresql://... and remove NAUTOBOT_MAPS_DB."
        )
    if not db.dialect():
        logger.warning(
            "No persistence database configured (NAUTOBOT_MAPS_DATABASE_URL): "
            "the alert board will stay empty; the map still works."
        )
    logger.info(
        "Alert board exclusions — statuses=%s, names=%s, types=%s, tags=%s, device statuses=%s; rows=%s",
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_STATUSES),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_NAMES),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_TYPES),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_TAGS),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_DEVICE_STATUSES),
        settings.ALERT_BOARD_SITE_LOCATION_TYPE or "every location",
    )


def _normalize_auth_role(role: str) -> str:
    role = (role or "").strip().lower()
    return role if role in _AUTH_ROLE_LEVELS else ""


def _is_auth_config_valid() -> bool:
    return settings.AUTH_MODE in _SUPPORTED_AUTH_MODES


def _get_flask_run_host() -> str:
    return "127.0.0.1" if settings.AUTH_MODE == "header" else "0.0.0.0"


def _auth_role_level(role: str) -> int:
    return _AUTH_ROLE_LEVELS.get(_normalize_auth_role(role), 0)


def _resolve_role_from_groups(groups: list[str]) -> str:
    normalized_groups = {group.strip().lower() for group in groups if group.strip()}
    if normalized_groups & settings.AUTH_ADMIN_GROUPS:
        return "admin"
    if normalized_groups & settings.AUTH_OPERATOR_GROUPS:
        return "operator"
    if normalized_groups & settings.AUTH_VIEWER_GROUPS:
        return "viewer"
    return settings.AUTH_DEFAULT_ROLE


def _get_current_user() -> dict:
    """Return the current authenticated user context for the request."""
    current = getattr(g, "_current_user", None)
    if current is not None:
        return current

    current = {
        "is_authenticated": False,
        "username": "",
        "groups": [],
        "role": "",
        "auth_mode": settings.AUTH_MODE,
    }
    if settings.AUTH_MODE == "disabled":
        g._current_user = current
        return current

    if settings.AUTH_MODE == "header":
        username = request.headers.get(settings.AUTH_HEADER_USER, "").strip()
        groups_header = request.headers.get(settings.AUTH_HEADER_GROUPS, "")
        groups = [item.strip() for item in re.split(r"[;,]", groups_header) if item.strip()]
        current = {
            "is_authenticated": bool(username),
            "username": username,
            "groups": groups,
            "role": _resolve_role_from_groups(groups),
            "auth_mode": settings.AUTH_MODE,
        }

    g._current_user = current
    return current


def require_role(required_role: str):
    """Allow access when auth is disabled or the current user meets *required_role*."""
    normalized_required_role = _normalize_auth_role(required_role)

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if settings.AUTH_MODE == "disabled":
                return func(*args, **kwargs)
            if not _is_auth_config_valid():
                return jsonify({"error": "Unsupported AUTH_MODE configuration"}), 503

            current_user = _get_current_user()
            if not current_user["is_authenticated"]:
                return jsonify({"error": "Authentication required"}), 401
            if _auth_role_level(current_user["role"]) < _auth_role_level(normalized_required_role):
                return jsonify(
                    {
                        "error": "Insufficient permissions",
                        "required_role": normalized_required_role,
                        "current_role": current_user["role"] or None,
                    }
                ), 403
            return func(*args, **kwargs)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Persistence (PostgreSQL)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# NOC alert helpers
# ---------------------------------------------------------------------------

# Device statuses that count as "down" for alert purposes
_DOWN_STATUSES: frozenset = frozenset({"offline", "failed", "decommissioning"})

# A site is "medium" when more than this share of its monitored devices is down.
_MEDIUM_DOWN_RATIO = 0.25

# Tier definitions shown in the alert-board summary tile (i) tooltips.  They
# describe compute_alert_level() and must be kept in sync with it.
ALERT_STATUS_TIER_DEFINITIONS = {
    "critical": (
        "At least one core device is down (its role matches a critical keyword, "
        "or it is marked critical by an override), or every monitored device is down."
    ),
    "medium": (
        f"More than {_MEDIUM_DOWN_RATIO:.0%} of the site's monitored devices are down and no core device is down."
    ),
    "low": (f"At least one monitored device is down, but {_MEDIUM_DOWN_RATIO:.0%} or fewer and no core device."),
    "no_data": ("The site has no monitored devices, or its state could not be computed. Not counted as an alert."),
    "ok": "The site has monitored devices and none of them is down.",
    "total": ("All sites on the board. Only devices with a Nautobot primary IP are monitored."),
}

# Board order, most severe first (#124).
ALERT_LEVEL_ORDER = ("critical", "medium", "low", "no_data", "ok")
# Levels that count as an active alert in summary["non_ok"].
ALERT_LEVELS_NON_OK = ("critical", "medium", "low")


def _device_has_primary_ip(device: dict) -> bool:
    return bool((device.get("primary_ip") or "").strip())


def _device_display_ip(device: dict) -> str:
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


def _nautobot_inventory_primary_ip_backfill_pending(conn=None) -> bool:
    """Treat primary-IP backfill as pending until Nautobot has synced the current cache version."""
    state = inventory.get_sync_state("nautobot_inventory", conn=conn)
    return inventory.cache_version_mismatch(state) or not bool((state or {}).get("last_successful_sync"))


def _location_is_excluded_from_alert_board(location: dict) -> bool:
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
        or _status_is_excluded(location.get("status"), settings.ALERT_BOARD_EXCLUDED_LOCATION_STATUSES)
        or (location_type and location_type in settings.ALERT_BOARD_EXCLUDED_LOCATION_TYPES)
        or (tags & settings.ALERT_BOARD_EXCLUDED_LOCATION_TAGS)
    )


# ---------------------------------------------------------------------------
# Configurable critical-role keyword system
# ---------------------------------------------------------------------------
# Built-in defaults – used when no overrides are configured.
_DEFAULT_CORE_ROLE_KEYWORDS: tuple = ("core", "spine", "distribution", "router", "gateway")

# CRITICAL_ROLE_KEYWORDS env var (comma-separated) replaces the built-in
# defaults for every location type that has no specific rule in the JSON file.
_env_keywords_raw = settings.CRITICAL_ROLE_KEYWORDS
_ENV_CORE_ROLE_KEYWORDS: tuple = (
    tuple(kw.strip().lower() for kw in _env_keywords_raw.split(",") if kw.strip())
    if _env_keywords_raw
    else _DEFAULT_CORE_ROLE_KEYWORDS
)

# Per-location-type rules loaded from the JSON file (if configured).
# Schema: {"<location_type_lower>": ["kw1", "kw2", ...], "default": [...]}
_CRITICALITY_RULES: dict = {}
if settings.CRITICALITY_RULES_FILE:
    try:
        with open(settings.CRITICALITY_RULES_FILE, encoding="utf-8") as _f:
            _loaded = json.load(_f)
        if isinstance(_loaded, dict):
            _CRITICALITY_RULES = {
                k.lower(): [kw.lower() for kw in v] for k, v in _loaded.items() if isinstance(v, list)
            }
            logger.info(
                "Loaded criticality rules from %s: %s",
                settings.CRITICALITY_RULES_FILE,
                list(_CRITICALITY_RULES.keys()),
            )
        else:
            logger.warning(
                "Criticality rules file %s must contain a JSON object; ignoring.",
                settings.CRITICALITY_RULES_FILE,
            )
    except Exception as exc:
        logger.warning("Could not load criticality rules from %s: %s", settings.CRITICALITY_RULES_FILE, exc)


def _get_critical_keywords(location_type: str | None = None) -> tuple:
    """Return the critical-role keyword set for *location_type*.

    Resolution order:
    1. Per-location-type entry in *_CRITICALITY_RULES* (from the JSON file).
    2. ``"default"`` entry in *_CRITICALITY_RULES*.
    3. *_ENV_CORE_ROLE_KEYWORDS* (from ``CRITICAL_ROLE_KEYWORDS`` env var, or
       the built-in defaults if the env var is not set).
    """
    if location_type and _CRITICALITY_RULES:
        lt_key = location_type.lower()
        if lt_key in _CRITICALITY_RULES:
            return tuple(_CRITICALITY_RULES[lt_key])
        if "default" in _CRITICALITY_RULES:
            return tuple(_CRITICALITY_RULES["default"])
    return _ENV_CORE_ROLE_KEYWORDS


def _read_criticality_overrides(conn, device_ids: list | None = None) -> dict:
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

    core_keywords = _get_critical_keywords(location_type)

    if override_map is None:
        override_map = {}
        conn = db.get_conn()
        if conn is not None:
            try:
                ids = [d.get("id") for d in devices if d.get("id")]
                if ids:
                    override_map = _read_criticality_overrides(conn, ids)
            except Exception as exc:
                logger.debug("Could not read criticality overrides: %s", exc)
            finally:
                conn.close()

    down_names: list = []
    core_down_names: list = []

    for device in devices:
        status = (device.get("status") or "").lower().strip()
        if status not in _DOWN_STATUSES:
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
    if down_count / total > _MEDIUM_DOWN_RATIO:
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


def _load_librenms_id_map() -> dict:
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


def _enrich_with_librenms(
    devices: list,
    lnms_devices: list | None = None,
    lnms_id_map: dict | None = None,
    snapshot_only: bool = False,
) -> list:
    """Merge live LibreNMS status into *devices* (in-place copy returned).

    For each device, LibreNMS is queried by hostname.  The mapping between
    Nautobot device IDs and LibreNMS device IDs is persisted in the
    ``librenms_device_map`` table when the DB is configured.

    LibreNMS ``status`` field: ``1`` = up, ``0`` = down.  When LibreNMS
    reports a device as down but Nautobot has it as active, the status is
    set to ``"offline"`` so ``compute_alert_level`` counts it as down.

    The enrichment is *additive*: Nautobot status is never upgraded (a device
    already offline in Nautobot stays offline regardless of LibreNMS).
    """
    if not (settings.LIBRENMS_URL or "").strip() or not (settings.LIBRENMS_API_TOKEN or "").strip():
        return devices

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

    # Build separate name/IP lookup maps so IP-valued hostnames are never
    # short-name normalized into ambiguous keys such as "10".
    lnms_by_hostname: dict = {}
    lnms_by_ip: dict = {}
    for ld in lnms_devices:
        hostname = (ld.get("hostname") or "").strip()
        if hostname:
            if inventory.is_ip_literal(hostname):
                lnms_by_ip[inventory.librenms_ip_key(hostname)] = ld
            else:
                lnms_by_hostname[inventory.librenms_host_key(hostname)] = ld

    # Load Nautobot UUID → LibreNMS device ID overrides from DB
    if lnms_id_map is None:
        lnms_id_map = _load_librenms_id_map()

    enriched = []
    for device in devices:
        device = dict(device)
        nautobot_id = device.get("id", "")
        lnms_record = None

        # 1. Try the persisted ID mapping first
        if nautobot_id in lnms_id_map:
            entry = lnms_id_map[nautobot_id]
            # Match by LibreNMS device_id
            for ld in lnms_devices:
                if ld.get("device_id") == entry["device_id"]:
                    lnms_record = ld
                    break

        # 2. Fall back to hostname matching
        if lnms_record is None:
            device_name = inventory.librenms_host_key(device.get("name") or "")
            lnms_record = lnms_by_hostname.get(device_name)
            if lnms_record is None:
                primary_ip = inventory.librenms_ip_key(device.get("primary_ip") or "")
                if primary_ip:
                    lnms_record = lnms_by_ip.get(primary_ip)

        if lnms_record is not None:
            device["librenms_hostname"] = lnms_record.get("hostname") or ""
            device["librenms_ip"] = inventory.librenms_polled_ip(lnms_record)
            lnms_status = lnms_record.get("status")
            if lnms_status == 0:
                # LibreNMS says down – mark as offline if not already a down status
                current = (device.get("status") or "").lower()
                if current not in _DOWN_STATUSES:
                    device["status"] = "offline"
                    logger.debug(
                        "LibreNMS enrichment: device %s marked offline (LibreNMS status=0)",
                        device.get("name"),
                    )
            # Persist the mapping if it was resolved by hostname and DB is available
            if nautobot_id and nautobot_id not in lnms_id_map:
                lnms_id = lnms_record.get("device_id")
                lnms_host = lnms_record.get("hostname", "")
                if lnms_id:
                    _store_librenms_map(nautobot_id, lnms_id, lnms_host)

        enriched.append(device)
    return enriched


def _store_librenms_map(nautobot_device_id: str, librenms_device_id: int, librenms_hostname: str) -> None:
    """Upsert a Nautobot ↔ LibreNMS device mapping into the database."""
    conn = db.get_conn()
    if conn is None:
        return
    try:
        with db.transaction(conn):
            p0, p1, p2 = db.placeholders(3).split(",")
            conn.execute(
                f"""
                INSERT INTO librenms_device_map (nautobot_device_id, librenms_device_id, librenms_hostname)
                VALUES ({p0}, {p1}, {p2})
                ON CONFLICT(nautobot_device_id) DO UPDATE SET
                    librenms_device_id = excluded.librenms_device_id,
                    librenms_hostname   = excluded.librenms_hostname
                """,
                (nautobot_device_id, librenms_device_id, librenms_hostname),
            )
    except Exception as exc:
        logger.debug("Could not store librenms_device_map entry: %s", exc)
    finally:
        conn.close()


def _fetch_live_location_devices(location_id: str) -> list:
    """Fetch devices for one location, handling Nautobot filter-key variants."""
    try:
        return nautobot.fetch_all_pages("dcim/devices/", {"location_id": location_id, "depth": 1})
    except requests.HTTPError as exc:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code != 400:
            raise
    return nautobot.fetch_all_pages("dcim/devices/", {"location": location_id, "depth": 1})


def _get_location_devices_and_alert(
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
                devices_data = _fetch_live_location_devices(location_id)

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
            }
            for d in devices_data
        ]
        if use_normalized_devices
        else inventory.normalize_devices(devices_data, lookup_maps=lookup_maps)
    )
    if require_primary_ip:
        devices = [device for device in devices if _device_has_primary_ip(device)]
    if excluded_device_statuses:
        devices = [
            device for device in devices if not _status_is_excluded(device.get("status"), excluded_device_statuses)
        ]

    enriched = _enrich_with_librenms(
        devices,
        lnms_devices=lnms_devices,
        lnms_id_map=lnms_id_map,
        snapshot_only=snapshot_only,
    )
    return enriched, compute_alert_level(enriched, location_type, override_map=override_map)


def _alert_sort_key(level: str) -> int:
    level = (level or "").lower()
    return ALERT_LEVEL_ORDER.index(level) if level in ALERT_LEVEL_ORDER else len(ALERT_LEVEL_ORDER)


def _insert_alert_event(
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


def _resolve_open_alert_instances_for_site(
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
        _insert_alert_event(
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


def _upsert_alert_lifecycle_for_site(
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
                if status not in _DOWN_STATUSES:
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
                            checked_at,
                            checked_at,
                        ),
                    ).fetchone()
                    inserted_id = db.row_to_dict(inserted).get("id")
                    if inserted_id is not None:
                        _insert_alert_event(
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
                p = db.placeholders(6).split(",")
                conn.execute(
                    f"""
                    UPDATE alert_instances
                    SET site_name = {p[0]},
                        device_name = {p[1]},
                        alert_level = {p[2]},
                        alert_reason = {p[3]},
                        last_seen_down_at = {p[4]},
                        updated_at = {now_sql}
                    WHERE id = {p[5]}
                    """,
                    (
                        site.get("name") or "",
                        device.get("name") or "",
                        level,
                        reason,
                        checked_at,
                        current["id"],
                    ),
                )
                if current.get("alert_level") != level or current.get("alert_reason") != reason:
                    _insert_alert_event(
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
            _resolve_open_alert_instances_for_site(conn, site_id, open_alert_keys, checked_at)
        return True
    except Exception as exc:
        logger.warning("Could not persist alert lifecycle for site %s: %s", site_id, exc)
        return False
    finally:
        if owns_conn:
            conn.close()


def _get_case_numbers_for_instance(conn, instance_id: int) -> list[str]:
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


def _get_case_numbers_for_instances(conn, instance_ids: list[int]) -> dict[int, list[str]]:
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


def _empty_alert_context() -> dict:
    return {
        "active_alert_instance_count": 0,
        "historical_downtime_seconds": 0,
        "current_downtime_seconds": 0,
        "active_cases": [],
        "down_devices": [],
    }


def _summarize_alert_context(
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
    for data in open_rows:
        started = timeutil.parse_iso_datetime(data.get("down_started_at"))
        if started is not None:
            elapsed = max(0, int((now_dt - started).total_seconds()))
            historical_seconds += elapsed
            current_downtime_seconds = max(current_downtime_seconds, elapsed)
        case_numbers = case_numbers_by_instance.get(int(data["id"]), [])
        active_cases.update(case_numbers)
        down_devices.append(
            {
                "device_id": data.get("device_id") or "",
                "device_name": data.get("device_name") or "",
                "case_numbers": case_numbers,
            }
        )
    return {
        "active_alert_instance_count": len(down_devices),
        "historical_downtime_seconds": historical_seconds,
        "current_downtime_seconds": current_downtime_seconds,
        "active_cases": sorted(active_cases),
        "down_devices": down_devices,
    }


def _read_alert_context(conn, site_id: str, checked_at: str) -> dict:
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
    case_numbers_by_instance = _get_case_numbers_for_instances(
        conn,
        [int(data["id"]) for data in open_rows if data.get("id") is not None],
    )
    return _summarize_alert_context(historical_seconds, open_rows, case_numbers_by_instance, checked_at)


def _get_alert_context_for_site(site_id: str, checked_at: str, conn=None) -> dict:
    owns_conn = conn is None
    if conn is None:
        conn = db.get_conn()
    if conn is None:
        return _empty_alert_context()
    try:
        return _read_alert_context(conn, site_id, checked_at)
    except Exception as exc:
        logger.debug("Could not load alert context for site %s: %s", site_id, exc)
        return _empty_alert_context()
    finally:
        if owns_conn:
            conn.close()


# A "running" sync state older than this is treated as abandoned (e.g. the
# worker that started it was restarted) rather than still in progress.
_SYNC_RUNNING_STALE_SECONDS = 900


def _nautobot_sync_in_progress() -> bool:
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
    return age_seconds < _SYNC_RUNNING_STALE_SECONDS


def _inventory_update_schedule() -> tuple[bool, int | None]:
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


def _apply_alert_board_freshness(
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
    result["sync_pending"] = bool(sync_enqueued) or _nautobot_sync_in_progress()
    # The board reads only the persisted snapshot, so without a database it is
    # always empty; the UI uses this flag to say why (#136).
    result["persistence_configured"] = bool(db.dialect())
    result["next_update_in_seconds"] = next_update_in_seconds
    return result


_PATH_SEPARATOR = " › "
_logged_rollup_orphans: set[str] = set()


def _roll_up_to_site_locations(
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
        return not include_non_operational and _location_is_excluded_from_alert_board(loc)

    def chain_up(location_id: str) -> list[dict]:
        """*location_id* and its ancestors, nearest first (stops at unknown ids and cycles)."""
        chain, seen = [], set()
        while location_id and location_id in by_id and location_id not in seen:
            seen.add(location_id)
            chain.append(by_id[location_id])
            location_id = by_id[location_id].get("parent_id") or ""
        return chain

    rows = []
    row_ids = set()
    for loc in locations:
        if is_site(loc) and not is_excluded(loc):
            ancestors = chain_up(loc.get("parent_id") or "")
            rows.append({**loc, "ancestor_path": _PATH_SEPARATOR.join(a.get("name", "") for a in reversed(ancestors))})
            row_ids.add(loc["id"])

    devices_by_row: dict[str, list[dict]] = {}
    for location_id, devices in devices_by_location.items():
        chain = chain_up(location_id)
        site_index = next((i for i, loc in enumerate(chain) if is_site(loc)), None)
        if site_index is None:
            # No site above it: the location keeps its own row.
            owner = chain[0] if chain else None
            if owner is None or is_excluded(owner):
                continue
            if owner["id"] not in row_ids:
                rows.append({**owner, "ancestor_path": ""})
                row_ids.add(owner["id"])
                if owner["id"] not in _logged_rollup_orphans:
                    _logged_rollup_orphans.add(owner["id"])
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
        location_path = _PATH_SEPARATOR.join(loc.get("name", "") for loc in reversed(below_site))
        devices_by_row.setdefault(row_id, []).extend({**device, "location_path": location_path} for device in devices)
    return rows, devices_by_row


def _read_alert_board_data(conn) -> dict:
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
        "override_map": _read_criticality_overrides(conn),
        "primary_ip_backfill_pending": _nautobot_inventory_primary_ip_backfill_pending(conn=conn),
        "historical_downtime_by_site": historical_downtime_by_site,
        "open_rows_by_site": open_rows_by_site,
        "case_numbers_by_instance": case_numbers_by_instance,
    }


def _build_alert_board_payload(
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
        locations = [loc for loc in locations if not _location_is_excluded_from_alert_board(loc)]
    alerts = []
    summary = dict.fromkeys(ALERT_LEVEL_ORDER, 0)
    lnms_devices = None
    lnms_id_map = None
    if (settings.LIBRENMS_URL or "").strip() and (settings.LIBRENMS_API_TOKEN or "").strip():
        try:
            lnms_devices = inventory.read_librenms_devices()
            if not lnms_devices and not snapshot_only:
                inventory.ensure_snapshot(force=True, wait=True)
                lnms_devices = inventory.read_librenms_devices()
            lnms_id_map = _load_librenms_id_map()
        except Exception as exc:
            logger.warning("Could not refresh LibreNMS inventory for alert board: %s", exc)
            lnms_devices = []
            lnms_id_map = {}

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
            board_data = _read_alert_board_data(read_conn)
        except Exception as exc:
            logger.warning("Could not bulk-read alert board data; reading per site instead: %s", exc)
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
        locations, devices_by_row = _roll_up_to_site_locations(
            all_locations,
            devices_by_location,
            settings.ALERT_BOARD_SITE_LOCATION_TYPE,
            include_non_operational=include_non_operational,
        )

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
                devices, alert = _get_location_devices_and_alert(
                    loc["id"],
                    loc.get("location_type") or None,
                    lnms_devices=lnms_devices,
                    lnms_id_map=lnms_id_map,
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

            down_devices = [d for d in devices if (d.get("status") or "").lower().strip() in _DOWN_STATUSES]
            checked_at = timeutil.iso_utc_now()
            alert_context = _empty_alert_context()
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
                            primary_ip_backfill_pending = _nautobot_inventory_primary_ip_backfill_pending(
                                conn=write_conn
                            )
                        if observation_succeeded and not primary_ip_backfill_pending:
                            if (
                                _upsert_alert_lifecycle_for_site(loc, devices, alert, checked_at, conn=write_conn)
                                is False
                            ):
                                raise RuntimeError("alert lifecycle write failed")
                        alert_context = _read_alert_context(write_conn, site_id, checked_at)
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
                alert_context = _summarize_alert_context(
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
                    "active_cases": [],
                    "down_devices": [],
                }
            device_ip_by_key = {
                (device.get("id") or device.get("name") or ""): _device_display_ip(device) for device in devices
            }
            current_down_devices = [
                {
                    "device_id": device.get("id") or "",
                    "device_name": device.get("name") or "Unknown",
                    "device_ip": _device_display_ip(device),
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
            alerts.append(
                {
                    **loc,
                    "alert_level": level,
                    "alert_reason": alert.get("reason", ""),
                    "device_count": len(devices),
                    "down_device_count": len(down_devices),
                    "current_downtime_seconds": alert_context["current_downtime_seconds"],
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

    alerts.sort(
        key=lambda item: (
            _alert_sort_key(item.get("alert_level", "ok")),
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
    sync_due, next_update_in_seconds = _inventory_update_schedule()
    if not sync_enqueued and sync_due:
        # A sync is due (or none has run yet): start it in the background, so
        # an open board keeps itself up to date (#152).  This request still
        # makes no upstream calls itself.
        sync_enqueued = inventory.ensure_snapshot(wait=False)
    cached = caching.get(cache_key)
    if cached is not None:
        return _apply_alert_board_freshness(
            cached, sync_enqueued=sync_enqueued, next_update_in_seconds=next_update_in_seconds
        )

    payload = _build_alert_board_payload(
        snapshot_only=True,
        include_non_operational=include_non_operational,
    )
    should_cache = bool(payload.get("alerts")) or inventory.snapshot_initialized()
    if should_cache:
        caching.set(cache_key, payload, timeout=settings.CACHE_TTL)
    return _apply_alert_board_freshness(
        payload, sync_enqueued=sync_enqueued, next_update_in_seconds=next_update_in_seconds
    )


# ---------------------------------------------------------------------------
# Background scheduler (#154)
# ---------------------------------------------------------------------------
# Every app process runs one scheduler thread; a PostgreSQL advisory lock
# makes sure only one of them (across all workers and containers) works per
# tick.  It runs the syncs that are due and, when one ran, rebuilds the alert
# board, which records alert history.  Without it, syncs and history only
# happened while someone had a page open.
_SCHEDULER_MAX_TICK_SECONDS = 30
_scheduler_started = False
_scheduler_start_lock = threading.Lock()
_scheduler_stop = threading.Event()


def _scheduler_tick_seconds() -> int:
    """Seconds between ticks: often enough to catch a due sync promptly."""
    intervals = [settings.INVENTORY_SYNC_INTERVAL_SECONDS]
    if (settings.LIBRENMS_URL or "").strip() and (settings.LIBRENMS_API_TOKEN or "").strip():
        intervals.append(settings.LIBRENMS_SYNC_INTERVAL_SECONDS)
    return max(1, min(_SCHEDULER_MAX_TICK_SECONDS, *intervals))


def _scheduler_tick() -> bool:
    """Run the due syncs and rebuild the board if one ran.  Returns whether it did work."""
    release = db.try_advisory_lock("background_scheduler")
    if not callable(release):
        return False  # no database, or another process holds the tick
    try:
        if not inventory.ensure_snapshot(wait=True):
            return False
        # The syncs invalidated the cached board; rebuild it now so alert
        # history is recorded even if nobody opens the board.
        payload = _build_alert_board_payload(snapshot_only=True)
        caching.set("alert-board-data:v3", payload, timeout=settings.CACHE_TTL)
        return True
    finally:
        release()


def _scheduler_loop() -> None:
    while not _scheduler_stop.is_set():
        try:
            _scheduler_tick()
        except Exception as exc:
            logger.warning("Background scheduler tick failed: %s", exc)
        _scheduler_stop.wait(_scheduler_tick_seconds())


def start_background_scheduler() -> bool:
    """Start this process's scheduler thread once.  Returns whether it runs."""
    global _scheduler_started
    if (
        not settings.BACKGROUND_SYNC_ENABLED
        or not db.dialect()
        or not (settings.NAUTOBOT_URL and settings.NAUTOBOT_TOKEN)
    ):
        return False
    with _scheduler_start_lock:
        if _scheduler_started:
            return True
        _scheduler_started = True
        _scheduler_stop.clear()
        threading.Thread(target=_scheduler_loop, name="background-scheduler", daemon=True).start()
    logger.info("Background scheduler started (tick every %ss)", _scheduler_tick_seconds())
    return True


def _location_field_asns(location_id: str) -> list:
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
    """Fetch detailed info (devices, prefixes, ASNs) for a single location.

    Upstream failures raise so the caller can report them.  The one exception
    is a 404 from ``ipam/asns/``: that endpoint does not exist on Nautobot 3.x
    without the BGP Models plugin, so the location's own ``asn`` field is used.
    """
    devices, alert = _get_location_devices_and_alert(location_id, location_type)
    try:
        asns_data = nautobot.fetch_all_pages("ipam/asns/", {"location_id": location_id})
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise
        asns = _location_field_asns(location_id)
    else:
        asns = [
            {
                "asn": a.get("asn"),
                "description": a.get("description", ""),
                "tenant": nautobot.nested_str(a.get("tenant"), "name", "display"),
            }
            for a in asns_data
        ]
    return {"devices": devices, "alert": alert, "asns": asns}


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


def _wants_json():
    """Return True when the client prefers a JSON response."""
    return (
        request.path.startswith("/api/")
        or request.accept_mimetypes.best_match(["application/json", "text/html"]) == "application/json"
    )


@app.errorhandler(404)
def page_not_found(exc):
    if _wants_json():
        return jsonify({"error": "Not found"}), 404
    return (
        render_template(
            "error.html",
            error_code=404,
            error_title="Page Not Found",
            error_message="The page you are looking for does not exist. Check the URL or head back to the map.",
        ),
        404,
    )


@app.errorhandler(405)
def method_not_allowed(exc):
    if _wants_json():
        return jsonify({"error": "Method not allowed"}), 405
    return (
        render_template(
            "error.html",
            error_code=405,
            error_title="Method Not Allowed",
            error_message="The HTTP method used is not allowed for this URL.",
        ),
        405,
    )


@app.errorhandler(500)
def internal_server_error(exc):
    if _wants_json():
        return jsonify({"error": "Internal server error"}), 500
    return (
        render_template(
            "error.html",
            error_code=500,
            error_title="Internal Server Error",
            error_message="Something went wrong on our end. Please try again later.",
        ),
        500,
    )


@app.errorhandler(HTTPException)
def api_http_error(exc):
    if _wants_json():
        status_code = exc.code or 500
        if status_code == 404:
            message = "Not found"
        elif status_code == 405:
            message = "Method not allowed"
        elif status_code == 500:
            message = "Internal server error"
        else:
            message = exc.description or "Request failed"
        return jsonify({"error": message}), status_code
    return exc


@app.errorhandler(Exception)
def api_unhandled_error(exc):
    logger.error("Unhandled application error: %s", exc)
    if _wants_json():
        return jsonify({"error": "Internal server error"}), 500
    return internal_server_error(exc)


def _nautobot_service_unavailable(context: str, exc: Exception):
    logger.warning("%s: %s", context, exc)
    return jsonify({"error": "Nautobot service unavailable"}), 503


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html", nautobot_url=settings.NAUTOBOT_URL)


@app.route("/healthz")
def healthz():
    """Liveness probe for Docker, load balancers and monitoring.

    Makes no Nautobot or LibreNMS calls: an upstream outage must not mark this
    app unhealthy, because restarting it cannot fix the upstream.  When
    persistence is configured the database must answer ``SELECT 1``, since the
    alert board cannot be served without it.

    Returns 200 ``{"status": "ok", "checks": {...}}`` or 503 with
    ``"status": "unavailable"``.  Error details are logged, never returned.
    """
    checks = {"app": "ok"}
    if db.dialect():
        conn = None
        try:
            conn = db.get_conn()
            conn.execute("SELECT 1").fetchone()
            checks["database"] = "ok"
        except Exception as exc:
            logger.warning("Health check: database unavailable: %s", exc)
            checks["database"] = "unavailable"
        finally:
            if conn is not None:
                conn.close()
    healthy = all(value == "ok" for value in checks.values())
    return (
        jsonify({"status": "ok" if healthy else "unavailable", "checks": checks}),
        200 if healthy else 503,
    )


@app.route("/alerts")
def alert_board():
    return render_template(
        "alerts.html",
        nautobot_url=settings.NAUTOBOT_URL,
        tier_definitions=ALERT_STATUS_TIER_DEFINITIONS,
    )


@app.route("/api/locations")
def api_locations():
    """Return all Nautobot locations that have GPS coordinates."""
    try:
        locations = inventory.get_locations()
        return jsonify({"locations": locations})
    except RuntimeError as exc:
        return _nautobot_service_unavailable("Locations endpoint unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.error("Unexpected error fetching locations: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/locations/<location_id>/detail")
def api_location_detail(location_id: str):
    """Return devices and ASNs for a specific location.

    Optional query parameter:
      location_type – the location type name (e.g. "Data Center", "Office").
        When provided, the criticality keyword set is resolved from the
        location-type-scoped rules configured via ``CRITICALITY_RULES_FILE``.
    """
    location_type = request.args.get("location_type", "").strip() or None
    try:
        detail = get_location_detail(location_id, location_type=location_type)
        return jsonify(detail)
    except RuntimeError as exc:
        return _nautobot_service_unavailable("Location detail endpoint unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.error("Unexpected error fetching location detail: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/alerts")
def api_alerts():
    """Return alert-board summaries for all Nautobot locations."""
    force_refresh = request.args.get("refresh", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "refresh",
    }
    include_non_operational = request.args.get(
        "include_non_operational",
        "",
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "include",
    }
    try:
        return jsonify(
            get_alert_board_data(
                force_refresh=force_refresh,
                include_non_operational=include_non_operational,
            )
        )
    except RuntimeError:
        return (
            jsonify({"error": "Alert board unavailable because Nautobot is not configured"}),
            503,
        )
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error while building alert board: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.error("Unexpected error building alert board: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/search")
def api_search():
    """
    Geocode an address or parse GPS coordinates and return all Nautobot
    locations within 5 km, sorted by distance.

    Query parameters:
      q  – address string  OR  "lat,lon" coordinate pair
    """
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Missing query parameter 'q'"}), 400

    # Try to parse as raw GPS coordinates first
    lat = lon = None
    parts = query.split(",")
    if len(parts) == 2:
        try:
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
        except ValueError:
            lat = lon = None

    # Fall back to geocoding
    if lat is None or lon is None:
        try:
            geolocator = Nominatim(user_agent="nautobot-maps/1.0")
            location = geolocator.geocode(query, timeout=10)
            if location is None:
                return jsonify({"error": f"Address not found: {query}"}), 404
            lat = location.latitude
            lon = location.longitude
        except Exception as exc:
            logger.error("Geocoding error: %s", exc)
            return jsonify({"error": "Geocoding service unavailable"}), 503

    # Find locations within 5 km
    try:
        all_locations = inventory.get_locations()
    except RuntimeError as exc:
        return _nautobot_service_unavailable("Location search unavailable", exc)
    except Exception as exc:
        logger.error("Error fetching locations for search: %s", exc)
        return jsonify({"error": "Internal server error"}), 500

    search_point = (lat, lon)
    nearby = []
    for loc in all_locations:
        loc_point = (loc["latitude"], loc["longitude"])
        dist_km = geodesic(search_point, loc_point).kilometers
        if dist_km <= 5.0:
            nearby.append({**loc, "distance_km": round(dist_km, 3)})

    nearby.sort(key=lambda x: x["distance_km"])

    return jsonify(
        {
            "search_lat": lat,
            "search_lon": lon,
            "radius_km": 5,
            "count": len(nearby),
            "locations": nearby,
        }
    )


# ---------------------------------------------------------------------------
# Criticality override REST endpoints
# ---------------------------------------------------------------------------


@app.route("/api/criticality-overrides", methods=["GET"])
@require_role("operator")
def api_list_criticality_overrides():
    """Return all per-device criticality overrides stored in the DB.

    Returns 503 when the persistence DB is not configured.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        rows = conn.execute(
            "SELECT nautobot_device_id, is_critical, reason, updated_by, updated_at "
            "FROM device_criticality_override ORDER BY updated_at DESC"
        ).fetchall()
        return jsonify({"overrides": [db.row_to_dict(r) for r in rows]})
    except Exception as exc:
        logger.error("Could not list criticality overrides: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


@app.route("/api/criticality-overrides", methods=["POST"])
@require_role("operator")
def api_set_criticality_override():
    """Create or update a per-device criticality override.

    Expected JSON body::

        {
            "nautobot_device_id": "<uuid>",
            "is_critical": true | false,
            "reason": "optional explanation",
            "updated_by": "operator-name"
        }

    Returns 503 when the DB is not configured.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    body = request.get_json(silent=True) or {}
    device_id = (body.get("nautobot_device_id") or "").strip()
    if not device_id:
        conn.close()
        return jsonify({"error": "nautobot_device_id is required"}), 400
    is_critical = bool(body.get("is_critical", True))
    reason = (body.get("reason") or "").strip()
    updated_by = (body.get("updated_by") or "").strip()
    if not updated_by:
        updated_by = _get_current_user().get("username", "")
    try:
        with db.transaction(conn):
            p0, p1, p2, p3 = db.placeholders(4).split(",")
            now_sql = db.sql_now()
            conn.execute(
                f"""
                INSERT INTO device_criticality_override
                    (nautobot_device_id, is_critical, reason, updated_by, updated_at)
                VALUES ({p0}, {p1}, {p2}, {p3}, {now_sql})
                ON CONFLICT(nautobot_device_id) DO UPDATE SET
                    is_critical = excluded.is_critical,
                    reason      = excluded.reason,
                    updated_by  = excluded.updated_by,
                    updated_at  = excluded.updated_at
                """,
                (device_id, int(is_critical), reason, updated_by),
            )
        return jsonify({"status": "ok", "nautobot_device_id": device_id, "is_critical": is_critical})
    except Exception as exc:
        logger.error("Could not set criticality override: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


@app.route("/api/criticality-overrides/<device_id>", methods=["DELETE"])
@require_role("operator")
def api_delete_criticality_override(device_id: str):
    """Delete a per-device criticality override.

    Returns 404 if no override exists for the given device ID.
    Returns 503 when the DB is not configured.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        with db.transaction(conn):
            marker = db.placeholders(1)
            cur = conn.execute(
                f"DELETE FROM device_criticality_override WHERE nautobot_device_id = {marker}",
                (device_id,),
            )
        if cur.rowcount == 0:
            return jsonify({"error": "Override not found"}), 404
        return jsonify({"status": "deleted", "nautobot_device_id": device_id})
    except Exception as exc:
        logger.error("Could not delete criticality override: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Alert lifecycle / case tracking endpoints
# ---------------------------------------------------------------------------


@app.route("/api/alert-history", methods=["GET"])
@require_role("operator")
def api_alert_history():
    """Return historical alert instances with events and case numbers."""
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    site_id = (request.args.get("site_id") or "").strip()
    device_id = (request.args.get("device_id") or "").strip()
    start_at = (request.args.get("start_at") or "").strip()
    end_at = (request.args.get("end_at") or "").strip()
    try:
        if start_at:
            parsed_start_at = timeutil.parse_iso_datetime(start_at)
            if parsed_start_at is None:
                return jsonify({"error": "start_at must be an ISO-8601 timestamp"}), 400
            if parsed_start_at.tzinfo is None:
                parsed_start_at = parsed_start_at.replace(tzinfo=UTC)
            start_at = parsed_start_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if end_at:
            parsed_end_at = timeutil.parse_iso_datetime(end_at)
            if parsed_end_at is None:
                return jsonify({"error": "end_at must be an ISO-8601 timestamp"}), 400
            if parsed_end_at.tzinfo is None:
                parsed_end_at = parsed_end_at.replace(tzinfo=UTC)
            end_at = parsed_end_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        conditions = []
        params = []
        if site_id:
            conditions.append(f"site_id = {db.placeholders(1)}")
            params.append(site_id)
        if device_id:
            conditions.append(f"device_id = {db.placeholders(1)}")
            params.append(device_id)
        if start_at:
            conditions.append(f"created_at >= {db.placeholders(1)}")
            params.append(start_at)
        if end_at:
            conditions.append(f"created_at <= {db.placeholders(1)}")
            params.append(end_at)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"""
            SELECT id, alert_key, site_id, site_name, device_id, device_name, alert_level,
                   alert_reason, status, down_started_at, last_seen_down_at, resolved_at,
                   total_downtime_seconds, created_at, updated_at
            FROM alert_instances
            {where_clause}
            ORDER BY id DESC
            LIMIT 500
            """,
            tuple(params),
        ).fetchall()
        instances = [db.row_to_dict(row) for row in rows]
        instance_ids = [row["id"] for row in instances if row.get("id") is not None]
        events_by_instance: dict[str, list[dict]] = {str(instance_id): [] for instance_id in instance_ids}
        cases_by_instance: dict[str, list[dict]] = {str(instance_id): [] for instance_id in instance_ids}
        if instance_ids:
            markers = db.placeholders(len(instance_ids))
            ev_rows = conn.execute(
                f"""
                SELECT alert_instance_id, event_type, event_at, alert_level, alert_reason, snapshot_json
                FROM alert_events
                WHERE alert_instance_id IN ({markers})
                ORDER BY alert_instance_id ASC, id ASC
                """,
                tuple(instance_ids),
            ).fetchall()
            case_rows = conn.execute(
                f"""
                SELECT alert_instance_id, case_number, created_by, created_at
                FROM alert_cases
                WHERE alert_instance_id IN ({markers})
                ORDER BY alert_instance_id ASC, id DESC
                """,
                tuple(instance_ids),
            ).fetchall()
            for ev_row in ev_rows:
                event = db.row_to_dict(ev_row)
                instance_id = str(event.pop("alert_instance_id"))
                try:
                    event["snapshot"] = json.loads(event.pop("snapshot_json", "{}") or "{}")
                except Exception:
                    event["snapshot"] = {}
                events_by_instance.setdefault(instance_id, []).append(event)
            for case_row in case_rows:
                case_data = db.row_to_dict(case_row)
                instance_id = str(case_data.pop("alert_instance_id"))
                cases_by_instance.setdefault(instance_id, []).append(case_data)
        for instance in instances:
            instance_id = str(instance.get("id"))
            instance["events"] = events_by_instance.get(instance_id, [])
            instance["cases"] = cases_by_instance.get(instance_id, [])
        return jsonify({"instances": instances})
    except Exception as exc:
        logger.error("Could not fetch alert history: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


# Upper bound on devices linked to one case in a single request.
_MAX_CASE_DEVICES = 200


@app.route("/api/alert-cases", methods=["POST"])
@require_role("operator")
def api_add_alert_case():
    """Attach a case number to the open alert of one or more devices at a site.

    JSON body: ``site_id``, ``case_number`` and either ``device_ids`` (list)
    or the single ``device_id``.  All-or-nothing: if any device has no open
    alert, nothing is written and the response is 404 with
    ``missing_device_ids``, so a case is never applied to only part of the
    selection.
    """
    body = request.get_json(silent=True) or {}
    site_id = (body.get("site_id") or "").strip() if isinstance(body.get("site_id"), str) else ""
    case_number = (body.get("case_number") or "").strip() if isinstance(body.get("case_number"), str) else ""
    raw_ids = body.get("device_ids")
    if raw_ids is None:
        raw_ids = [body.get("device_id")] if body.get("device_id") else []
    if not isinstance(raw_ids, list) or not all(isinstance(value, str) for value in raw_ids):
        return jsonify({"error": "device_ids must be a list of device ID strings"}), 400
    # De-duplicate while keeping the operator's order.
    device_ids = list(dict.fromkeys(value.strip() for value in raw_ids if value.strip()))
    if not site_id or not device_ids or not case_number:
        return jsonify({"error": "site_id, device_ids and case_number are required"}), 400
    if len(device_ids) > _MAX_CASE_DEVICES:
        return jsonify({"error": f"At most {_MAX_CASE_DEVICES} devices per request"}), 400

    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    created_by = (_get_current_user().get("username") or "").strip()
    try:
        with db.transaction(conn):
            instance_ids: dict[str, int] = {}
            for device_id in device_ids:
                p0, p1 = db.placeholders(2).split(",")
                row = conn.execute(
                    f"""
                    SELECT id
                    FROM alert_instances
                    WHERE site_id = {p0} AND device_id = {p1} AND status = 'open'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (site_id, device_id),
                ).fetchone()
                if row is not None:
                    instance_ids[device_id] = db.row_to_dict(row)["id"]
            missing = [device_id for device_id in device_ids if device_id not in instance_ids]
            if missing:
                return (
                    jsonify(
                        {
                            "error": "No active alert found for some devices; nothing was changed",
                            "missing_device_ids": missing,
                        }
                    ),
                    404,
                )
            for device_id in device_ids:
                p0, p1, p2 = db.placeholders(3).split(",")
                conn.execute(
                    f"""
                    INSERT INTO alert_cases (alert_instance_id, case_number, created_by, created_at)
                    VALUES ({p0}, {p1}, {p2}, {db.sql_now()})
                    ON CONFLICT(alert_instance_id, case_number) DO NOTHING
                    """,
                    (instance_ids[device_id], case_number, created_by),
                )
        # The board payload embeds case numbers; drop it so the new case shows.
        caching.invalidate_alert_board()
        result = {
            "status": "ok",
            "site_id": site_id,
            "case_number": case_number,
            "linked": [
                {"device_id": device_id, "alert_instance_id": instance_ids[device_id]} for device_id in device_ids
            ],
        }
        if len(device_ids) == 1:
            # Fields of the original single-device response, kept for API clients.
            result["device_id"] = device_ids[0]
            result["alert_instance_id"] = instance_ids[device_ids[0]]
        return jsonify(result)
    except Exception as exc:
        logger.error("Could not add alert case: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Roles proxy endpoints
# ---------------------------------------------------------------------------


@app.route("/api/roles", methods=["GET"])
def api_list_roles():
    """Return all roles from Nautobot (proxied from extras/roles/)."""
    try:
        roles = nautobot.fetch_all_pages("extras/roles/")
        return jsonify({"roles": roles})
    except RuntimeError as exc:
        return _nautobot_service_unavailable("Roles listing unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.error("Unexpected error listing roles: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/roles", methods=["POST"])
@require_role("admin")
def api_create_role():
    """Create a new role in Nautobot (proxied to extras/roles/).

    Expected JSON body follows the Nautobot Role schema, e.g.::

        {"name": "Core Router", "color": "aa1409", "content_types": [...]}
    """
    body = request.get_json(silent=True) or {}
    if not body.get("name"):
        return jsonify({"error": "name is required"}), 400
    try:
        created = nautobot.post("extras/roles/", body)
        cache.delete_memoized(nautobot.fetch_all_pages)
        return jsonify(created), 201
    except RuntimeError as exc:
        return _nautobot_service_unavailable("Role creation unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        try:
            detail = exc.response.json()
        except Exception:
            detail = "Could not parse Nautobot error response"
        return jsonify({"error": "Failed to communicate with Nautobot API", "detail": detail}), exc.response.status_code
    except Exception as exc:
        logger.error("Unexpected error creating role: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/roles/<role_id>", methods=["DELETE"])
@require_role("admin")
def api_delete_role(role_id: str):
    """Delete a role from Nautobot by its UUID (proxied to extras/roles/<id>/)."""
    try:
        nautobot.delete(f"extras/roles/{role_id}/")
        cache.clear()
        return jsonify({"status": "deleted", "id": role_id})
    except RuntimeError as exc:
        return _nautobot_service_unavailable("Role deletion unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        if exc.response.status_code == 404:
            return jsonify({"error": "Role not found"}), 404
        return jsonify({"error": "Failed to communicate with Nautobot API"}), exc.response.status_code
    except Exception as exc:
        logger.error("Unexpected error deleting role: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# Location-type proxy endpoints
# ---------------------------------------------------------------------------


@app.route("/api/location-types", methods=["GET"])
def api_list_location_types():
    """Return all location types from Nautobot (proxied from dcim/location-types/)."""
    try:
        location_types = nautobot.fetch_all_pages("dcim/location-types/")
        return jsonify({"location_types": location_types})
    except RuntimeError as exc:
        return _nautobot_service_unavailable("Location type listing unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.error("Unexpected error listing location types: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/location-types", methods=["POST"])
@require_role("admin")
def api_create_location_type():
    """Create a new location type in Nautobot (proxied to dcim/location-types/).

    Expected JSON body follows the Nautobot LocationType schema, e.g.::

        {"name": "Data Center", "slug": "data-center"}
    """
    body = request.get_json(silent=True) or {}
    if not body.get("name"):
        return jsonify({"error": "name is required"}), 400
    try:
        created = nautobot.post("dcim/location-types/", body)
        cache.clear()
        return jsonify(created), 201
    except RuntimeError as exc:
        return _nautobot_service_unavailable("Location type creation unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        try:
            detail = exc.response.json()
        except Exception:
            detail = "Could not parse Nautobot error response"
        return jsonify({"error": "Failed to communicate with Nautobot API", "detail": detail}), exc.response.status_code
    except Exception as exc:
        logger.error("Unexpected error creating location type: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/location-types/<lt_id>", methods=["DELETE"])
@require_role("admin")
def api_delete_location_type(lt_id: str):
    """Delete a location type from Nautobot by its UUID (proxied to dcim/location-types/<id>/)."""
    try:
        nautobot.delete(f"dcim/location-types/{lt_id}/")
        cache.clear()
        return jsonify({"status": "deleted", "id": lt_id})
    except RuntimeError as exc:
        return _nautobot_service_unavailable("Location type deletion unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        if exc.response.status_code == 404:
            return jsonify({"error": "Location type not found"}), 404
        return jsonify({"error": "Failed to communicate with Nautobot API"}), exc.response.status_code
    except Exception as exc:
        logger.error("Unexpected error deleting location type: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    debug = settings.FLASK_DEBUG
    try:
        port = int(settings.FLASK_RUN_PORT or 5000)
    except (ValueError, TypeError):
        port = 5000
    _log_alert_board_exclusions()
    start_background_scheduler()
    app.run(host=_get_flask_run_host(), port=port, debug=debug)
