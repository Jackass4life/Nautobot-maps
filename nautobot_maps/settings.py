"""All settings read from the environment (.env), in one place (#165).

Other modules read them as ``settings.NAME`` at call time, never with
``from settings import NAME``, so tests can change a setting with
``monkeypatch.setattr(settings, "NAME", value)``.
"""

import ipaddress
import os
import re
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()


def parse_csv_set(value: str) -> set[str]:
    """Return a lower-cased set from a comma/semicolon-separated string."""
    return {item.strip().lower() for item in re.split(r"[;,]", value or "") if item.strip()}


def _validate_nautobot_url(value: str) -> str:
    normalized = (value or "").strip().rstrip("/")
    if not normalized:
        return normalized
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(
            "Invalid NAUTOBOT_URL configuration: expected an absolute http(s) URL with a host, "
            "for example https://nautobot.example.com"
        )
    return normalized


def _verify_ssl(value: str) -> bool | str:
    """ "true" (default) = verify, "false" = skip, anything else = path to a CA bundle.

    The usual yes/no words work too ("no"/"0"/"off" were documented for
    LIBRENMS_VERIFY_SSL before it accepted a path, #191).
    """
    value = value.strip()
    if value.lower() in ("false", "no", "0", "off"):
        return False
    if value.lower() in ("", "true", "yes", "1", "on"):
        return True
    return value


def _networks(name: str, value: str) -> tuple:
    """Parse comma/semicolon-separated IP addresses or CIDRs; fail at startup on a typo."""
    networks = []
    for item in re.split(r"[;,]", value or ""):
        if item.strip():
            try:
                networks.append(ipaddress.ip_network(item.strip(), strict=False))
            except ValueError as exc:
                raise RuntimeError(f"Invalid {name} entry {item.strip()!r}: expected an IP address or CIDR") from exc
    return tuple(networks)


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value not in ("0", "false", "no")


# Flask
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"  # python app.py only
FLASK_RUN_PORT = os.getenv("FLASK_RUN_PORT", "")  # python app.py only

# Logging (#193): DEBUG, INFO, WARNING, ERROR; "text" or "json" lines.
LOG_LEVEL = os.getenv("LOG_LEVEL", "").strip().upper() or "INFO"
if LOG_LEVEL not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
    raise RuntimeError(f"Invalid LOG_LEVEL {LOG_LEVEL!r}: expected DEBUG, INFO, WARNING or ERROR")
LOG_FORMAT = os.getenv("LOG_FORMAT", "").strip().lower() or "text"
if LOG_FORMAT not in ("text", "json"):
    raise RuntimeError(f"Invalid LOG_FORMAT {LOG_FORMAT!r}: expected text or json")

# Nautobot
NAUTOBOT_URL = _validate_nautobot_url(os.getenv("NAUTOBOT_URL", ""))
NAUTOBOT_TOKEN = os.getenv("NAUTOBOT_TOKEN", "")
NAUTOBOT_API_VERSION = os.getenv("NAUTOBOT_API_VERSION", "").strip()
NAUTOBOT_VERIFY_SSL = _verify_ssl(os.getenv("NAUTOBOT_VERIFY_SSL", "true"))

# Caching and sync intervals.  An empty value (docker-compose passes unset
# variables as "") means the default.
CACHE_TTL = int(os.getenv("CACHE_TTL", "").strip() or 300)
CACHE_TYPE = os.getenv("CACHE_TYPE", "SimpleCache")
CACHE_REDIS_URL = os.getenv("CACHE_REDIS_URL", "")
INVENTORY_SYNC_INTERVAL_SECONDS = int(os.getenv("INVENTORY_SYNC_INTERVAL_SECONDS", "").strip() or CACHE_TTL)
LIBRENMS_SYNC_INTERVAL_SECONDS = int(os.getenv("LIBRENMS_SYNC_INTERVAL_SECONDS", "").strip() or CACHE_TTL)
# Background scheduler (#154): sync and record alert history with nobody
# viewing the board.  On by default; "false"/"0"/"no" turns it off.
BACKGROUND_SYNC_ENABLED = _flag("BACKGROUND_SYNC_ENABLED", True)

# LibreNMS (optional)
LIBRENMS_URL = os.getenv("LIBRENMS_URL", "").strip().rstrip("/")
LIBRENMS_API_TOKEN = os.getenv("LIBRENMS_API_TOKEN", "").strip()
# Like NAUTOBOT_VERIFY_SSL: "true", "false" or a path to a CA bundle (#191).
LIBRENMS_VERIFY_SSL = _verify_ssl(os.getenv("LIBRENMS_VERIFY_SSL", "true"))

# Delete resolved alerts (with their events and cases) and site severity
# changes older than this many days, once a day (#194).  0 keeps everything.
ALERT_HISTORY_RETENTION_DAYS = int(os.getenv("ALERT_HISTORY_RETENTION_DAYS", "").strip() or 0)

# Prometheus metrics at /metrics (#200); "false" turns the endpoint off.
METRICS_ENABLED = _flag("METRICS_ENABLED", True)

# MCP server at /mcp for AI assistants (#250); off unless "true".
MCP_ENABLED = _flag("MCP_ENABLED", False)

# Map tiles and address search (#197).  Both default to the public
# OpenStreetMap services; point them at internal ones on closed networks.
MAP_TILE_URL = os.getenv("MAP_TILE_URL", "").strip() or "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_TILE_ATTRIBUTION = (
    os.getenv("MAP_TILE_ATTRIBUTION", "").strip()
    or '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
)
GEOCODER_ENABLED = _flag("GEOCODER_ENABLED", True)
GEOCODER_URL = os.getenv("GEOCODER_URL", "").strip().rstrip("/") or "https://nominatim.openstreetmap.org"
GEOCODER_USER_AGENT = (
    os.getenv("GEOCODER_USER_AGENT", "").strip() or "nautobot-maps (+https://github.com/Jackass4life/Nautobot-maps)"
)

# Persistence
NAUTOBOT_MAPS_DATABASE_URL = os.getenv("NAUTOBOT_MAPS_DATABASE_URL", "").strip()
# Without these a database that drops packets makes every request hang until
# the OS gives up on the connection, and a runaway query holds a worker (#190).
DB_CONNECT_TIMEOUT_SECONDS = int(os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "").strip() or 5)
DB_STATEMENT_TIMEOUT_SECONDS = int(os.getenv("DB_STATEMENT_TIMEOUT_SECONDS", "").strip() or 60)
# Removed in #153 (SQLite support); only read to warn when it is still set.
LEGACY_SQLITE_DB = os.getenv("NAUTOBOT_MAPS_DB", "").strip()

# Authentication / RBAC (optional)
AUTH_MODE = os.getenv("AUTH_MODE", "disabled").strip().lower() or "disabled"
AUTH_HEADER_USER = os.getenv("AUTH_HEADER_USER", "X-Forwarded-User").strip() or "X-Forwarded-User"
AUTH_HEADER_GROUPS = os.getenv("AUTH_HEADER_GROUPS", "X-Forwarded-Groups").strip() or "X-Forwarded-Groups"
AUTH_DEFAULT_ROLE = os.getenv("AUTH_DEFAULT_ROLE", "").strip().lower()
if AUTH_DEFAULT_ROLE not in ("viewer", "operator", "admin"):
    AUTH_DEFAULT_ROLE = ""
AUTH_VIEWER_GROUPS = parse_csv_set(os.getenv("AUTH_VIEWER_GROUPS", ""))
AUTH_OPERATOR_GROUPS = parse_csv_set(os.getenv("AUTH_OPERATOR_GROUPS", ""))
AUTH_ADMIN_GROUPS = parse_csv_set(os.getenv("AUTH_ADMIN_GROUPS", ""))
# Header mode: every page and API (except /healthz) needs at least the viewer
# role.  Off by default, so reads stay public (#188).
AUTH_REQUIRE_VIEWER = _flag("AUTH_REQUIRE_VIEWER", False)
# Header mode: identity headers are only trusted from these proxy addresses,
# and, when AUTH_PROXY_SECRET is set, only with that secret in the
# X-Auth-Proxy-Secret header.  Anything else is anonymous (#187).
AUTH_TRUSTED_PROXIES = _networks(
    "AUTH_TRUSTED_PROXIES", os.getenv("AUTH_TRUSTED_PROXIES", "").strip() or "127.0.0.1/32,::1/128"
)
AUTH_PROXY_SECRET = os.getenv("AUTH_PROXY_SECRET", "").strip()
# AUTH_MODE=disabled: allow changing criticality overrides without
# authentication.  Off by default: anyone who can reach the app could change
# which devices count as critical (#188).
ALLOW_UNAUTHENTICATED_WRITES = _flag("ALLOW_UNAUTHENTICATED_WRITES", False)

# Criticality
CRITICAL_ROLE_KEYWORDS = os.getenv("CRITICAL_ROLE_KEYWORDS", "").strip()
# Path to a JSON file with per-location-type criticality keyword rules
CRITICALITY_RULES_FILE = os.getenv("CRITICALITY_RULES_FILE", "")

# Alert board
ALERT_BOARD_EXCLUDED_LOCATION_TYPES = parse_csv_set(
    os.getenv("ALERT_BOARD_EXCLUDED_LOCATION_TYPES", "graveyard,warehouse")
)
ALERT_BOARD_EXCLUDED_LOCATION_STATUSES = parse_csv_set(os.getenv("ALERT_BOARD_EXCLUDED_LOCATION_STATUSES", ""))
ALERT_BOARD_EXCLUDED_LOCATION_TAGS = parse_csv_set(os.getenv("ALERT_BOARD_EXCLUDED_LOCATION_TAGS", ""))
ALERT_BOARD_EXCLUDED_LOCATION_NAMES = parse_csv_set(os.getenv("ALERT_BOARD_EXCLUDED_LOCATION_NAMES", ""))
ALERT_BOARD_EXCLUDED_DEVICE_STATUSES = parse_csv_set(os.getenv("ALERT_BOARD_EXCLUDED_DEVICE_STATUSES", ""))
# Location type whose locations are the alert-board rows (e.g. "Site"); devices
# in descendant locations roll up into them (#158).  Empty: one row per location.
ALERT_BOARD_SITE_LOCATION_TYPE = os.getenv("ALERT_BOARD_SITE_LOCATION_TYPE", "").strip().lower()
# Nautobot Relationships (keys or labels) that link a location to more
# tenants (#238).  Empty: every Location <-> Tenant relationship.
SITE_TENANT_RELATIONSHIPS = parse_csv_set(os.getenv("SITE_TENANT_RELATIONSHIPS", ""))

# Every setting, for tests that check none is read or set elsewhere.
SETTING_NAMES = tuple(name for name in dir() if name.isupper() and name != "SETTING_NAMES")
