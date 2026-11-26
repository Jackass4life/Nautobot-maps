"""Application settings, read from the environment (and `.env`).

Everything is a setting (#739): the wireless controllers and the criticality rules
are JSON, a CA certificate is PEM text; no file is read.

Every setting of the current app (nautobot_maps/settings.py) with the same
name and the same parsing (#588). No defaults (#760): every setting is in the
environment or .env (api/local.env has them all, with the values the code used
before), or the app won't start; empty means none, and a number or yes/no needs a
value. Invalid values that stop the current app at startup stop this one too
(RuntimeError); those it warns about fall back the same way.

Each field is named exactly like its environment variable (#760): NAUTOBOT_URL is
`settings.NAUTOBOT_URL`. One shared object, read once at import (#754): `from
app.core.settings import settings`, then `settings.NAME` inside the function that needs
it, never at import. Tests change
the object: the `configure` fixture (environment), the `override_settings` fixture or
`overridden()` (in code); or they build their own `Settings(NAME=...)`.
"""

import base64
import binascii
import ipaddress
import json
import math
import os
import pathlib
import re
import ssl
from typing import Any, Literal
from urllib.parse import urlsplit

from loguru import logger
from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url

Role = Literal["viewer", "operator", "admin"]
Level = Literal["low", "medium", "critical"]
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

DEVICE_LEVELS = ("critical", "medium", "low")
API_DIR = pathlib.Path(__file__).resolve().parents[2]
ROLE_NAMES = ("viewer", "operator", "admin")

# Wireless controllers (#508): kind -> (the settings each instance needs, those it may have).
WIRELESS_CONTROLLER_KINDS = {
    "catalyst_center": ({"name", "url", "username", "password"}, {"verify_ssl"}),
    # New Central: a Central-scoped API client from the GreenLake workspace;
    # region (us1, de1, ...) or base_url.
    "aruba_central": (
        {"name", "client_id", "client_secret"},
        {"region", "base_url", "token_url", "verify_ssl"},
    ),
}
ARUBA_CENTRAL_TOKEN_URL = "https://sso.common.cloud.hpe.com/as/token.oauth2"
ARUBA_CENTRAL_REGION = re.compile(r"^[a-z]{2}[0-9]$")
CONTROLLER_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# The file settings before #739, and what replaced each: set, they stop startup.
REMOVED_FILE_SETTINGS = {
    "WIRELESS_CONTROLLERS_FILE": "WIRELESS_CONTROLLERS (the file's contents as JSON)",
    "CRITICALITY_RULES_FILE": "CRITICALITY_RULES (the file's contents)",
    "LDAP_CA_FILE": "EXTRA_CA_PEM (the certificate itself)",
}

# The types whose empty value isn't one (#760): a number or yes/no needs a value.
NEEDS_A_VALUE = (int, float, bool)


# --- Parsing, as nautobot_maps/settings.py does it ------------------------------------------


def parse_csv_set(value: str) -> set[str]:
    """A lower-cased set from a comma/semicolon-separated string."""
    return {item.strip().lower() for item in re.split(r"[;,]", value or "") if item.strip()}


def _csv_list(value: str, separators: str = r"[;,]") -> list[str]:
    return [item.strip() for item in re.split(separators, value or "") if item.strip()]


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


def _verify_ssl(value: str, setting: str = "VERIFY_SSL") -> bool:
    """ "true" (default) = verify, "false" = skip. A CA is EXTRA_CA_PEM (#739): a file's
    path here, as before #739, stops startup and says so."""
    value = value.strip()
    if value.lower() in ("false", "no", "0", "off"):
        return False
    if value.lower() in ("", "true", "yes", "1", "on"):
        return True
    raise RuntimeError(
        f"{setting} is true or false; a CA certificate goes in EXTRA_CA_PEM (#739), "
        "not as a file's path"
    )


PEM_CERTIFICATE = "-----BEGIN CERTIFICATE-----"


def _extra_ca_pem(raw: str) -> str:
    """EXTRA_CA_PEM (#739): one or more PEM certificates, as text (a line break may be
    written \\n, to keep it on one line in .env) or base64 of that text. Checked at
    startup: a context that can't load it would fail every connection later."""
    text = raw.strip().replace("\\n", "\n")
    if text and PEM_CERTIFICATE not in text:
        try:
            text = base64.b64decode("".join(text.split()), validate=True).decode().strip()
        except (binascii.Error, UnicodeDecodeError):
            text = ""
        if PEM_CERTIFICATE not in text:
            raise RuntimeError(
                "EXTRA_CA_PEM: expected PEM certificates (-----BEGIN CERTIFICATE-----...) "
                "or base64 of them"
            )
    if text:
        try:
            ssl.create_default_context(cadata=text)
        except (ssl.SSLError, ValueError) as exc:
            raise RuntimeError(f"EXTRA_CA_PEM can't be read as certificates: {exc}") from None
    return text


def _networks(name: str, value: str) -> tuple[IpNetwork, ...]:
    """Comma/semicolon-separated IP addresses or CIDRs; fail at startup on a typo."""
    networks = []
    for item in re.split(r"[;,]", value or ""):
        if item.strip():
            try:
                networks.append(ipaddress.ip_network(item.strip(), strict=False))
            except ValueError as exc:
                raise RuntimeError(
                    f"Invalid {name} entry {item.strip()!r}: expected an IP address or CIDR"
                ) from exc
    return tuple(networks)


def _flag(value: str) -> bool:
    """Off only for "0", "false" or "no"."""
    return value.strip().lower() not in ("0", "false", "no")


def _http_url(value: object, where: str, setting: str) -> str:
    """An https URL: credentials travel over it. Plain http only on this machine."""
    url = str(value).strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"{where}: {setting} must be an absolute https URL")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(f"{where}: {setting} must be https (only localhost may be http)")
    return url


def _controller(kind: str, name: str, entry: dict, where: str) -> dict:
    """One instance's settings, by kind."""
    common = {
        "kind": kind,
        "name": name,
        "verify_ssl": _verify_ssl(str(entry.get("verify_ssl", "true")), f"{where}: verify_ssl"),
    }
    if kind == "catalyst_center":
        return {
            **common,
            "url": _http_url(entry["url"], where, "url"),
            "username": str(entry["username"]).strip(),
            "password": str(entry["password"]),
        }
    if kind != "aruba_central":
        raise RuntimeError(f"{where}: no settings for {kind}")
    region = str(entry.get("region") or "").strip().lower()
    if bool(region) == bool(str(entry.get("base_url") or "").strip()):
        raise RuntimeError(f"{where}: give region (e.g. de1) or base_url, one of them")
    if region and not ARUBA_CENTRAL_REGION.match(region):
        raise RuntimeError(f"{where}: region must be like us1, de1 or gb1")
    # China's cluster has its own domain.
    domain = (
        "api.central.arubanetworks.com.cn" if region == "cn1" else "api.central.arubanetworks.com"
    )
    return {
        **common,
        "url": f"https://{region}.{domain}"
        if region
        else _http_url(entry["base_url"], where, "base_url"),
        "token_url": _http_url(
            entry.get("token_url") or ARUBA_CENTRAL_TOKEN_URL, where, "token_url"
        ),
        "client_id": str(entry["client_id"]).strip(),
        "client_secret": str(entry["client_secret"]),
    }


def _wireless_controllers(raw: str) -> tuple[dict, ...]:
    """Every controller in WIRELESS_CONTROLLERS (#739: JSON, the file's contents before),
    checked: fail at startup naming the instance and the setting, never echoing a
    password."""
    if not raw.strip():
        return ()
    source = "WIRELESS_CONTROLLERS"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        # The position only: the text holds passwords.
        raise RuntimeError(
            f"{source} isn't valid JSON (line {exc.lineno}, column {exc.colno}); "
            'expected {"catalyst_center": [...], "aruba_central": [...]}'
        ) from None
    if not isinstance(data, dict):
        raise RuntimeError(f'{source}: expected an object such as {{"catalyst_center": [...]}}')
    unknown = set(data) - set(WIRELESS_CONTROLLER_KINDS)
    if unknown:
        raise RuntimeError(
            f"{source}: unknown {', '.join(sorted(map(str, unknown)))}; "
            f"expected {', '.join(sorted(WIRELESS_CONTROLLER_KINDS))}"
        )
    controllers = []
    for kind, (required, optional) in WIRELESS_CONTROLLER_KINDS.items():
        entries = data.get(kind) or []
        if not isinstance(entries, list):
            raise RuntimeError(f"{source}: {kind} must be a list of instances")
        names: set[str] = set()
        for index, entry in enumerate(entries, start=1):
            where = f"{source}: {kind} #{index}"
            if not isinstance(entry, dict):
                raise RuntimeError(f"{where}: expected name, url, ...")
            name = str(entry.get("name") or "").strip().lower()
            if not CONTROLLER_NAME.match(name):
                raise RuntimeError(
                    f"{where}: name must be 1-32 of a-z, 0-9, _ and -, "
                    "starting with a letter or digit (e.g. dk)"
                )
            where = f"{source}: {kind} {name!r}"
            if name in names:
                raise RuntimeError(f"{where} is listed twice")
            names.add(name)
            extra = set(entry) - required - optional
            if extra:
                raise RuntimeError(f"{where}: unknown {', '.join(sorted(map(str, extra)))}")
            missing = sorted(key for key in required if not str(entry.get(key) or "").strip())
            if missing:
                raise RuntimeError(f"{where}: missing {', '.join(missing)}")
            controllers.append(_controller(kind, name, entry, where))
    return tuple(controllers)


def _criticality_rules(raw: str) -> dict[str, list[str]]:
    """CRITICALITY_RULES (#739: the JSON file's contents before): critical role keywords
    per location type, {"site": ["core", "router"], "default": [...]}, lower-cased."""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"CRITICALITY_RULES isn't valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from None
    if isinstance(data, dict):
        # "_comment" and the like: notes, as the file before #739 had.
        data = {kind: keywords for kind, keywords in data.items() if not kind.startswith("_")}
    if not isinstance(data, dict) or not all(
        isinstance(keywords, list) and all(isinstance(k, str) for k in keywords)
        for keywords in data.values()
    ):
        raise RuntimeError('CRITICALITY_RULES: expected {"<location type>": ["keyword", ...], ...}')
    return {
        str(kind).lower(): [keyword.lower() for keyword in keywords]
        for kind, keywords in data.items()
    }


def _map_view(raw: str) -> tuple[float, float, int] | None:
    """MAP_DEFAULT_VIEW "lat,lon,zoom" (#362), or None when empty or not one:
    then the map fits itself to the sites."""
    if not raw:
        return None
    try:
        lat_s, lon_s, zoom_s = (part.strip() for part in raw.split(","))
        lat, lon, zoom = float(lat_s), float(lon_s), int(zoom_s)
        if -90 <= lat <= 90 and -180 <= lon <= 180 and 1 <= zoom <= 19:
            return lat, lon, zoom
    except ValueError:
        pass
    logger.warning("MAP_DEFAULT_VIEW ignored: expected lat,lon,zoom, e.g. 55.7,12.6,6")
    return None


def _tier_device_level(raw: str) -> dict[str, str]:
    """TIER_DEVICE_LEVEL "gold:critical,bronze:low" (#377); bad entries warned about."""
    levels = {}
    for item in filter(None, (item.strip() for item in raw.split(","))):
        tier, _, level = (part.strip().lower() for part in item.partition(":"))
        if tier and level in DEVICE_LEVELS:
            levels[tier] = level
        else:
            logger.warning(
                "TIER_DEVICE_LEVEL entry {!r} ignored: expected tier:level, "
                "the level critical, medium or low",
                item,
            )
    return levels


def _notify_min_level_by_tier(raw: str) -> dict[str, str]:
    """ "gold:low,silver:medium": NOTIFY_MIN_LEVEL for those tiers."""
    return {
        tier.strip().lower(): level.strip().lower()
        for tier, _, level in (item.partition(":") for item in raw.split(",") if ":" in item)
        if tier.strip() and level.strip().lower() in ("low", "medium", "critical")
    }


def _sign_out_url(url: str) -> str:
    """A path on this site or an http(s):// address (#546); otherwise ignored."""
    if url and not (
        (url.startswith("/") and not url.startswith("//"))
        or url.lower().startswith(("https://", "http://"))
    ):
        logger.warning(
            "AUTH_SIGN_OUT_URL ignored: it must be a path (/...) or an http(s):// address"
        )
        return ""
    return url


def _case_url_template(template: str) -> str:
    """An http(s) template with {case} (#289); otherwise ignored."""
    if template and (
        not template.lower().startswith(("https://", "http://")) or "{case}" not in template
    ):
        logger.warning(
            "CASE_URL_TEMPLATE ignored: it must start with http(s):// and contain {case}"
        )
        return ""
    return template


def _availability_target(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not 0 < value < 100:
        raise RuntimeError(
            f"Invalid AVAILABILITY_TARGET_PCT {raw!r}: expected a percentage above 0 "
            "and below 100, such as 99.9"
        )
    return value


def _origins(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("["):
        return [str(origin) for origin in json.loads(raw)]
    return _csv_list(raw, ",")


# The URL schemes given the asyncpg driver: the current app's (postgres://,
# postgresql://), and psycopg's, which the new stack used until #664.
_POSTGRES_SCHEMES = (
    "postgres://",
    "postgresql://",
    "postgresql+psycopg://",
    "postgresql+psycopg2://",
)


def _sqlalchemy_scheme(url: str) -> str:
    """A PostgreSQL URL with the asyncpg driver (the app's only one since #664)."""
    url = url.strip()
    for scheme in _POSTGRES_SCHEMES:
        if url.lower().startswith(scheme):
            return "postgresql+asyncpg://" + url[len(scheme) :]
    return url


# --- Parsing the environment's text: one validator per kind of value ------------------------


def _parse(parse, *fields: str) -> Any:
    """A validator that applies *parse* to the text of *fields* (the environment); a
    value given in code passes as is."""
    return field_validator(*fields, mode="before")(
        lambda value: parse(value) if isinstance(value, str) else value
    )


def _at_least(minimum: int):
    return lambda value: max(minimum, int(value.strip()))


def _url(value: str) -> str:
    return value.strip().rstrip("/")


def _lower(value: str) -> str:
    return value.strip().lower()


def _build_dir(value: str) -> str:
    """Empty: no pages here (the web container serves them); a relative path is from api/."""
    value = value.strip()
    return str((API_DIR / value).resolve()) if value else ""


def _csv_set(value: str) -> frozenset[str]:
    return frozenset(parse_csv_set(value))


# Off only for "0", "false" or "no" (_flag).
FLAGS = (
    "BACKGROUND_SYNC_ENABLED",
    "METRICS_ENABLED",
    "SMTP_STARTTLS",
    "MCP_ENABLED",
    "GEOCODER_ENABLED",
    "AUTH_REQUIRE_VIEWER",
    "AUTH_GROUP_ROLES",
    "ALLOW_UNAUTHENTICATED_WRITES",
    "STATUS_PAGE_TENANT_DEVICES",
    "COMPRESS_RESPONSES",
)


class Settings(BaseSettings):
    # Field names are the environment's names (#760); enable_decoding=False: lists and
    # sets are comma-separated text, parsed below, not JSON.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", enable_decoding=False)

    # --- The new stack's own -----------------------------------------------------------------
    # Optional: the password on its own, as is (no URL encoding). It replaces the one in
    # the database URL, so a password with "@", ":" or "%" needs no escaping (#577).
    DATABASE_PASSWORD: str | None
    # Other sites' pages allowed to call the API (a JSON list or comma-separated); the
    # app's own pages share its origin (#594), so usually none.
    CORS_ORIGINS: list[str]
    # web/'s built pages (`bun run generate`, #594), relative to api/; empty: none here,
    # the web container serves them (#674).
    WEB_BUILD_DIR: str
    SQL_ECHO: bool

    # --- Development server (the names are Flask's, kept for existing .env files) ----------
    FLASK_DEBUG: bool
    FLASK_RUN_PORT: str

    # --- Logging (#193) ----------------------------------------------------------------------
    LOG_LEVEL: str
    LOG_FORMAT: str

    # --- Nautobot ----------------------------------------------------------------------------
    NAUTOBOT_URL: str
    NAUTOBOT_TOKEN: str
    NAUTOBOT_API_VERSION: str
    NAUTOBOT_VERIFY_SSL: bool
    # A company CA (#739): PEM text, trusted with the system's CAs by Nautobot, LibreNMS
    # and the wireless controllers' connections.
    EXTRA_CA_PEM: str

    # --- Caching and sync intervals ------------------------------------------------------------
    CACHE_TTL: int
    CACHE_TYPE: str
    CACHE_REDIS_URL: str
    # Empty: CACHE_TTL.
    INVENTORY_SYNC_INTERVAL_SECONDS: int | None
    LIBRENMS_SYNC_INTERVAL_SECONDS: int | None
    # Background scheduler (#154): sync and record history with nobody viewing the board.
    BACKGROUND_SYNC_ENABLED: bool

    # --- LibreNMS (optional) -------------------------------------------------------------------
    LIBRENMS_URL: str
    LIBRENMS_API_TOKEN: str
    LIBRENMS_VERIFY_SSL: bool

    # --- Wireless controllers (#508) -----------------------------------------------------------
    # JSON (#739): {"catalyst_center": [...], "aruba_central": [...]}.
    WIRELESS_CONTROLLERS: tuple[dict, ...]
    WIRELESS_SYNC_INTERVAL_SECONDS: int

    # --- History, metrics ----------------------------------------------------------------------
    # Delete resolved alerts older than this many days, once a day (#194); 0 keeps everything.
    ALERT_HISTORY_RETENTION_DAYS: int
    METRICS_ENABLED: bool

    # --- Notifications (#282) ------------------------------------------------------------------
    NOTIFY_MIN_LEVEL: str
    NOTIFY_TENANTS: frozenset[str]
    NOTIFY_BOARD_URL: str
    NOTIFY_SUMMARY_THRESHOLD: int
    NOTIFY_WEBHOOK_URL: str
    NOTIFY_WEBHOOK_SECRET: str
    NOTIFY_TEAMS_WEBHOOK_URL: str
    NOTIFY_EMAIL_TO: list[str]
    SMTP_HOST: str
    SMTP_PORT: int
    SMTP_USERNAME: str
    SMTP_PASSWORD: str
    SMTP_FROM: str
    SMTP_STARTTLS: bool

    # --- MCP (#250) ----------------------------------------------------------------------------
    MCP_ENABLED: bool
    MCP_ALLOWED_ORIGINS: list[str]

    # --- Map tiles and address search (#197) ---------------------------------------------------
    MAP_TILE_URL: str
    MAP_TILE_ATTRIBUTION: str
    # A fixed first view (lat, lon, zoom); None: fit the map to the sites (#362).
    MAP_DEFAULT_VIEW: tuple[float, float, int] | None
    GEOCODER_ENABLED: bool
    GEOCODER_URL: str
    GEOCODER_USER_AGENT: str

    # --- Persistence ---------------------------------------------------------------------------
    # The current app's name; when set (not empty) it wins over DATABASE_URL, the new
    # stack's. Two fields, not aliases: an empty first one must not hide the second.
    NAUTOBOT_MAPS_DATABASE_URL: str
    DATABASE_URL: str
    DB_CONNECT_TIMEOUT_SECONDS: int
    DB_STATEMENT_TIMEOUT_SECONDS: int

    # --- Authentication (#582) -----------------------------------------------------------------
    # disabled: no sign-in, everyone may do everything (development only).
    # header: a trusted sign-in proxy (oauth2-proxy) sets the user and group headers.
    AUTH_MODE: Literal["disabled", "header"]
    AUTH_HEADER_USER: str
    AUTH_HEADER_GROUPS: str
    # Role for a signed-in user in none of the groups; empty (or not a role): no role.
    AUTH_DEFAULT_ROLE: Role | Literal[""]
    # Group names (CSV, case-insensitive) that give each role; the first of admin,
    # operator (NWO), noc, customer, viewer wins (#691): one role, never merged.
    AUTH_VIEWER_GROUPS: frozenset[str]
    AUTH_OPERATOR_GROUPS: frozenset[str]
    AUTH_ADMIN_GROUPS: frozenset[str]
    AUTH_NOC_GROUPS: frozenset[str]
    # The NOC role was DOV until 0.5.1 (#703): read when AUTH_NOC_GROUPS is empty.
    AUTH_DOV_GROUPS: frozenset[str]
    AUTH_CUSTOMER_GROUPS: frozenset[str]
    # Reads need at least the viewer role too (otherwise reads are public).
    AUTH_REQUIRE_VIEWER: bool
    # Someone signed in without an identity (#715): a role from AUTH_*_GROUPS as before
    # (true), or no access (false: set it once everyone has an identity).
    AUTH_GROUP_ROLES: bool
    # Identity headers count only from these peer addresses (CSV of CIDRs) and, when
    # AUTH_PROXY_SECRET is set, only with it in X-Auth-Proxy-Secret.
    AUTH_TRUSTED_PROXIES: tuple[IpNetwork, ...]
    AUTH_PROXY_SECRET: str
    # Where Sign out goes (#546): a path on this site or an http(s):// address.
    AUTH_SIGN_OUT_URL: str
    # --- Sign-in with AD (#717, #782): off until AD_HOST is set ---------------------------
    # The domain controller's host name, e.g. dc1.example.local: sit_core's
    # BasicAuthenticator binds there as the user (the company's package, installed apart).
    AD_HOST: str
    # Signs the app's own sign-in tokens: required with AD_HOST, at least 32 characters.
    JWT_SIGNING_SECRET: str
    JWT_LIFETIME_SECONDS: int
    JWT_ISSUER: str
    JWT_AUDIENCE: str

    # AUTH_MODE=disabled: allow changing criticality overrides without signing in (#188).
    ALLOW_UNAUTHENTICATED_WRITES: bool

    # --- Criticality ---------------------------------------------------------------------------
    CRITICAL_ROLE_KEYWORDS: str
    # JSON (#739): critical role keywords per location type.
    CRITICALITY_RULES: dict[str, list[str]]

    # --- Alert board ---------------------------------------------------------------------------
    ALERT_BOARD_EXCLUDED_LOCATION_TYPES: frozenset[str]
    ALERT_BOARD_EXCLUDED_LOCATION_STATUSES: frozenset[str]
    ALERT_BOARD_EXCLUDED_LOCATION_TAGS: frozenset[str]
    ALERT_BOARD_EXCLUDED_LOCATION_NAMES: frozenset[str]
    ALERT_BOARD_EXCLUDED_DEVICE_STATUSES: frozenset[str]
    # Down this long before a device alarms (#286); 0: at once.
    ALERT_DELAY_SECONDS: int
    # Up/down this many times within 30 minutes: flapping (#286); 0: off.
    FLAP_CHANGES: int
    # Alertmanager (#292): labels naming the device (the first present wins), alert names.
    ALERTMANAGER_DEVICE_LABELS: list[str]
    ALERTMANAGER_ALERTNAMES: frozenset[str]
    # A firing webhook alert not refreshed for this long stops counting.
    WEBHOOK_ALERT_TTL_HOURS: int
    # Severity thresholds (#293), in percent of a site's monitored devices down.
    ALERT_MEDIUM_PERCENT: int
    # Clamped to above ALERT_MEDIUM_PERCENT, at most 100.
    ALERT_CRITICAL_PERCENT: int
    # Site importance (#293): a Nautobot custom field and its values, most important first.
    SITE_TIER_FIELD: str
    SITE_TIERS: list[str]
    TENANT_SCOPE_FIELD: str
    # Name the customer's own devices on their status page (#555).
    STATUS_PAGE_TENANT_DEVICES: bool
    # The location type whose locations are countries (#386).
    COUNTRY_LOCATION_TYPE: str
    # A device's level when down (#377): a custom field, and a default per site tier.
    DEVICE_CRITICALITY_FIELD: str
    TIER_DEVICE_LEVEL: dict[str, str]
    # A site with only access points down (#521): this level, never notified.
    WIRELESS_AP_LEVEL: str
    NOTIFY_MIN_LEVEL_BY_TIER: dict[str, str]
    # A case number's ticket in the ITSM system (#289), with {case}.
    CASE_URL_TEMPLATE: str
    # gzip large text answers (#349); off where a proxy in front compresses.
    COMPRESS_RESPONSES: bool

    # --- Availability --------------------------------------------------------------------------
    AVAILABILITY_BASIS: Literal["any_down", "critical"]
    # The availability target in percent (#525): below it is shown in red.
    AVAILABILITY_TARGET_PCT: float
    # Location type whose locations are the board's rows (#158); empty: one row per location.
    ALERT_BOARD_SITE_LOCATION_TYPE: str
    # Nautobot Relationships that link a location to more tenants (#238); empty: all.
    SITE_TENANT_RELATIONSHIPS: frozenset[str]
    # Device roles that are access points (#499), case-folded.
    ACCESS_POINT_ROLES: frozenset[str]

    # --- Not settings: names only read to refuse them, so not needed in .env --------------
    # Removed in #739: set, they stop startup and say what replaced them.
    WIRELESS_CONTROLLERS_FILE: str = ""
    CRITICALITY_RULES_FILE: str = ""
    LDAP_CA_FILE: str = ""
    # Removed in #782 (sign-in through sit_core needs only AD_HOST): set, it stops startup.
    LDAP_URL: str = ""
    # Removed in #153 (SQLite); only read to warn when it is still set.
    NAUTOBOT_MAPS_DB: str = ""

    # --- How the environment's text is read ----------------------------------------------------
    _strip = _parse(
        str.strip,
        "NAUTOBOT_API_VERSION",
        "CACHE_TTL",
        "INVENTORY_SYNC_INTERVAL_SECONDS",
        "LIBRENMS_SYNC_INTERVAL_SECONDS",
        "LIBRENMS_API_TOKEN",
        "ALERT_HISTORY_RETENTION_DAYS",
        "NOTIFY_SUMMARY_THRESHOLD",
        "NOTIFY_WEBHOOK_URL",
        "NOTIFY_WEBHOOK_SECRET",
        "NOTIFY_TEAMS_WEBHOOK_URL",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_FROM",
        "MAP_TILE_URL",
        "MAP_TILE_ATTRIBUTION",
        "GEOCODER_USER_AGENT",
        "DB_CONNECT_TIMEOUT_SECONDS",
        "DB_STATEMENT_TIMEOUT_SECONDS",
        "NAUTOBOT_MAPS_DB",
        "AUTH_HEADER_USER",
        "AUTH_HEADER_GROUPS",
        "AUTH_PROXY_SECRET",
        "AD_HOST",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
        "CRITICAL_ROLE_KEYWORDS",
        "WIRELESS_CONTROLLERS_FILE",
        "CRITICALITY_RULES_FILE",
        "LDAP_CA_FILE",
        "LDAP_URL",
        "ALERT_CRITICAL_PERCENT",
        "SITE_TIER_FIELD",
        "TENANT_SCOPE_FIELD",
        "COUNTRY_LOCATION_TYPE",
        "DEVICE_CRITICALITY_FIELD",
    )
    _web_build_dir = _parse(_build_dir, "WEB_BUILD_DIR")
    _urls = _parse(_url, "LIBRENMS_URL", "NOTIFY_BOARD_URL", "GEOCODER_URL")
    _lowered = _parse(
        _lower,
        "LOG_FORMAT",
        "NOTIFY_MIN_LEVEL",
        "AUTH_MODE",
        "WIRELESS_AP_LEVEL",
        "ALERT_BOARD_SITE_LOCATION_TYPE",
    )
    _csv_sets = _parse(
        _csv_set,
        "NOTIFY_TENANTS",
        "AUTH_VIEWER_GROUPS",
        "AUTH_OPERATOR_GROUPS",
        "AUTH_ADMIN_GROUPS",
        "AUTH_NOC_GROUPS",
        "AUTH_DOV_GROUPS",
        "AUTH_CUSTOMER_GROUPS",
        "ALERT_BOARD_EXCLUDED_LOCATION_TYPES",
        "ALERT_BOARD_EXCLUDED_LOCATION_STATUSES",
        "ALERT_BOARD_EXCLUDED_LOCATION_TAGS",
        "ALERT_BOARD_EXCLUDED_LOCATION_NAMES",
        "ALERT_BOARD_EXCLUDED_DEVICE_STATUSES",
        "ALERTMANAGER_ALERTNAMES",
        "SITE_TENANT_RELATIONSHIPS",
    )
    _comma_lists = _parse(
        lambda v: _csv_list(v, ","), "MCP_ALLOWED_ORIGINS", "ALERTMANAGER_DEVICE_LABELS"
    )
    _at_least_0 = _parse(_at_least(0), "ALERT_DELAY_SECONDS", "FLAP_CHANGES")
    _at_least_1 = _parse(_at_least(1), "WEBHOOK_ALERT_TTL_HOURS")
    _at_least_60 = _parse(_at_least(60), "WIRELESS_SYNC_INTERVAL_SECONDS", "JWT_LIFETIME_SECONDS")
    _database_urls = _parse(_sqlalchemy_scheme, "NAUTOBOT_MAPS_DATABASE_URL", "DATABASE_URL")
    _cors_origins = _parse(_origins, "CORS_ORIGINS")
    _flask_debug = _parse(lambda v: v.strip().lower() == "true", "FLASK_DEBUG")
    _log_level_text = _parse(lambda v: v.strip().upper(), "LOG_LEVEL")
    _nautobot_url = _parse(_validate_nautobot_url, "NAUTOBOT_URL")
    _extra_ca = _parse(_extra_ca_pem, "EXTRA_CA_PEM")
    _controllers = _parse(_wireless_controllers, "WIRELESS_CONTROLLERS")
    _email_to = _parse(_csv_list, "NOTIFY_EMAIL_TO")
    _map_default_view = _parse(lambda v: _map_view(v.strip()), "MAP_DEFAULT_VIEW")
    _default_role = _parse(
        lambda v: v.strip().lower() if v.strip().lower() in ROLE_NAMES else "",
        "AUTH_DEFAULT_ROLE",
    )
    _trusted_proxies = _parse(
        lambda v: _networks("AUTH_TRUSTED_PROXIES", v), "AUTH_TRUSTED_PROXIES"
    )
    _sign_out = _parse(lambda v: _sign_out_url(v.strip()), "AUTH_SIGN_OUT_URL")
    _rules = _parse(_criticality_rules, "CRITICALITY_RULES")
    _medium_percent = _parse(lambda v: min(99, max(1, int(v.strip()))), "ALERT_MEDIUM_PERCENT")
    _site_tiers = _parse(lambda v: [t.lower() for t in _csv_list(v, ",")], "SITE_TIERS")
    _tier_levels = _parse(_tier_device_level, "TIER_DEVICE_LEVEL")
    _min_level_by_tier = _parse(_notify_min_level_by_tier, "NOTIFY_MIN_LEVEL_BY_TIER")
    _case_url = _parse(lambda v: _case_url_template(v.strip()), "CASE_URL_TEMPLATE")
    _basis = _parse(
        lambda v: v.strip().lower() if v.strip().lower() == "critical" else "any_down",
        "AVAILABILITY_BASIS",
    )
    _target = _parse(lambda v: _availability_target(v.strip()), "AVAILABILITY_TARGET_PCT")
    _ap_roles = _parse(
        lambda v: frozenset(role.casefold() for role in parse_csv_set(v)), "ACCESS_POINT_ROLES"
    )

    _flags = _parse(_flag, *FLAGS)
    # Empty: none. The password as given otherwise ("   " is one); the URL's own then.
    _password = _parse(lambda v: v or None, "DATABASE_PASSWORD")
    # Empty: CACHE_TTL.
    _intervals = _parse(
        lambda v: v.strip() or None,
        "INVENTORY_SYNC_INTERVAL_SECONDS",
        "LIBRENMS_SYNC_INTERVAL_SECONDS",
    )

    @field_validator("NAUTOBOT_VERIFY_SSL", "LIBRENMS_VERIFY_SSL", mode="before")
    @classmethod
    def _verify(cls, value: Any, info: ValidationInfo) -> Any:
        return _verify_ssl(value, info.field_name) if isinstance(value, str) else value

    @model_validator(mode="before")
    @classmethod
    def _every_setting_given(cls, data: Any) -> Any:
        """No defaults (#760): every setting is in the environment or .env, a number or
        yes/no with a value. One error naming them all, not one per field."""
        if not isinstance(data, dict):
            return data
        given = {str(key).upper(): value for key, value in data.items()}
        missing = [name for name in REQUIRED if name not in given]
        empty = [
            name
            for name in REQUIRED
            if isinstance(given.get(name), str)
            and not given[name].strip()
            and cls.model_fields[name].annotation in NEEDS_A_VALUE
        ]
        problems = []
        if missing:
            problems.append(f"missing in .env: {', '.join(missing)}")
        if empty:
            problems.append(f"empty, but need a value: {', '.join(empty)}")
        if problems:
            raise RuntimeError(
                f"Settings {'; '.join(problems)}. Every setting is in .env (#760): "
                "python -m app.check_env --fill .env >> .env adds the missing ones, with the "
                "values the code used before"
            )
        return data

    @field_validator("LOG_LEVEL")
    @classmethod
    def _log_level(cls, value: str) -> str:
        if value not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise RuntimeError(
                f"Invalid LOG_LEVEL {value!r}: expected DEBUG, INFO, WARNING or ERROR"
            )
        return value

    @field_validator("LOG_FORMAT")
    @classmethod
    def _log_format(cls, value: str) -> str:
        if value not in ("text", "json"):
            raise RuntimeError(f"Invalid LOG_FORMAT {value!r}: expected text or json")
        return value

    @field_validator("NOTIFY_MIN_LEVEL")
    @classmethod
    def _notify_min_level(cls, value: str) -> str:
        if value not in ("low", "medium", "critical"):
            raise RuntimeError(
                f"Invalid NOTIFY_MIN_LEVEL {value!r}: expected low, medium or critical"
            )
        return value

    @field_validator("WIRELESS_AP_LEVEL")
    @classmethod
    def _wireless_ap_level(cls, value: str) -> str:
        if value not in DEVICE_LEVELS:
            logger.warning(
                "WIRELESS_AP_LEVEL {!r} ignored: expected critical, medium or low; using low", value
            )
            return "low"
        return value

    @model_validator(mode="after")
    def _derived(self) -> "Settings":
        if self.NAUTOBOT_MAPS_DATABASE_URL:
            self.DATABASE_URL = self.NAUTOBOT_MAPS_DATABASE_URL
        if not self.DATABASE_URL:
            raise RuntimeError("Set NAUTOBOT_MAPS_DATABASE_URL (or DATABASE_URL): the database")
        if self.INVENTORY_SYNC_INTERVAL_SECONDS is None:
            self.INVENTORY_SYNC_INTERVAL_SECONDS = self.CACHE_TTL
        if self.LIBRENMS_SYNC_INTERVAL_SECONDS is None:
            self.LIBRENMS_SYNC_INTERVAL_SECONDS = self.CACHE_TTL
        self.ALERT_CRITICAL_PERCENT = min(
            100, max(self.ALERT_MEDIUM_PERCENT + 1, self.ALERT_CRITICAL_PERCENT)
        )
        for field, instead in REMOVED_FILE_SETTINGS.items():
            if getattr(self, field):
                raise RuntimeError(f"{field} was removed (#739): set {instead} in .env instead")
        if self.AUTH_PROXY_SECRET and self.AUTH_MODE != "header":
            # The bundled proxy's settings are .env's since #739, AUTH_MODE too: without
            # it the app would ignore the proxy's sign-in.
            raise RuntimeError(
                "AUTH_PROXY_SECRET is set but AUTH_MODE isn't header: behind the bundled "
                "proxy set AUTH_MODE=header, AUTH_REQUIRE_VIEWER=true and "
                "AUTH_TRUSTED_PROXIES=<the proxy's address>/32 in .env (.env.example)"
            )
        if self.LDAP_URL:
            raise RuntimeError(
                "LDAP_URL was removed (#782): set AD_HOST (the domain controller's host name, "
                "e.g. dc1.example.local) instead; LDAP_BASE_DN, LDAP_BIND_DN, "
                "LDAP_BIND_PASSWORD and the other LDAP_* settings aren't needed any more"
            )
        if self.AD_HOST:
            _check_ad_login(self)
        return self

    def sqlalchemy_url(self) -> URL:
        url = make_url(self.DATABASE_URL)
        if self.DATABASE_PASSWORD is not None:
            url = url.set(password=self.DATABASE_PASSWORD)
        return url


MIN_JWT_SECRET_LENGTH = 32


def _check_ad_login(settings: "Settings") -> None:
    """AD sign-in on (#717): refused at start when it can't be safe."""
    if len(settings.JWT_SIGNING_SECRET) < MIN_JWT_SECRET_LENGTH:
        raise RuntimeError(
            f"AD_HOST is set: JWT_SIGNING_SECRET must be at least {MIN_JWT_SECRET_LENGTH} "
            "characters (e.g. openssl rand -base64 48)"
        )


# Every setting the API needs in its environment or .env (#760): all but the names only
# read to refuse them.
REQUIRED = tuple(name for name, info in Settings.model_fields.items() if info.is_required())


def env_name(field: str) -> str:
    """The environment variable a setting is read from: its name (#760)."""
    return field


def environment_names() -> list[str]:
    """The names in the environment (never their values), for the startup warning about
    names nothing reads (app/core/env_names.py)."""
    return [name.upper() for name in os.environ]


# Every setting's environment name, for tests that check each is documented; the
# removed ones only stop startup.
SETTING_NAMES = tuple(
    sorted(env_name(field) for field in Settings.model_fields if field not in REMOVED_FILE_SETTINGS)
)


# The settings, read from the environment and .env once (#754). Tests change this object
# (tests/overrides.py), never rebind it, so every module that imported it sees the change.
settings = Settings()
