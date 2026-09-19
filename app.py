import json
import os
import sqlite3
import logging
import re
from datetime import datetime, timezone
from functools import wraps

import requests
from flask import Flask, render_template, jsonify, request, g
from flask_caching import Cache
from dotenv import load_dotenv
from geopy.distance import geodesic
from geopy.geocoders import Nominatim

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-to-a-random-string")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NAUTOBOT_URL = os.getenv("NAUTOBOT_URL", "").rstrip("/")
NAUTOBOT_TOKEN = os.getenv("NAUTOBOT_TOKEN", "")
NAUTOBOT_API_VERSION = os.getenv("NAUTOBOT_API_VERSION", "").strip()
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))

# LibreNMS optional integration
LIBRENMS_URL = os.getenv("LIBRENMS_URL", "").rstrip("/")
LIBRENMS_API_TOKEN = os.getenv("LIBRENMS_API_TOKEN", "")

# SQLite database path (leave empty to disable persistence features)
NAUTOBOT_MAPS_DB = os.getenv("NAUTOBOT_MAPS_DB", "")

# Optional authentication / RBAC configuration
AUTH_MODE = os.getenv("AUTH_MODE", "disabled").strip().lower() or "disabled"
AUTH_HEADER_USER = os.getenv("AUTH_HEADER_USER", "X-Forwarded-User").strip() or "X-Forwarded-User"
AUTH_HEADER_GROUPS = os.getenv("AUTH_HEADER_GROUPS", "X-Forwarded-Groups").strip() or "X-Forwarded-Groups"
AUTH_DEFAULT_ROLE = os.getenv("AUTH_DEFAULT_ROLE", "").strip().lower()

# Path to a JSON file with per-location-type criticality keyword rules
CRITICALITY_RULES_FILE = os.getenv("CRITICALITY_RULES_FILE", "")

# Flask-Caching configuration.
# Defaults to SimpleCache (in-process) for development / single-worker setups.
# Set CACHE_TYPE=RedisCache and CACHE_REDIS_URL=redis://redis:6379/0 in
# production to share cache across multiple Gunicorn workers.
app.config["CACHE_TYPE"] = os.getenv("CACHE_TYPE", "SimpleCache")
app.config["CACHE_DEFAULT_TIMEOUT"] = CACHE_TTL
_redis_url = os.getenv("CACHE_REDIS_URL", "")
if _redis_url:
    app.config["CACHE_REDIS_URL"] = _redis_url
cache = Cache(app)

# SSL verification: "true" (default) = verify, "false" = skip verification,
# or a file path to a custom CA bundle.
_ssl_env = os.getenv("NAUTOBOT_VERIFY_SSL", "true").strip()
if _ssl_env.lower() == "false":
    NAUTOBOT_VERIFY_SSL: bool | str = False
elif _ssl_env.lower() == "true":
    NAUTOBOT_VERIFY_SSL = True
else:
    # Treat the value as a path to a CA bundle / certificate file
    NAUTOBOT_VERIFY_SSL = _ssl_env


_AUTH_ROLE_LEVELS = {"viewer": 1, "operator": 2, "admin": 3}
_SUPPORTED_AUTH_MODES = {"disabled", "header"}


def _parse_csv_set(value: str) -> set[str]:
    """Return a lower-cased set from a comma/semicolon-separated string."""
    return {
        item.strip().lower()
        for item in re.split(r"[;,]", value or "")
        if item.strip()
    }


AUTH_VIEWER_GROUPS = _parse_csv_set(os.getenv("AUTH_VIEWER_GROUPS", ""))
AUTH_OPERATOR_GROUPS = _parse_csv_set(os.getenv("AUTH_OPERATOR_GROUPS", ""))
AUTH_ADMIN_GROUPS = _parse_csv_set(os.getenv("AUTH_ADMIN_GROUPS", ""))


def _normalize_auth_role(role: str) -> str:
    role = (role or "").strip().lower()
    return role if role in _AUTH_ROLE_LEVELS else ""


AUTH_DEFAULT_ROLE = _normalize_auth_role(AUTH_DEFAULT_ROLE)


def _is_auth_config_valid() -> bool:
    return AUTH_MODE in _SUPPORTED_AUTH_MODES


def _get_flask_run_host() -> str:
    return "127.0.0.1" if AUTH_MODE == "header" else "0.0.0.0"


def _auth_role_level(role: str) -> int:
    return _AUTH_ROLE_LEVELS.get(_normalize_auth_role(role), 0)


def _resolve_role_from_groups(groups: list[str]) -> str:
    normalized_groups = {group.strip().lower() for group in groups if group.strip()}
    if normalized_groups & AUTH_ADMIN_GROUPS:
        return "admin"
    if normalized_groups & AUTH_OPERATOR_GROUPS:
        return "operator"
    if normalized_groups & AUTH_VIEWER_GROUPS:
        return "viewer"
    return AUTH_DEFAULT_ROLE


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
        "auth_mode": AUTH_MODE,
    }
    if AUTH_MODE == "disabled":
        g._current_user = current
        return current

    if AUTH_MODE == "header":
        username = request.headers.get(AUTH_HEADER_USER, "").strip()
        groups_header = request.headers.get(AUTH_HEADER_GROUPS, "")
        groups = [item.strip() for item in re.split(r"[;,]", groups_header) if item.strip()]
        current = {
            "is_authenticated": bool(username),
            "username": username,
            "groups": groups,
            "role": _resolve_role_from_groups(groups),
            "auth_mode": AUTH_MODE,
        }

    g._current_user = current
    return current


def require_role(required_role: str):
    """Allow access when auth is disabled or the current user meets *required_role*."""
    normalized_required_role = _normalize_auth_role(required_role)

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if AUTH_MODE == "disabled":
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
# SQLite persistence (optional – only active when NAUTOBOT_MAPS_DB is set)
# ---------------------------------------------------------------------------

def _get_db_conn() -> sqlite3.Connection | None:
    """Return a SQLite connection if NAUTOBOT_MAPS_DB is configured, else None."""
    if not NAUTOBOT_MAPS_DB:
        return None
    conn = sqlite3.connect(NAUTOBOT_MAPS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    """Create the persistence tables if they don't exist yet."""
    conn = _get_db_conn()
    if conn is None:
        return
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS device_criticality_override (
                nautobot_device_id TEXT PRIMARY KEY,
                is_critical        INTEGER NOT NULL DEFAULT 1,
                reason             TEXT    NOT NULL DEFAULT '',
                updated_by         TEXT    NOT NULL DEFAULT '',
                updated_at         TEXT    NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS librenms_device_map (
                nautobot_device_id  TEXT PRIMARY KEY,
                librenms_device_id  INTEGER NOT NULL,
                librenms_hostname   TEXT    NOT NULL DEFAULT ''
            )
            """
        )
    conn.close()
    logger.info("Nautobot Maps DB initialised at %s", NAUTOBOT_MAPS_DB)


# Initialise the DB at startup (no-op when NAUTOBOT_MAPS_DB is not set).
_init_db()

def _cache_get(key: str):
    return cache.get(key)


def _cache_set(key: str, data):
    cache.set(key, data)


def _nested_str(obj: dict | None, *keys: str) -> str:
    """Return the first non-empty value found in *obj* for the given keys.

    Nautobot 2.x uses ``name`` / ``label`` for nested objects; Nautobot 3.x
    returns a full model representation that uses ``display``.  Trying all
    three keys keeps the code compatible with both versions and with the
    mock fixtures used in unit/integration tests.
    """
    if not obj:
        return ""
    if isinstance(obj, str):
        return obj
    if not isinstance(obj, dict):
        return str(obj)
    for key in keys:
        val = obj.get(key)
        if val is not None and val != "":
            return str(val)
    return ""


def _build_id_name_map(endpoint: str) -> dict:
    """Fetch all objects from *endpoint* and return a ``{id: display_name}`` map.

    Used as a fallback when nested objects in Nautobot's response don't
    include a human-readable field (e.g. some Nautobot 3.x builds return
    brief nested objects with only ``id`` and ``url``).
    """
    try:
        items = fetch_all_pages(endpoint)
        result = {}
        for item in items:
            uid = item.get("id")
            if not uid:
                continue
            name = _nested_str(item, "name", "display", "label", "slug")
            if name:
                result[uid] = name
        return result
    except Exception as exc:
        logger.debug("Could not build name lookup for %s: %s", endpoint, exc)
        return {}


def _build_device_type_maps() -> tuple:
    """Return ``({device_type_id: manufacturer_name}, {device_type_id: model_name})``.

    In Nautobot 3.x the brief nested ``device_type`` object returned inside
    device list responses does **not** include ``manufacturer`` or ``model``
    fields — only ``id`` and ``url``.  Fetching all device types once lets us
    resolve both fields for any device without extra per-device API calls.

    The manufacturer sub-object inside a device-type listing may itself be a
    brief object (id+url only in Nautobot 3.0.x), so we also build a
    manufacturer UUID→name map and fall back to it when the inline name is
    missing.
    """
    try:
        mfr_map = _build_id_name_map("dcim/manufacturers/")
        items = fetch_all_pages("dcim/device-types/")
        dt_mfr: dict = {}
        dt_model: dict = {}
        for item in items:
            uid = item.get("id")
            if not uid:
                continue
            # model name
            model = item.get("model") or _nested_str(item, "display") or ""
            if model:
                dt_model[uid] = model
            # manufacturer name
            mfr_obj = item.get("manufacturer") or {}
            mfr_id = mfr_obj.get("id", "") if isinstance(mfr_obj, dict) else ""
            mfr_name = (
                _nested_str(mfr_obj, "name", "display")
                or mfr_map.get(mfr_id, "")
            )
            if mfr_name:
                dt_mfr[uid] = mfr_name
        return dt_mfr, dt_model
    except Exception as exc:
        logger.debug("Could not build device-type maps: %s", exc)
        return {}, {}


def _build_tenant_group_map() -> dict:
    """Return ``{tenant_id: tenant_group_name}``.

    Fetches all tenants and resolves each tenant's ``tenant_group`` field so
    that locations can expose the tenant group without extra per-location API
    calls.  A fallback name-map for tenant groups is built from the
    ``tenancy/tenant-groups/`` endpoint for Nautobot builds where the nested
    object is brief (id + url only).
    """
    try:
        tg_name_map = _build_id_name_map("tenancy/tenant-groups/")
        tenants = fetch_all_pages("tenancy/tenants/")
        tenant_group_map: dict = {}
        for tenant in tenants:
            tid = tenant.get("id")
            if not tid:
                continue
            tg_obj = tenant.get("tenant_group") or {}
            tg_id = tg_obj.get("id", "") if isinstance(tg_obj, dict) else ""
            tg_name = (
                _nested_str(tg_obj, "name", "display")
                or tg_name_map.get(tg_id, "")
            )
            if tg_name:
                tenant_group_map[tid] = tg_name
        return tenant_group_map
    except Exception as exc:
        logger.debug("Could not build tenant group map: %s", exc)
        return {}



def nautobot_get(endpoint: str, params: dict | None = None) -> dict:
    """Perform a GET request against the Nautobot REST API."""
    if not NAUTOBOT_URL or not NAUTOBOT_TOKEN:
        raise RuntimeError(
            "NAUTOBOT_URL and NAUTOBOT_TOKEN must be set in environment variables."
        )
    cache_key = f"{endpoint}:{params}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    accept = "application/json"
    if NAUTOBOT_API_VERSION:
        accept += f"; version={NAUTOBOT_API_VERSION}"
    headers = {
        "Authorization": f"Token {NAUTOBOT_TOKEN}",
        "Content-Type": "application/json",
        "Accept": accept,
    }
    url = f"{NAUTOBOT_URL}/api/{endpoint.lstrip('/')}"
    response = requests.get(
        url, headers=headers, params=params, timeout=15, verify=NAUTOBOT_VERIFY_SSL
    )
    response.raise_for_status()
    data = response.json()
    _cache_set(cache_key, data)
    return data


def nautobot_post(endpoint: str, payload: dict) -> dict:
    """Perform a POST request against the Nautobot REST API."""
    if not NAUTOBOT_URL or not NAUTOBOT_TOKEN:
        raise RuntimeError(
            "NAUTOBOT_URL and NAUTOBOT_TOKEN must be set in environment variables."
        )
    accept = "application/json"
    if NAUTOBOT_API_VERSION:
        accept += f"; version={NAUTOBOT_API_VERSION}"
    headers = {
        "Authorization": f"Token {NAUTOBOT_TOKEN}",
        "Content-Type": "application/json",
        "Accept": accept,
    }
    url = f"{NAUTOBOT_URL}/api/{endpoint.lstrip('/')}"
    response = requests.post(
        url, headers=headers, json=payload, timeout=15, verify=NAUTOBOT_VERIFY_SSL
    )
    response.raise_for_status()
    return response.json()


def nautobot_delete(endpoint: str) -> None:
    """Perform a DELETE request against the Nautobot REST API."""
    if not NAUTOBOT_URL or not NAUTOBOT_TOKEN:
        raise RuntimeError(
            "NAUTOBOT_URL and NAUTOBOT_TOKEN must be set in environment variables."
        )
    accept = "application/json"
    if NAUTOBOT_API_VERSION:
        accept += f"; version={NAUTOBOT_API_VERSION}"
    headers = {
        "Authorization": f"Token {NAUTOBOT_TOKEN}",
        "Content-Type": "application/json",
        "Accept": accept,
    }
    url = f"{NAUTOBOT_URL}/api/{endpoint.lstrip('/')}"
    response = requests.delete(
        url, headers=headers, timeout=15, verify=NAUTOBOT_VERIFY_SSL
    )
    response.raise_for_status()


def fetch_all_pages(endpoint: str, params: dict | None = None) -> list:
    """Fetch all paginated results from a Nautobot API endpoint."""
    params = dict(params or {})
    params.setdefault("limit", 200)
    results = []
    offset = 0
    while True:
        params["offset"] = offset
        data = nautobot_get(endpoint, params)
        results.extend(data.get("results", []))
        if not data.get("next"):
            break
        offset += params["limit"]
    return results


def get_locations(include_without_coordinates: bool = False) -> list:
    """Fetch locations from Nautobot.

    By default, only locations with valid GPS coordinates are returned.
    Set ``include_without_coordinates=True`` to include all locations and
    keep missing/invalid coordinates as ``None``.
    """
    raw = fetch_all_pages("dcim/locations/")

    # Fallback lookup tables: cover Nautobot builds where brief nested objects
    # only contain ``id`` + ``url`` without a human-readable name/display field.
    tenant_map = _build_id_name_map("tenancy/tenants/")
    status_map = _build_id_name_map("extras/statuses/")
    lt_map = _build_id_name_map("dcim/location-types/")
    tag_map = _build_id_name_map("extras/tags/")
    tenant_group_map = _build_tenant_group_map()

    # Build a location id → name map from the raw data for parent resolution.
    # Parents are locations themselves, and their nested objects may also be
    # brief in Nautobot 3.x.
    loc_name_map: dict = {}
    for loc in raw:
        uid = loc.get("id")
        if uid:
            name = _nested_str(loc, "name", "display")
            if name:
                loc_name_map[uid] = name

    if raw:
        logger.debug(
            "Nautobot location sample – tenant=%r  status=%r",
            raw[0].get("tenant"),
            raw[0].get("status"),
        )

    locations = []
    for loc in raw:
        lat = None
        lon = None
        raw_lat = loc.get("latitude")
        raw_lon = loc.get("longitude")
        has_coordinates = raw_lat is not None and raw_lon is not None
        if has_coordinates:
            try:
                lat = float(raw_lat)
                lon = float(raw_lon)
            except (TypeError, ValueError):
                has_coordinates = False
                lat = None
                lon = None
        if not has_coordinates and not include_without_coordinates:
            continue

        tenant_obj = loc.get("tenant") or {}
        tenant_id = tenant_obj.get("id", "") if isinstance(tenant_obj, dict) else ""
        tenant_name = (
            _nested_str(tenant_obj, "name", "display")
            or tenant_map.get(tenant_id, "")
        )

        status_obj = loc.get("status") or {}
        status_id = status_obj.get("id", "") if isinstance(status_obj, dict) else ""
        status_name = (
            _nested_str(status_obj, "label", "name", "display")
            or status_map.get(status_id, "")
        )

        lt_obj = loc.get("location_type") or {}
        lt_id = lt_obj.get("id", "") if isinstance(lt_obj, dict) else ""
        location_type_name = (
            _nested_str(lt_obj, "name", "display")
            or lt_map.get(lt_id, "")
        )

        parent_obj = loc.get("parent") or {}
        parent_id = parent_obj.get("id", "") if isinstance(parent_obj, dict) else ""
        parent_name = (
            _nested_str(parent_obj, "name", "display")
            or loc_name_map.get(parent_id, "")
        )

        tenant_group_name = tenant_group_map.get(tenant_id, "")

        # Tags – each tag is a nested object with at least a name/display key.
        # In Nautobot 3.x brief tag objects may only contain id+url, so fall
        # back to the pre-built tag_map.
        raw_tags = loc.get("tags") or []
        tag_names = []
        for t in raw_tags:
            if isinstance(t, dict):
                tag_id = t.get("id", "")
                tag_name = (
                    _nested_str(t, "name", "display")
                    or tag_map.get(tag_id, "")
                )
            else:
                tag_name = ""
            if tag_name:
                tag_names.append(tag_name)

        locations.append(
            {
                "id": loc.get("id", ""),
                "name": loc.get("name", "Unknown"),
                "slug": loc.get("slug", ""),
                "status": status_name,
                "location_type": location_type_name,
                "parent": parent_name,
                "latitude": lat,
                "longitude": lon,
                "description": loc.get("description", ""),
                "physical_address": loc.get("physical_address", ""),
                "facility": loc.get("facility", ""),
                "tenant": tenant_name,
                "tenant_id": tenant_id,
                "tenant_group": tenant_group_name,
                "asn": loc.get("asn"),
                "time_zone": loc.get("time_zone", ""),
                "tags": tag_names,
                "url": loc.get("url", ""),
            }
        )
    return locations


# ---------------------------------------------------------------------------
# NOC alert helpers
# ---------------------------------------------------------------------------

# Device statuses that count as "down" for alert purposes
_DOWN_STATUSES: frozenset = frozenset({"offline", "failed", "decommissioning"})

# ---------------------------------------------------------------------------
# Configurable critical-role keyword system
# ---------------------------------------------------------------------------
# Built-in defaults – used when no overrides are configured.
_DEFAULT_CORE_ROLE_KEYWORDS: tuple = ("core", "spine", "distribution", "router", "gateway")

# CRITICAL_ROLE_KEYWORDS env var (comma-separated) replaces the built-in
# defaults for every location type that has no specific rule in the JSON file.
_env_keywords_raw = os.getenv("CRITICAL_ROLE_KEYWORDS", "").strip()
_ENV_CORE_ROLE_KEYWORDS: tuple = (
    tuple(kw.strip().lower() for kw in _env_keywords_raw.split(",") if kw.strip())
    if _env_keywords_raw
    else _DEFAULT_CORE_ROLE_KEYWORDS
)

# Per-location-type rules loaded from the JSON file (if configured).
# Schema: {"<location_type_lower>": ["kw1", "kw2", ...], "default": [...]}
_CRITICALITY_RULES: dict = {}
if CRITICALITY_RULES_FILE:
    try:
        with open(CRITICALITY_RULES_FILE, encoding="utf-8") as _f:
            _loaded = json.load(_f)
        if isinstance(_loaded, dict):
            _CRITICALITY_RULES = {
                k.lower(): [kw.lower() for kw in v]
                for k, v in _loaded.items()
                if isinstance(v, list)
            }
            logger.info(
                "Loaded criticality rules from %s: %s",
                CRITICALITY_RULES_FILE,
                list(_CRITICALITY_RULES.keys()),
            )
        else:
            logger.warning(
                "Criticality rules file %s must contain a JSON object; ignoring.",
                CRITICALITY_RULES_FILE,
            )
    except Exception as exc:
        logger.warning("Could not load criticality rules from %s: %s", CRITICALITY_RULES_FILE, exc)


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


def compute_alert_level(devices: list, location_type: str | None = None) -> dict:
    """Return the NOC alert level for a location based on its device list.

    Returns a dict::

        {"level": "critical" | "medium" | "ok", "reason": "<human-readable text>"}

    Rules:
    * **critical** – at least one device whose role contains a core-network
      keyword has a down status.  The keyword set is resolved from
      ``CRITICAL_ROLE_KEYWORDS`` / ``CRITICALITY_RULES_FILE`` / the
      per-device ``is_critical`` override stored in the SQLite DB.
    * **medium**   – more than 25 % of all devices have a down status.
    * **ok**       – neither condition above is met (or no devices present).

    The optional *location_type* parameter selects the matching keyword set
    when location-type-scoped rules are configured (e.g. "datacenter" vs
    "office").
    """
    if not devices:
        return {"level": "ok", "reason": ""}

    core_keywords = _get_critical_keywords(location_type)

    # Load per-device overrides from SQLite (if DB is configured)
    override_map: dict = {}
    conn = _get_db_conn()
    if conn is not None:
        try:
            ids = [d.get("id") for d in devices if d.get("id")]
            if ids:
                placeholders = ",".join("?" * len(ids))
                rows = conn.execute(
                    f"SELECT nautobot_device_id, is_critical FROM device_criticality_override "
                    f"WHERE nautobot_device_id IN ({placeholders})",
                    ids,
                ).fetchall()
                override_map = {r["nautobot_device_id"]: bool(r["is_critical"]) for r in rows}
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
    if total > 0 and down_count / total > 0.25:
        pct = round(down_count / total * 100)
        return {
            "level": "medium",
            "reason": f"{down_count}/{total} devices offline ({pct}%)",
        }

    return {"level": "ok", "reason": ""}



def _librenms_get(path: str, params: dict | None = None) -> dict:
    """Perform a GET request against the LibreNMS REST API."""
    headers = {"X-Auth-Token": LIBRENMS_API_TOKEN}
    url = f"{LIBRENMS_URL}/api/v0/{path.lstrip('/')}"
    response = requests.get(url, headers=headers, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def _fetch_librenms_inventory() -> list:
    """Fetch full LibreNMS inventory once."""
    data = _librenms_get("devices", {"type": "all"})
    return data.get("devices", [])


def _load_librenms_id_map() -> dict:
    """Load persisted Nautobot UUID → LibreNMS device mapping."""
    lnms_id_map: dict = {}
    conn = _get_db_conn()
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT nautobot_device_id, librenms_device_id, librenms_hostname "
                "FROM librenms_device_map"
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
    devices: list, lnms_devices: list | None = None, lnms_id_map: dict | None = None
) -> list:
    """Merge live LibreNMS status into *devices* (in-place copy returned).

    For each device, LibreNMS is queried by hostname.  The mapping between
    Nautobot device IDs and LibreNMS device IDs is persisted in the
    ``librenms_device_map`` SQLite table when the DB is configured.

    LibreNMS ``status`` field: ``1`` = up, ``0`` = down.  When LibreNMS
    reports a device as down but Nautobot has it as active, the status is
    set to ``"offline"`` so ``compute_alert_level`` counts it as down.

    The enrichment is *additive*: Nautobot status is never upgraded (a device
    already offline in Nautobot stays offline regardless of LibreNMS).
    """
    if not LIBRENMS_URL or not LIBRENMS_API_TOKEN:
        return devices

    if lnms_devices is None:
        try:
            lnms_devices = _fetch_librenms_inventory()
        except Exception as exc:
            logger.warning("LibreNMS enrichment failed (could not fetch devices): %s", exc)
            return devices

    # Build hostname → LibreNMS record map (case-insensitive)
    lnms_by_hostname: dict = {}
    for ld in lnms_devices:
        hostname = (ld.get("hostname") or "").lower()
        if hostname:
            lnms_by_hostname[hostname] = ld

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
            device_name = (device.get("name") or "").lower()
            lnms_record = lnms_by_hostname.get(device_name)

        if lnms_record is not None:
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
    """Upsert a Nautobot ↔ LibreNMS device mapping into the SQLite DB."""
    conn = _get_db_conn()
    if conn is None:
        return
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO librenms_device_map (nautobot_device_id, librenms_device_id, librenms_hostname)
                VALUES (?, ?, ?)
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


def _get_location_devices_and_alert(
    location_id: str,
    location_type: str | None = None,
    devices_data: list | None = None,
    lookup_maps: dict | None = None,
    lnms_devices: list | None = None,
    lnms_id_map: dict | None = None,
) -> tuple[list, dict]:
    """Return ``(devices, alert)`` for a location."""
    if devices_data is None:
        # Devices at this location
        # Nautobot 3.x uses the "location" filter parameter (UUID accepted);
        # "location_id" was removed in 3.x and returns 400.
        devices_data = fetch_all_pages("dcim/devices/", {"location": location_id})

    # Fallback lookup: covers Nautobot builds where brief nested objects
    # only carry id+url without a human-readable name.
    # In Nautobot 3.x the brief device_type nested object inside device
    # list responses does NOT include manufacturer or model fields, so we
    # pre-fetch all device types to resolve device_type_id → model/manufacturer.
    if lookup_maps is None:
        dt_mfr_map, dt_model_map = _build_device_type_maps()
        mfr_map = _build_id_name_map("dcim/manufacturers/")
        role_map = _build_id_name_map("extras/roles/")
        tenant_map = _build_id_name_map("tenancy/tenants/")
        status_map = _build_id_name_map("extras/statuses/")
    else:
        dt_mfr_map = lookup_maps.get("dt_mfr_map", {})
        dt_model_map = lookup_maps.get("dt_model_map", {})
        mfr_map = lookup_maps.get("mfr_map", {})
        role_map = lookup_maps.get("role_map", {})
        tenant_map = lookup_maps.get("tenant_map", {})
        status_map = lookup_maps.get("status_map", {})

    devices = []
    for d in devices_data:
        dt = d.get("device_type") or {}
        dt_id = dt.get("id", "") if isinstance(dt, dict) else ""
        mfr_obj = dt.get("manufacturer") if isinstance(dt, dict) else None
        mfr_id = mfr_obj.get("id", "") if isinstance(mfr_obj, dict) else ""
        mfr_name = (
            _nested_str(mfr_obj, "name", "display")
            or mfr_map.get(mfr_id, "")
            or dt_mfr_map.get(dt_id, "")
        )

        ten_obj = d.get("tenant") or {}
        ten_id = ten_obj.get("id", "") if isinstance(ten_obj, dict) else ""
        ten_name = (
            _nested_str(ten_obj, "name", "display")
            or tenant_map.get(ten_id, "")
        )

        st_obj = d.get("status") or {}
        st_id = st_obj.get("id", "") if isinstance(st_obj, dict) else ""
        st_name = (
            _nested_str(st_obj, "label", "name", "display")
            or status_map.get(st_id, "")
        )

        devices.append(
            {
                "id": d.get("id") or "",
                "name": d.get("name") or "Unknown",
                "device_type": (
                    _nested_str(d.get("device_type"), "model", "display")
                    or dt_model_map.get(dt_id, "")
                ),
                "manufacturer": mfr_name,
                "role": (
                    _nested_str(d.get("role"), "name", "display")
                    or role_map.get(
                        d.get("role", {}).get("id", "")
                        if isinstance(d.get("role"), dict)
                        else "",
                        "",
                    )
                ),
                "status": st_name,
                "platform": _nested_str(d.get("platform"), "name", "display"),
                "serial": d.get("serial") or "",
                "tenant": ten_name,
            }
        )

    enriched = _enrich_with_librenms(
        devices, lnms_devices=lnms_devices, lnms_id_map=lnms_id_map
    )
    return enriched, compute_alert_level(enriched, location_type)


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _alert_sort_key(level: str) -> int:
    return {"critical": 0, "medium": 1, "unknown": 2, "ok": 3}.get(
        (level or "").lower(), 4
    )


def _apply_alert_board_freshness(payload: dict) -> dict:
    """Attach freshness metadata to an alert-board payload."""
    checked_at = payload.get("checked_at")
    age_seconds = 0
    if checked_at:
        try:
            parsed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
            age_seconds = max(
                0, int((datetime.now(timezone.utc) - parsed).total_seconds())
            )
        except ValueError:
            age_seconds = 0
    result = dict(payload)
    stale_after_seconds = int(result.get("stale_after_seconds", CACHE_TTL))
    result["age_seconds"] = age_seconds
    result["stale"] = age_seconds > stale_after_seconds
    return result


def get_alert_board_data(force_refresh: bool = False) -> dict:
    """Return alert summaries for all locations."""
    cache_key = "alert-board-data:v1"
    if force_refresh:
        cache.delete(cache_key)
    cached = _cache_get(cache_key)
    if cached is not None:
        return _apply_alert_board_freshness(cached)

    locations = get_locations(include_without_coordinates=True)
    alerts = []
    summary = {"critical": 0, "medium": 0, "unknown": 0, "ok": 0}

    all_devices = fetch_all_pages("dcim/devices/")
    devices_by_location: dict = {}
    for device in all_devices:
        location_obj = device.get("location") or {}
        location_id = location_obj.get("id", "") if isinstance(location_obj, dict) else ""
        if not location_id:
            continue
        devices_by_location.setdefault(location_id, []).append(device)

    dt_mfr_map, dt_model_map = _build_device_type_maps()
    lookup_maps = {
        "dt_mfr_map": dt_mfr_map,
        "dt_model_map": dt_model_map,
        "mfr_map": _build_id_name_map("dcim/manufacturers/"),
        "role_map": _build_id_name_map("extras/roles/"),
        "tenant_map": _build_id_name_map("tenancy/tenants/"),
        "status_map": _build_id_name_map("extras/statuses/"),
    }
    lnms_devices = None
    lnms_id_map = None
    if LIBRENMS_URL and LIBRENMS_API_TOKEN:
        try:
            lnms_devices = _fetch_librenms_inventory()
            lnms_id_map = _load_librenms_id_map()
        except Exception as exc:
            logger.warning("Could not refresh LibreNMS inventory for alert board: %s", exc)
            lnms_devices = []
            lnms_id_map = {}

    for loc in locations:
        loc_devices = devices_by_location.get(loc["id"], [])
        try:
            devices, alert = _get_location_devices_and_alert(
                loc["id"],
                loc.get("location_type") or None,
                devices_data=loc_devices,
                lookup_maps=lookup_maps,
                lnms_devices=lnms_devices,
                lnms_id_map=lnms_id_map,
            )
        except Exception as exc:
            logger.warning(
                "Could not compute alert summary for location %s: %s",
                loc.get("id"),
                exc,
            )
            devices = []
            alert = {"level": "unknown", "reason": "Could not compute alert state"}

        down_devices = [
            d for d in devices if (d.get("status") or "").lower().strip() in _DOWN_STATUSES
        ]
        level = (alert.get("level") or "ok").lower()
        summary[level] = summary.get(level, 0) + 1
        alerts.append(
            {
                **loc,
                "alert_level": level,
                "alert_reason": alert.get("reason", ""),
                "device_count": len(devices),
                "down_device_count": len(down_devices),
            }
        )

    alerts.sort(
        key=lambda item: (
            _alert_sort_key(item.get("alert_level", "ok")),
            -item.get("down_device_count", 0),
            item.get("name", "").lower(),
        )
    )

    payload = {
        "checked_at": _iso_utc_now(),
        "stale_after_seconds": CACHE_TTL,
        "summary": {
            "total": len(alerts),
            "critical": summary.get("critical", 0),
            "medium": summary.get("medium", 0),
            "unknown": summary.get("unknown", 0),
            "ok": summary.get("ok", 0),
            "non_ok": summary.get("critical", 0)
            + summary.get("medium", 0)
            + summary.get("unknown", 0),
        },
        "alerts": alerts,
    }
    _cache_set(cache_key, payload)
    return _apply_alert_board_freshness(payload)


def get_location_detail(location_id: str, location_type: str | None = None) -> dict:
    """Fetch detailed info (devices, prefixes, ASNs) for a single location."""
    detail: dict = {}
    try:
        devices, alert = _get_location_devices_and_alert(location_id, location_type)
        detail["devices"] = devices
        detail["alert"] = alert
    except Exception as exc:
        logger.warning("Could not fetch devices for location %s: %s", location_id, exc)
        detail["devices"] = []
        detail["alert"] = {"level": "ok", "reason": ""}

    # ASN(s) associated with this location via the ipam/asns endpoint
    try:
        asns_data = fetch_all_pages("ipam/asns/", {"location_id": location_id})
        detail["asns"] = [
            {
                "asn": a.get("asn"),
                "description": a.get("description", ""),
                "tenant": _nested_str(a.get("tenant"), "name", "display"),
            }
            for a in asns_data
        ]
    except Exception as exc:
        logger.warning("Could not fetch ASNs for location %s: %s", location_id, exc)
        detail["asns"] = []

    return detail


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


def _wants_json():
    """Return True when the client prefers a JSON response."""
    return (
        request.path.startswith("/api/")
        or request.accept_mimetypes.best_match(["application/json", "text/html"])
        == "application/json"
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
            error_message="The page you are looking for does not exist. "
            "Check the URL or head back to the map.",
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html", nautobot_url=NAUTOBOT_URL)


@app.route("/alerts")
def alert_board():
    return render_template("alerts.html", nautobot_url=NAUTOBOT_URL)


@app.route("/api/locations")
def api_locations():
    """Return all Nautobot locations that have GPS coordinates."""
    try:
        locations = get_locations()
        return jsonify({"locations": locations})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
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
        return jsonify({"error": str(exc)}), 503
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
    try:
        return jsonify(get_alert_board_data(force_refresh=force_refresh))
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
        all_locations = get_locations()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
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

    Returns 503 when the DB is not configured (``NAUTOBOT_MAPS_DB`` not set).
    """
    conn = _get_db_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured (set NAUTOBOT_MAPS_DB)"}), 503
    try:
        rows = conn.execute(
            "SELECT nautobot_device_id, is_critical, reason, updated_by, updated_at "
            "FROM device_criticality_override ORDER BY updated_at DESC"
        ).fetchall()
        return jsonify(
            {"overrides": [dict(r) for r in rows]}
        )
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
    conn = _get_db_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured (set NAUTOBOT_MAPS_DB)"}), 503
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
        with conn:
            conn.execute(
                """
                INSERT INTO device_criticality_override
                    (nautobot_device_id, is_critical, reason, updated_by, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
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
    conn = _get_db_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured (set NAUTOBOT_MAPS_DB)"}), 503
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM device_criticality_override WHERE nautobot_device_id = ?",
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
# Roles proxy endpoints
# ---------------------------------------------------------------------------

@app.route("/api/roles", methods=["GET"])
def api_list_roles():
    """Return all roles from Nautobot (proxied from extras/roles/)."""
    try:
        roles = fetch_all_pages("extras/roles/")
        return jsonify({"roles": roles})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
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
        created = nautobot_post("extras/roles/", body)
        cache.delete_memoized(fetch_all_pages)
        return jsonify(created), 201
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
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
        nautobot_delete(f"extras/roles/{role_id}/")
        cache.clear()
        return jsonify({"status": "deleted", "id": role_id})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
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
        location_types = fetch_all_pages("dcim/location-types/")
        return jsonify({"location_types": location_types})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
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
        created = nautobot_post("dcim/location-types/", body)
        cache.clear()
        return jsonify(created), 201
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
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
        nautobot_delete(f"dcim/location-types/{lt_id}/")
        cache.clear()
        return jsonify({"status": "deleted", "id": lt_id})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        if exc.response.status_code == 404:
            return jsonify({"error": "Location type not found"}), 404
        return jsonify({"error": "Failed to communicate with Nautobot API"}), exc.response.status_code
    except Exception as exc:
        logger.error("Unexpected error deleting location type: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    try:
        port = int(os.getenv("FLASK_RUN_PORT", 5000))
    except (ValueError, TypeError):
        port = 5000
    app.run(host=_get_flask_run_host(), port=port, debug=debug)
