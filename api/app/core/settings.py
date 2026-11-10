"""Application settings, read from the environment (and `.env`).

Every setting of the current app (nautobot_maps/settings.py) with the same
name and the same parsing (#588): an empty value means the default (compose
passes unset variables as ""), except where "" deliberately means "none"
(EMPTY_IS_A_VALUE). Invalid values that stop the current app at startup stop
this one too (RuntimeError); those it warns about fall back the same way.

Read settings with `get_settings().name` at call time; tests change them with
the `configure` fixture (environment) or `Settings(name=...)`.
"""

import ipaddress
import json
import math
import os
import pathlib
import re
from functools import lru_cache
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import yaml
from loguru import logger
from pydantic import AliasChoices, BeforeValidator, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL, make_url

Role = Literal["viewer", "operator", "admin"]
Level = Literal["low", "medium", "critical"]
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

DEVICE_LEVELS = ("critical", "medium", "low")
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
# The app's user and group in the container: files mounted from ./config must
# be readable by this group (#517).
CONTAINER_GID = 10001

DEFAULT_MAP_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_MAP_TILE_ATTRIBUTION = (
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
)
DEFAULT_GEOCODER_USER_AGENT = "nautobot-maps (+https://github.com/Jackass4life/Nautobot-maps)"

# Settings whose empty value is a value, not "the default": the current
# compose passes it with ${NAME-default}, so setting it empty is deliberate
# (no excluded location types).
EMPTY_IS_A_VALUE = {"alert_board_excluded_location_types"}
# Used exactly as given, as the current app does: whitespace is part of a secret.
VERBATIM = {"database_password", "smtp_password", "nautobot_token"}


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


def _verify_ssl(value: str) -> bool | str:
    """ "true" (default) = verify, "false" = skip, anything else = path to a CA bundle."""
    value = value.strip()
    if value.lower() in ("false", "no", "0", "off"):
        return False
    if value.lower() in ("", "true", "yes", "1", "on"):
        return True
    return value


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


def _flag(value: str, default: bool) -> bool:
    """Off only for "0", "false" or "no"; empty: the default."""
    value = value.strip().lower()
    if not value:
        return default
    return value not in ("0", "false", "no")


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
        "verify_ssl": _verify_ssl(str(entry.get("verify_ssl", "true"))),
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


def unreadable_hint(path: str) -> str:
    """Why *path* can't be read and what to do, for the startup log or error (#517)."""
    hint = f"{path} can't be read by this process (uid {os.getuid()}, gid {os.getgid()})"
    if os.getgid() == CONTAINER_GID:
        hint += (
            f"; give the file to the container's group: sudo chgrp {CONTAINER_GID} config/<file> "
            "&& chmod 640 config/<file> (config/README.md)"
        )
    return hint


def _wireless_controllers(path: str) -> tuple[dict, ...]:
    """Every controller in the file at *path*, checked: fail at startup naming
    the instance and the setting, never echoing a password."""
    if not path:
        return ()
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except PermissionError as exc:
        raise RuntimeError(f"WIRELESS_CONTROLLERS_FILE: {unreadable_hint(path)}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(
            f"WIRELESS_CONTROLLERS_FILE {path!r} can't be read: {type(exc).__name__}"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(
            f"WIRELESS_CONTROLLERS_FILE {path!r}: expected a mapping such as catalyst_center: [...]"
        )
    unknown = set(data) - set(WIRELESS_CONTROLLER_KINDS)
    if unknown:
        raise RuntimeError(
            f"WIRELESS_CONTROLLERS_FILE {path!r}: unknown {', '.join(sorted(map(str, unknown)))}; "
            f"expected {', '.join(sorted(WIRELESS_CONTROLLER_KINDS))}"
        )
    controllers = []
    for kind, (required, optional) in WIRELESS_CONTROLLER_KINDS.items():
        entries = data.get(kind) or []
        if not isinstance(entries, list):
            raise RuntimeError(
                f"WIRELESS_CONTROLLERS_FILE {path!r}: {kind} must be a list of instances"
            )
        names: set[str] = set()
        for index, entry in enumerate(entries, start=1):
            where = f"WIRELESS_CONTROLLERS_FILE {path!r}: {kind} #{index}"
            if not isinstance(entry, dict):
                raise RuntimeError(f"{where}: expected name, url, ...")
            name = str(entry.get("name") or "").strip().lower()
            if not CONTROLLER_NAME.match(name):
                raise RuntimeError(
                    f"{where}: name must be 1-32 of a-z, 0-9, _ and -, "
                    "starting with a letter or digit (e.g. dk)"
                )
            where = f"WIRELESS_CONTROLLERS_FILE {path!r}: {kind} {name!r}"
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


def _sqlalchemy_scheme(url: str) -> str:
    """postgres:// and postgresql:// (the current app's URLs) with the psycopg driver."""
    url = url.strip()
    for scheme in ("postgres://", "postgresql://"):
        if url.lower().startswith(scheme):
            return "postgresql+psycopg://" + url[len(scheme) :]
    return url


# --- Field types: raw text from the environment, or a value given in code ------------------


def _text(parse):
    """Apply *parse* to text (the environment); a value given in code passes as is."""
    return BeforeValidator(lambda value: parse(value) if isinstance(value, str) else value)


Text = Annotated[str, _text(str.strip)]
Url = Annotated[str, _text(lambda v: v.strip().rstrip("/"))]
Lower = Annotated[str, _text(lambda v: v.strip().lower())]
Int = Annotated[int, _text(str.strip)]
CsvSet = Annotated[frozenset[str], NoDecode, _text(lambda v: frozenset(parse_csv_set(v)))]
VerifySsl = Annotated[bool | str, _text(_verify_ssl)]


def Flag(default: bool) -> Any:  # reads like a type
    return Annotated[bool, _text(lambda v: _flag(v, default))]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # --- The new stack's own -----------------------------------------------------------------
    # Optional: the password on its own, as is (no URL encoding). It replaces the one in
    # the database URL, so a password with "@", ":" or "%" needs no escaping (#577).
    database_password: str | None = None
    # Other sites' pages allowed to call the API (a JSON list or comma-separated); the
    # app's own pages share its origin (#594), so none by default.
    cors_origins: Annotated[list[str], NoDecode, _text(_origins)] = []
    # web/'s built pages (`bun run generate`, #594); the image sets it.
    web_build_dir: Text = str(
        pathlib.Path(__file__).resolve().parents[3] / "web" / ".output" / "public"
    )
    sql_echo: bool = False

    # --- Development server (the names are Flask's, kept for existing .env files) ----------
    flask_debug: Annotated[bool, _text(lambda v: v.strip().lower() == "true")] = False
    flask_run_port: str = ""

    # --- Logging (#193) ----------------------------------------------------------------------
    log_level: Annotated[str, _text(lambda v: v.strip().upper())] = "INFO"
    log_format: Lower = "text"

    # --- Nautobot ----------------------------------------------------------------------------
    nautobot_url: Annotated[str, _text(_validate_nautobot_url)] = ""
    nautobot_token: str = ""
    nautobot_api_version: Text = ""
    nautobot_verify_ssl: VerifySsl = True

    # --- Caching and sync intervals ------------------------------------------------------------
    cache_ttl: Int = 300
    cache_type: str = "SimpleCache"
    cache_redis_url: str = ""
    # Default: CACHE_TTL.
    inventory_sync_interval_seconds: Int | None = None
    librenms_sync_interval_seconds: Int | None = None
    # Background scheduler (#154): sync and record history with nobody viewing the board.
    background_sync_enabled: Flag(True) = True

    # --- LibreNMS (optional) -------------------------------------------------------------------
    librenms_url: Url = ""
    librenms_api_token: Text = ""
    librenms_verify_ssl: VerifySsl = True

    # --- Wireless controllers (#508) -----------------------------------------------------------
    wireless_controllers_file: Text = ""
    # Read from WIRELESS_CONTROLLERS_FILE, never from the environment itself.
    wireless_controllers: Annotated[tuple[dict, ...], NoDecode] = ()
    wireless_sync_interval_seconds: Annotated[int, _text(lambda v: max(60, int(v.strip())))] = 300

    # --- History, metrics ----------------------------------------------------------------------
    # Delete resolved alerts older than this many days, once a day (#194); 0 keeps everything.
    alert_history_retention_days: Int = 730
    metrics_enabled: Flag(True) = True

    # --- Notifications (#282) ------------------------------------------------------------------
    notify_min_level: Lower = "critical"
    notify_tenants: CsvSet = frozenset()
    notify_board_url: Url = ""
    notify_summary_threshold: Int = 5
    notify_webhook_url: Text = ""
    notify_webhook_secret: Text = ""
    notify_teams_webhook_url: Text = ""
    notify_email_to: Annotated[list[str], NoDecode, _text(_csv_list)] = []
    smtp_host: Text = ""
    smtp_port: Int = 587
    smtp_username: Text = ""
    smtp_password: str = ""
    smtp_from: Text = ""
    smtp_starttls: Flag(True) = True

    # --- MCP (#250) ----------------------------------------------------------------------------
    mcp_enabled: Flag(False) = False
    mcp_allowed_origins: Annotated[list[str], NoDecode, _text(lambda v: _csv_list(v, ","))] = []

    # --- Map tiles and address search (#197) ---------------------------------------------------
    map_tile_url: Text = DEFAULT_MAP_TILE_URL
    map_tile_attribution: Text = DEFAULT_MAP_TILE_ATTRIBUTION
    # A fixed first view (lat, lon, zoom); None: fit the map to the sites (#362).
    map_default_view: Annotated[
        tuple[float, float, int] | None, NoDecode, _text(lambda v: _map_view(v.strip()))
    ] = None
    geocoder_enabled: Flag(True) = True
    geocoder_url: Url = "https://nominatim.openstreetmap.org"
    geocoder_user_agent: Text = DEFAULT_GEOCODER_USER_AGENT

    # --- Persistence ---------------------------------------------------------------------------
    # The current app's name; when set (not empty) it wins over DATABASE_URL, the new
    # stack's. Two fields, not aliases: an empty first one must not hide the second.
    nautobot_maps_database_url: Annotated[str, _text(_sqlalchemy_scheme)] = ""
    database_url: Annotated[str, _text(_sqlalchemy_scheme)] = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/app"
    )
    db_connect_timeout_seconds: Int = 5
    db_statement_timeout_seconds: Int = 60
    # Removed in #153 (SQLite); only read to warn when it is still set.
    legacy_sqlite_db: Text = Field("", validation_alias="nautobot_maps_db")

    # --- Authentication (#582) -----------------------------------------------------------------
    # disabled: no sign-in, everyone may do everything (development only).
    # header: a trusted sign-in proxy (oauth2-proxy) sets the user and group headers.
    auth_mode: Annotated[Literal["disabled", "header"], _text(lambda v: v.strip().lower())] = (
        "disabled"
    )
    auth_header_user: Text = "X-Forwarded-User"
    auth_header_groups: Text = "X-Forwarded-Groups"
    # Role for a signed-in user in none of the groups; empty (or not a role): no role.
    auth_default_role: Annotated[
        Role | Literal[""],
        _text(lambda v: v.strip().lower() if v.strip().lower() in ROLE_NAMES else ""),
    ] = ""
    # Group names (CSV, case-insensitive) that give each role; the highest wins.
    auth_viewer_groups: CsvSet = frozenset()
    auth_operator_groups: CsvSet = frozenset()
    auth_admin_groups: CsvSet = frozenset()
    # Reads need at least the viewer role too (otherwise reads are public).
    auth_require_viewer: Flag(False) = False
    # Identity headers count only from these peer addresses (CSV of CIDRs) and, when
    # AUTH_PROXY_SECRET is set, only with it in X-Auth-Proxy-Secret.
    auth_trusted_proxies: Annotated[
        tuple[IpNetwork, ...], NoDecode, _text(lambda v: _networks("AUTH_TRUSTED_PROXIES", v))
    ] = (ipaddress.ip_network("127.0.0.1/32"), ipaddress.ip_network("::1/128"))
    auth_proxy_secret: Text = ""
    # Where Sign out goes (#546): a path on this site or an http(s):// address.
    auth_sign_out_url: Annotated[str, _text(lambda v: _sign_out_url(v.strip()))] = ""
    # AUTH_MODE=disabled: allow changing criticality overrides without signing in (#188).
    allow_unauthenticated_writes: Flag(False) = False

    # --- Criticality ---------------------------------------------------------------------------
    critical_role_keywords: Text = ""
    criticality_rules_file: str = ""

    # --- Alert board ---------------------------------------------------------------------------
    alert_board_excluded_location_types: CsvSet = frozenset({"graveyard", "warehouse"})
    alert_board_excluded_location_statuses: CsvSet = frozenset()
    alert_board_excluded_location_tags: CsvSet = frozenset()
    alert_board_excluded_location_names: CsvSet = frozenset()
    alert_board_excluded_device_statuses: CsvSet = frozenset()
    # Down this long before a device alarms (#286); 0: at once.
    alert_delay_seconds: Annotated[int, _text(lambda v: max(0, int(v.strip())))] = 0
    # Up/down this many times within 30 minutes: flapping (#286); 0: off.
    flap_changes: Annotated[int, _text(lambda v: max(0, int(v.strip())))] = 4
    # Alertmanager (#292): labels naming the device (the first present wins), alert names.
    alertmanager_device_labels: Annotated[
        list[str], NoDecode, _text(lambda v: _csv_list(v, ","))
    ] = ["instance", "hostname", "host", "device"]
    alertmanager_alertnames: CsvSet = frozenset()
    # A firing webhook alert not refreshed for this long stops counting.
    webhook_alert_ttl_hours: Annotated[int, _text(lambda v: max(1, int(v.strip())))] = 24
    # Severity thresholds (#293), in percent of a site's monitored devices down.
    alert_medium_percent: Annotated[int, _text(lambda v: min(99, max(1, int(v.strip()))))] = 25
    # Clamped to above ALERT_MEDIUM_PERCENT, at most 100.
    alert_critical_percent: Int = 100
    # Site importance (#293): a Nautobot custom field and its values, most important first.
    site_tier_field: Text = ""
    site_tiers: Annotated[
        list[str], NoDecode, _text(lambda v: [t.lower() for t in _csv_list(v, ",")])
    ] = ["gold", "silver", "bronze"]
    tenant_scope_field: Text = ""
    # Name the customer's own devices on their status page (#555).
    status_page_tenant_devices: Flag(False) = False
    # The location type whose locations are countries (#386).
    country_location_type: Text = "Country"
    # A device's level when down (#377): a custom field, and a default per site tier.
    device_criticality_field: Text = ""
    tier_device_level: Annotated[dict[str, str], NoDecode, _text(_tier_device_level)] = {}
    # A site with only access points down (#521): this level, never notified.
    wireless_ap_level: Lower = "low"
    notify_min_level_by_tier: Annotated[
        dict[str, str], NoDecode, _text(_notify_min_level_by_tier)
    ] = {}
    # A case number's ticket in the ITSM system (#289), with {case}.
    case_url_template: Annotated[str, _text(lambda v: _case_url_template(v.strip()))] = ""
    # gzip large text answers (#349); off where a proxy in front compresses.
    compress_responses: Flag(True) = True

    # --- Availability --------------------------------------------------------------------------
    availability_basis: Annotated[
        Literal["any_down", "critical"],
        _text(lambda v: v.strip().lower() if v.strip().lower() == "critical" else "any_down"),
    ] = "any_down"
    # The availability target in percent (#525): below it is shown in red.
    availability_target_pct: Annotated[float, _text(lambda v: _availability_target(v.strip()))] = (
        99.9
    )
    # Location type whose locations are the board's rows (#158); empty: one row per location.
    alert_board_site_location_type: Lower = ""
    # Nautobot Relationships that link a location to more tenants (#238); empty: all.
    site_tenant_relationships: CsvSet = frozenset()
    # Device roles that are access points (#499), case-folded.
    access_point_roles: Annotated[
        frozenset[str],
        NoDecode,
        _text(lambda v: frozenset(role.casefold() for role in parse_csv_set(v))),
    ] = frozenset({"access point"})

    @model_validator(mode="before")
    @classmethod
    def _empty_means_default(cls, data: Any) -> Any:
        """docker-compose passes unset variables as "": take the default."""
        if isinstance(data, dict):
            return {
                key: value
                for key, value in data.items()
                if not isinstance(value, str)
                or key.lower() in EMPTY_IS_A_VALUE
                # Secrets as given: "   " is a password; only "" means none.
                or (value != "" if key.lower() in VERBATIM else value.strip() != "")
            }
        return data

    @field_validator("log_level")
    @classmethod
    def _log_level(cls, value: str) -> str:
        if value not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise RuntimeError(
                f"Invalid LOG_LEVEL {value!r}: expected DEBUG, INFO, WARNING or ERROR"
            )
        return value

    @field_validator("log_format")
    @classmethod
    def _log_format(cls, value: str) -> str:
        if value not in ("text", "json"):
            raise RuntimeError(f"Invalid LOG_FORMAT {value!r}: expected text or json")
        return value

    @field_validator("notify_min_level")
    @classmethod
    def _notify_min_level(cls, value: str) -> str:
        if value not in ("low", "medium", "critical"):
            raise RuntimeError(
                f"Invalid NOTIFY_MIN_LEVEL {value!r}: expected low, medium or critical"
            )
        return value

    @field_validator("wireless_ap_level")
    @classmethod
    def _wireless_ap_level(cls, value: str) -> str:
        if value not in DEVICE_LEVELS:
            logger.warning(
                "WIRELESS_AP_LEVEL {!r} ignored: expected critical, medium or low; using low", value
            )
            return "low"
        return value

    @field_validator("wireless_controllers", mode="before")
    @classmethod
    def _controllers_not_from_env(cls, value: Any) -> Any:
        if isinstance(value, str):
            raise RuntimeError("Set WIRELESS_CONTROLLERS_FILE, not WIRELESS_CONTROLLERS")
        return value

    @model_validator(mode="after")
    def _derived(self) -> "Settings":
        if self.nautobot_maps_database_url:
            self.database_url = self.nautobot_maps_database_url
        if self.inventory_sync_interval_seconds is None:
            self.inventory_sync_interval_seconds = self.cache_ttl
        if self.librenms_sync_interval_seconds is None:
            self.librenms_sync_interval_seconds = self.cache_ttl
        self.alert_critical_percent = min(
            100, max(self.alert_medium_percent + 1, self.alert_critical_percent)
        )
        if self.wireless_controllers_file and not self.wireless_controllers:
            self.wireless_controllers = _wireless_controllers(self.wireless_controllers_file)
        return self

    def sqlalchemy_url(self) -> URL:
        url = make_url(self.database_url)
        if self.database_password is not None:
            url = url.set(password=self.database_password)
        return url


def env_name(field: str) -> str:
    """The environment variable a setting is read from (the first, for aliases)."""
    info = Settings.model_fields[field]
    alias = info.validation_alias
    if isinstance(alias, AliasChoices):
        alias = alias.choices[0]
    return str(alias or field).upper()


# Not read from the environment: derived from another setting.
DERIVED = {"wireless_controllers"}
# Every setting's environment name, for tests that check each is passed and documented.
SETTING_NAMES = tuple(
    sorted(env_name(field) for field in Settings.model_fields if field not in DERIVED)
)


# Values set in code through app/core/legacy_settings.py (the current app's
# ``settings.NAME = value``): kept across fresh settings, as its module globals were.
OVERRIDES: dict[str, object] = {}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    for field, value in OVERRIDES.items():
        setattr(settings, field, value)
    return settings
