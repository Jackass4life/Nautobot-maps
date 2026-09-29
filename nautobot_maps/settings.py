"""All settings read from the environment (.env), in one place (#165).

Other modules read them as ``settings.NAME`` at call time, never with
``from settings import NAME``, so tests can change a setting with
``monkeypatch.setattr(settings, "NAME", value)``.
"""

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
    """ "true" (default) = verify, "false" = skip, anything else = path to a CA bundle."""
    value = value.strip()
    if value.lower() == "false":
        return False
    if value.lower() == "true":
        return True
    return value


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value not in ("0", "false", "no")


# Flask
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change-me-to-a-random-string")
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"  # python app.py only
FLASK_RUN_PORT = os.getenv("FLASK_RUN_PORT", "")  # python app.py only

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
LIBRENMS_VERIFY_SSL = _flag("LIBRENMS_VERIFY_SSL", True)

# Prometheus metrics at /metrics (#200); "false" turns the endpoint off.
METRICS_ENABLED = _flag("METRICS_ENABLED", True)

# Persistence
NAUTOBOT_MAPS_DATABASE_URL = os.getenv("NAUTOBOT_MAPS_DATABASE_URL", "").strip()
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

# Every setting, for tests that check none is read or set elsewhere.
SETTING_NAMES = tuple(name for name in dir() if name.isupper() and name != "SETTING_NAMES")
