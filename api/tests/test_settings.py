"""Every setting of the current app, parsed the same way (#588).

Ported from the current app's tests/test_config.py, tests/test_app.py
(map view, NAUTOBOT_URL, verify SSL) and tests/test_wireless.py (the wireless
controllers file); the parse helpers keep their names, so the assertions are
the same.
"""

import ipaddress
import os
import pathlib
import re

import pytest
import yaml

from app.core import settings
from app.core.settings import SETTING_NAMES, Settings

API_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = API_ROOT.parent

# Read only to warn that it is still set (#153): neither passed nor documented.
LEGACY_SETTINGS = {"NAUTOBOT_MAPS_DB"}
# Only for the development server.
NOT_PASSED_BY_COMPOSE = {
    # The development server's (python run.py), and development's SQL log.
    "FLASK_DEBUG",
    "FLASK_RUN_PORT",
    "SQL_ECHO",
    # The development server's: where `bun run generate` put the pages (#674: the API
    # image has none; the web image serves them).
    "WEB_BUILD_DIR",
    # compose sets NAUTOBOT_MAPS_DATABASE_URL to its own PostgreSQL, which wins.
    "DATABASE_URL",
    "DATABASE_PASSWORD",
    *LEGACY_SETTINGS,
}


def load(monkeypatch, **env: str) -> Settings:
    """Settings from exactly this environment (and no .env file)."""
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


# --- Plumbing ------------------------------------------------------------------------------


def test_compose_passes_every_setting():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text("utf-8"))
    environment = compose["services"]["api"]["environment"]
    passed = {entry.split("=", 1)[0] for entry in environment}
    missing = sorted(set(SETTING_NAMES) - passed - NOT_PASSED_BY_COMPOSE)
    assert not missing, f"add to docker-compose.yml (api environment): {missing}"


def test_every_setting_is_documented():
    docs = "\n".join(
        (path).read_text(encoding="utf-8")
        for path in (REPO_ROOT / "docs" / "configuration.md", API_ROOT / "README.md")
    )
    missing = sorted(
        name for name in set(SETTING_NAMES) - LEGACY_SETTINGS if f"`{name}`" not in docs
    )
    assert not missing, f"document in docs/configuration.md or api/README.md: {missing}"


def test_only_the_settings_module_reads_the_environment():
    readers = [
        str(path.relative_to(API_ROOT))
        for path in sorted((API_ROOT / "app").rglob("*.py"))
        if path != API_ROOT / "app" / "core" / "settings.py"
        and re.search(r"os\.(getenv|environ)", path.read_text(encoding="utf-8"))
    ]
    assert not readers, f"read settings through app.core.settings: {readers}"


def test_empty_settings_mean_defaults(monkeypatch):
    """compose passes unset variables as "": every setting takes its default
    then, except those where empty is a value."""
    defaults = load(monkeypatch)
    empty = load(monkeypatch, **dict.fromkeys(SETTING_NAMES, ""))
    for field in Settings.model_fields:
        if field in settings.EMPTY_IS_A_VALUE:
            assert not getattr(empty, field), field
        elif field != "database_password":  # None vs "": the URL's own password
            assert getattr(empty, field) == getattr(defaults, field), field
    assert defaults.cache_ttl == 300 and defaults.inventory_sync_interval_seconds == 300


def test_empty_is_a_value_where_compose_says_so():
    """compose passes these with ${NAME-default}: set empty means none (#588)."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for field in settings.EMPTY_IS_A_VALUE:
        name = field.upper()
        assert re.search(rf"{name}=\$\{{{name}-", compose), name


# --- Values --------------------------------------------------------------------------------


def test_flags(monkeypatch):
    for value, expected in [("0", False), ("false", False), ("No", False), ("yes", True)]:
        assert load(monkeypatch, METRICS_ENABLED=value).metrics_enabled is expected
        assert load(monkeypatch, MCP_ENABLED=value).mcp_enabled is expected
    # Anything but 0/false/no is on, as in the current app.
    assert load(monkeypatch, MCP_ENABLED="maybe").mcp_enabled is True
    assert load(monkeypatch, FLASK_DEBUG="yes").flask_debug is False  # only "true"
    assert load(monkeypatch, FLASK_DEBUG="TRUE").flask_debug is True


def test_derived_and_clamped(monkeypatch):
    s = load(monkeypatch, CACHE_TTL="120", LIBRENMS_SYNC_INTERVAL_SECONDS=" 30 ")
    assert (s.inventory_sync_interval_seconds, s.librenms_sync_interval_seconds) == (120, 30)
    s = load(monkeypatch, WIRELESS_SYNC_INTERVAL_SECONDS="5")
    assert s.wireless_sync_interval_seconds == 60
    assert load(monkeypatch, ALERT_DELAY_SECONDS="-3").alert_delay_seconds == 0
    assert load(monkeypatch, WEBHOOK_ALERT_TTL_HOURS="0").webhook_alert_ttl_hours == 1
    s = load(monkeypatch, ALERT_MEDIUM_PERCENT="60", ALERT_CRITICAL_PERCENT="10")
    assert (s.alert_medium_percent, s.alert_critical_percent) == (60, 61)
    s = load(monkeypatch, ALERT_MEDIUM_PERCENT="150", ALERT_CRITICAL_PERCENT="500")
    assert (s.alert_medium_percent, s.alert_critical_percent) == (99, 100)


def test_lists_and_sets(monkeypatch):
    s = load(
        monkeypatch,
        NOTIFY_EMAIL_TO="a@x.example; b@y.example,",
        NOTIFY_TENANTS="Acme;Globex",
        AUTH_ADMIN_GROUPS="Net-Admins, ops",
        SITE_TIERS="Gold, Silver",
        ALERTMANAGER_DEVICE_LABELS="instance, host",
        ACCESS_POINT_ROLES="Access Point;AP",
        MCP_ALLOWED_ORIGINS="https://a.example,https://b.example",
    )
    assert s.notify_email_to == ["a@x.example", "b@y.example"]
    assert s.notify_tenants == {"acme", "globex"}
    assert s.auth_admin_groups == {"net-admins", "ops"}
    assert s.site_tiers == ["gold", "silver"]
    assert s.alertmanager_device_labels == ["instance", "host"]
    assert s.access_point_roles == {"access point", "ap"}
    assert s.mcp_allowed_origins == ["https://a.example", "https://b.example"]
    defaults = load(monkeypatch)
    assert defaults.alert_board_excluded_location_types == {"graveyard", "warehouse"}
    assert defaults.alertmanager_device_labels == ["instance", "hostname", "host", "device"]


def test_empty_exclusions_exclude_nothing(monkeypatch):
    s = load(monkeypatch, ALERT_BOARD_EXCLUDED_LOCATION_TYPES="")
    assert s.alert_board_excluded_location_types == frozenset()
    # compose passes this one with ${NAME:-default}: empty is the default.
    assert load(monkeypatch, ACCESS_POINT_ROLES="").access_point_roles == {"access point"}


def test_tiers_and_levels(monkeypatch, caplog):
    s = load(
        monkeypatch,
        TIER_DEVICE_LEVEL="Gold:Critical, bronze:low, silver:urgent",
        NOTIFY_MIN_LEVEL_BY_TIER="gold:low,silver:loud,bronze",
        WIRELESS_AP_LEVEL="loud",
    )
    assert s.tier_device_level == {"gold": "critical", "bronze": "low"}
    assert "TIER_DEVICE_LEVEL entry 'silver:urgent' ignored" in caplog.text
    assert s.notify_min_level_by_tier == {"gold": "low"}
    assert s.wireless_ap_level == "low" and "WIRELESS_AP_LEVEL 'loud' ignored" in caplog.text


def test_ignored_with_a_warning(monkeypatch, caplog):
    s = load(
        monkeypatch,
        AUTH_SIGN_OUT_URL="//evil.example/out",
        CASE_URL_TEMPLATE="https://itsm.example/ticket",
        AUTH_DEFAULT_ROLE="superuser",
        AVAILABILITY_BASIS="sometimes",
    )
    assert (s.auth_sign_out_url, s.case_url_template, s.auth_default_role) == ("", "", "")
    assert s.availability_basis == "any_down"
    assert "AUTH_SIGN_OUT_URL ignored" in caplog.text
    assert "CASE_URL_TEMPLATE ignored" in caplog.text
    s = load(
        monkeypatch,
        AUTH_SIGN_OUT_URL="/oauth2/sign_out",
        CASE_URL_TEMPLATE="https://itsm.example/ticket/{case}",
        AUTH_DEFAULT_ROLE=" Operator ",
        AVAILABILITY_BASIS="CRITICAL",
    )
    assert s.auth_sign_out_url == "/oauth2/sign_out"
    assert s.case_url_template == "https://itsm.example/ticket/{case}"
    assert (s.auth_default_role, s.availability_basis) == ("operator", "critical")


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("LOG_LEVEL", "loud", "Invalid LOG_LEVEL 'LOUD'"),
        ("LOG_FORMAT", "xml", "Invalid LOG_FORMAT 'xml'"),
        ("NOTIFY_MIN_LEVEL", "urgent", "Invalid NOTIFY_MIN_LEVEL 'urgent'"),
        ("NAUTOBOT_URL", "nautobot.example.com", "Invalid NAUTOBOT_URL configuration"),
        ("AUTH_TRUSTED_PROXIES", "10.0.0.0/8,proxy", "Invalid AUTH_TRUSTED_PROXIES entry 'proxy'"),
    ],
)
def test_a_mistake_stops_startup(monkeypatch, name, value, message):
    with pytest.raises(RuntimeError, match=re.escape(message)):
        load(monkeypatch, **{name: value})


@pytest.mark.parametrize("value", ["0", "100", "-1", "abc", "99,5"])
def test_an_invalid_availability_target_stops_startup(monkeypatch, value):
    """#525: a target outside 0-100 (or not a number) is refused with a clear message."""
    with pytest.raises(RuntimeError, match=re.escape(f"Invalid AVAILABILITY_TARGET_PCT {value!r}")):
        load(monkeypatch, AVAILABILITY_TARGET_PCT=value)


def test_the_availability_target_setting(monkeypatch):
    assert load(monkeypatch, AVAILABILITY_TARGET_PCT="99.95").availability_target_pct == 99.95
    assert load(monkeypatch).availability_target_pct == 99.9


def test_trusted_proxies(monkeypatch):
    s = load(monkeypatch, AUTH_TRUSTED_PROXIES="10.0.0.1; 172.16.0.0/12")
    assert s.auth_trusted_proxies == (
        ipaddress.ip_network("10.0.0.1/32"),
        ipaddress.ip_network("172.16.0.0/12"),
    )


def test_database_url_names(monkeypatch):
    """The current app's NAUTOBOT_MAPS_DATABASE_URL first, DATABASE_URL too; the
    current app's postgresql:// URLs get the asyncpg driver, and so do the psycopg ones
    the new stack took until #664."""
    s = load(monkeypatch, NAUTOBOT_MAPS_DATABASE_URL="postgresql://u:p@db:5432/maps")
    assert s.database_url == "postgresql+asyncpg://u:p@db:5432/maps"
    s = load(monkeypatch, DATABASE_URL="postgres://u@db/maps")
    assert s.database_url == "postgresql+asyncpg://u@db/maps"
    s = load(monkeypatch, DATABASE_URL="postgresql+psycopg://u@db/maps?sslmode=require")
    assert s.database_url == "postgresql+asyncpg://u@db/maps?sslmode=require"
    s = load(monkeypatch, DATABASE_URL="postgresql+asyncpg://u@db/maps")
    assert s.database_url == "postgresql+asyncpg://u@db/maps"
    s = load(
        monkeypatch,
        NAUTOBOT_MAPS_DATABASE_URL="postgresql://u@one/maps",
        DATABASE_URL="postgresql://u@two/maps",
    )
    assert s.sqlalchemy_url().host == "one"
    # Empty (compose passes unset variables as ""): DATABASE_URL counts.
    s = load(monkeypatch, NAUTOBOT_MAPS_DATABASE_URL="", DATABASE_URL="postgresql://u@two/maps")
    assert s.sqlalchemy_url().host == "two"
    assert load(monkeypatch, NAUTOBOT_MAPS_DB="/data/maps.db").legacy_sqlite_db == "/data/maps.db"


def test_secrets_are_kept_as_given(monkeypatch):
    """Whitespace is part of a password, as in the current app; only "" means none."""
    s = load(monkeypatch, DATABASE_PASSWORD="   ", SMTP_PASSWORD=" pw ", NAUTOBOT_TOKEN=" t ")
    assert (s.database_password, s.smtp_password, s.nautobot_token) == ("   ", " pw ", " t ")
    assert load(monkeypatch, DATABASE_PASSWORD="").database_password is None
    # Other settings: whitespace is empty, the default.
    assert load(monkeypatch, CACHE_TTL="  ").cache_ttl == 300


def test_cors_origins_json_or_csv(monkeypatch):
    s = load(monkeypatch, CORS_ORIGINS='["https://maps.example"]')
    assert s.cors_origins == ["https://maps.example"]
    s = load(monkeypatch, CORS_ORIGINS="https://a.example, https://b.example")
    assert s.cors_origins == ["https://a.example", "https://b.example"]


def test_controllers_come_from_the_file_only(monkeypatch):
    with pytest.raises(RuntimeError, match="Set WIRELESS_CONTROLLERS_FILE"):
        load(monkeypatch, WIRELESS_CONTROLLERS='[{"kind": "catalyst_center"}]')


# --- Ported: map view, NAUTOBOT_URL, verify SSL (tests/test_app.py) ---------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", None),
        ("55.7, 12.6, 6", (55.7, 12.6, 6)),
        ("-33.9,151.2,10", (-33.9, 151.2, 10)),
        ("55.7,12.6", None),
        ("55.7,12.6,6,1", None),
        ("91,0,5", None),
        ("0,181,5", None),
        ("55.7,12.6,0", None),
        ("55.7,12.6,20", None),
        ("55.7,12.6,6.5", None),
        ("Copenhagen", None),
        ("nan,0,5", None),
    ],
)
def test_default_view_is_checked(raw, expected, caplog):
    assert settings._map_view(raw) == expected
    assert ("MAP_DEFAULT_VIEW ignored" in caplog.text) == (bool(raw) and expected is None)


def test_default_view_from_the_environment(monkeypatch):
    assert load(monkeypatch, MAP_DEFAULT_VIEW=" 55.7,12.6,6 ").map_default_view == (55.7, 12.6, 6)
    assert load(monkeypatch).map_default_view is None


def test_validate_nautobot_url():
    assert (
        settings._validate_nautobot_url("https://nautobot.example.com/")
        == "https://nautobot.example.com"
    )
    for wrong in ("nautobot.example.com", "NAUTOBOT_URL=https://nautobot.example.com"):
        with pytest.raises(RuntimeError, match="Invalid NAUTOBOT_URL configuration"):
            settings._validate_nautobot_url(wrong)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("", True),
        ("yes", True),
        ("false", False),
        ("no", False),
        ("0", False),
        ("/certs/internal-ca.pem", "/certs/internal-ca.pem"),
    ],
)
def test_verify_setting_values(value, expected):
    """Both upstreams accept true/false or a CA bundle path (#191)."""
    assert settings._verify_ssl(value) == expected


def test_verify_ssl_from_the_environment(monkeypatch):
    s = load(monkeypatch, LIBRENMS_VERIFY_SSL="/certs/ca.pem", NAUTOBOT_VERIFY_SSL="off")
    assert (s.librenms_verify_ssl, s.nautobot_verify_ssl) == ("/certs/ca.pem", False)
    assert load(monkeypatch).nautobot_verify_ssl is True


# --- Ported: the wireless controllers file (tests/test_wireless.py) ---------------------


def write(tmp_path, text: str) -> str:
    path = tmp_path / "wireless.yml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_any_number_of_instances(tmp_path, monkeypatch):
    path = write(
        tmp_path,
        """
catalyst_center:
  - name: DK
    url: https://dnac-dk.example.com/
    username: maps-ro
    password: one
  - name: se
    url: https://dnac-se.example.com
    username: maps-ro
    password: two
    verify_ssl: false
""",
    )
    dk, se = settings._wireless_controllers(path)
    assert (dk["name"], dk["url"], dk["verify_ssl"]) == ("dk", "https://dnac-dk.example.com", True)
    assert (se["kind"], se["password"], se["verify_ssl"]) == ("catalyst_center", "two", False)
    assert settings._wireless_controllers("") == ()
    assert load(monkeypatch, WIRELESS_CONTROLLERS_FILE=path).wireless_controllers == (dk, se)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("aruba_classic: []", "unknown aruba_classic"),
        ("catalyst_center: {name: dk}", "must be a list"),
        (
            "catalyst_center: [{name: 'D K', url: 'https://x', username: u, password: p}]",
            "name must be",
        ),
        ("catalyst_center: [{name: dk, url: 'https://x', username: u}]", "'dk': missing password"),
        (
            "catalyst_center: [{name: dk, url: 'ftp://x', username: u, password: p}]",
            "absolute https",
        ),
        # Credentials travel over it: https, but for this machine (the demo's mock).
        (
            "catalyst_center: [{name: dk, url: 'http://dnac.example', username: u, password: p}]",
            "must be https",
        ),
        (
            "catalyst_center: [{name: dk, url: 'https://x', username: u, password: p, tokn: 1}]",
            "unknown tokn",
        ),
        (
            "catalyst_center: [{name: dk, url: 'https://x', username: u, password: p},"
            " {name: dk, url: 'https://y', username: u, password: p}]",
            "listed twice",
        ),
        ("catalyst_center: [", "can't be read: ParserError"),
    ],
)
def test_a_controller_mistake_stops_startup_and_names_it(tmp_path, text, message):
    with pytest.raises(RuntimeError) as caught:
        settings._wireless_controllers(write(tmp_path, text))
    assert message in str(caught.value)


def test_a_password_is_never_in_the_message(tmp_path, monkeypatch):
    path = write(
        tmp_path, "catalyst_center: [{name: dk, url: 'nope', username: u, password: hunter2}]"
    )
    with pytest.raises(RuntimeError) as caught:
        settings._wireless_controllers(path)
    assert "hunter2" not in str(caught.value)
    # Nor when it stops startup through the settings.
    with pytest.raises(RuntimeError) as caught:
        load(monkeypatch, WIRELESS_CONTROLLERS_FILE=path)
    assert "hunter2" not in str(caught.value)


def test_aruba_central_by_region_or_base_url(tmp_path):
    path = write(
        tmp_path,
        """
aruba_central:
  - {name: eu, region: DE1, client_id: c1, client_secret: s1}
  - {name: cn, region: cn1, client_id: c2, client_secret: s2}
  - {name: lab, base_url: "https://central.lab.example/", token_url: "https://sso.lab.example/token",
     client_id: c3, client_secret: s3, verify_ssl: false}
catalyst_center:
  - {name: eu, url: "https://dnac.example.com", username: u, password: p}
""",
    )
    dnac, eu, cn, lab = settings._wireless_controllers(path)
    assert dnac["kind"] == "catalyst_center", "the same name in two kinds is two instances"
    assert (eu["url"], eu["token_url"]) == (
        "https://de1.api.central.arubanetworks.com",
        "https://sso.common.cloud.hpe.com/as/token.oauth2",
    )
    assert cn["url"] == "https://cn1.api.central.arubanetworks.com.cn"
    assert (lab["url"], lab["token_url"], lab["verify_ssl"]) == (
        "https://central.lab.example",
        "https://sso.lab.example/token",
        False,
    )
    assert (eu["client_id"], eu["client_secret"]) == ("c1", "s1")


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ("{name: eu, client_id: c, client_secret: s}", "give region (e.g. de1) or base_url"),
        (
            "{name: eu, region: de1, base_url: 'https://x', client_id: c, client_secret: s}",
            "one of them",
        ),
        ("{name: eu, region: europe, client_id: c, client_secret: s}", "region must be like"),
        ("{name: eu, region: de1, client_id: c}", "'eu': missing client_secret"),
        (
            "{name: eu, base_url: 'x', client_id: c, client_secret: s}",
            "base_url must be an absolute",
        ),
        (
            "{name: eu, region: de1, token_url: 'x', client_id: c, client_secret: s}",
            "token_url must be an",
        ),
        (
            "{name: eu, region: de1, token_url: 'http://sso.x', client_id: c, client_secret: s}",
            "must be https",
        ),
    ],
)
def test_aruba_central_mistakes(tmp_path, entry, message):
    with pytest.raises(RuntimeError) as caught:
        settings._wireless_controllers(write(tmp_path, f"aruba_central: [{entry}]"))
    assert message in str(caught.value)
    assert "s}" not in str(caught.value)


def test_localhost_may_be_http(tmp_path):
    path = write(
        tmp_path,
        "catalyst_center: [{name: demo, url: 'http://127.0.0.1:8080', username: u, password: p}]",
    )
    assert settings._wireless_controllers(path)[0]["url"] == "http://127.0.0.1:8080"


@pytest.mark.skipif(os.getuid() == 0, reason="root reads any file")
def test_an_unreadable_file_says_what_to_do(tmp_path, monkeypatch):
    # In the container the app is uid/gid 10001, not the file's owner on the host (#517).
    path = write(tmp_path, "catalyst_center: []")
    os.chmod(path, 0)
    with pytest.raises(RuntimeError, match="can't be read by this process"):
        settings._wireless_controllers(path)
    monkeypatch.setattr(os, "getgid", lambda: settings.CONTAINER_GID)
    with pytest.raises(RuntimeError, match="sudo chgrp 10001 config/<file> && chmod 640"):
        settings._wireless_controllers(path)


def test_a_missing_file_says_so(tmp_path):
    with pytest.raises(RuntimeError, match="can't be read: FileNotFoundError"):
        settings._wireless_controllers(str(tmp_path / "none.yml"))
