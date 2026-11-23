"""Every setting of the current app, parsed the same way (#588).

Ported from the current app's tests/test_config.py, tests/test_app.py
(map view, NAUTOBOT_URL, verify SSL) and tests/test_wireless.py (the wireless
controllers, a file until #739: JSON now, written here as YAML for reading); the
parse helpers keep their names, so the assertions are the same.
"""

import base64
import ipaddress
import json
import pathlib
import re
import subprocess

import pytest
import yaml
from dotenv import dotenv_values

from app.core import settings
from app.core.settings import SETTING_NAMES, Settings

API_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = API_ROOT.parent


def local_env() -> dict[str, str]:
    """api/local.env: every setting, with the values the code had before #760."""
    return {name: value or "" for name, value in dotenv_values(API_ROOT / "local.env").items()}


def load(monkeypatch, **env: str) -> Settings:
    """Settings from api/local.env with *env* on top (and no .env file)."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
    for name, value in {**local_env(), **env}.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


# --- Plumbing ------------------------------------------------------------------------------


def test_env_example_parses_as_settings(monkeypatch):
    """Its values, as compose passes them, start the app: the JSON settings too."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    values = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", text, flags=re.MULTILINE))
    known = {name: value for name, value in values.items() if name in SETTING_NAMES}
    # On its own (#760): no api/local.env under it.
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
    for name, value in known.items():
        monkeypatch.setenv(name, value.strip("'"))
    assert Settings(_env_file=None).CACHE_TYPE == "RedisCache"
    # The commented examples parse too.
    examples = dict(re.findall(r"^# ([A-Z][A-Z0-9_]*)=('\{.*\}')$", text, flags=re.MULTILINE))
    assert {"WIRELESS_CONTROLLERS", "CRITICALITY_RULES"} <= set(examples)
    parsed = load(monkeypatch, **{name: value.strip("'") for name, value in examples.items()})
    assert parsed.WIRELESS_CONTROLLERS and parsed.CRITICALITY_RULES


def test_every_setting_is_needed(monkeypatch):
    """No defaults (#760): a missing setting stops startup, naming every one missing; a
    number or yes/no needs a value; text may be empty."""
    env = local_env()
    del env["CACHE_TTL"], env["LIBRENMS_URL"]
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=re.escape("missing in .env: CACHE_TTL, LIBRENMS_URL")):
        Settings(_env_file=None)
    with pytest.raises(RuntimeError, match="empty, but need a value: CACHE_TTL, METRICS_ENABLED"):
        load(monkeypatch, CACHE_TTL=" ", METRICS_ENABLED="")
    assert load(monkeypatch, LIBRENMS_URL="").LIBRENMS_URL == ""


def test_a_database_url_is_needed(monkeypatch):
    with pytest.raises(RuntimeError, match="Set NAUTOBOT_MAPS_DATABASE_URL"):
        load(monkeypatch, NAUTOBOT_MAPS_DATABASE_URL="", DATABASE_URL="")


def test_web_build_dir_is_relative_to_the_api(monkeypatch):
    assert load(monkeypatch, WEB_BUILD_DIR="../web/x").WEB_BUILD_DIR == str(REPO_ROOT / "web" / "x")
    assert load(monkeypatch, WEB_BUILD_DIR="").WEB_BUILD_DIR == ""


# --- Values --------------------------------------------------------------------------------


def test_flags(monkeypatch):
    for value, expected in [("0", False), ("false", False), ("No", False), ("yes", True)]:
        assert load(monkeypatch, METRICS_ENABLED=value).METRICS_ENABLED is expected
        assert load(monkeypatch, MCP_ENABLED=value).MCP_ENABLED is expected
    # Anything but 0/false/no is on, as in the current app.
    assert load(monkeypatch, MCP_ENABLED="maybe").MCP_ENABLED is True
    assert load(monkeypatch, FLASK_DEBUG="yes").FLASK_DEBUG is False  # only "true"
    assert load(monkeypatch, FLASK_DEBUG="TRUE").FLASK_DEBUG is True


def test_derived_and_clamped(monkeypatch):
    s = load(monkeypatch, CACHE_TTL="120", LIBRENMS_SYNC_INTERVAL_SECONDS=" 30 ")
    assert (s.INVENTORY_SYNC_INTERVAL_SECONDS, s.LIBRENMS_SYNC_INTERVAL_SECONDS) == (120, 30)
    s = load(monkeypatch, WIRELESS_SYNC_INTERVAL_SECONDS="5")
    assert s.WIRELESS_SYNC_INTERVAL_SECONDS == 60
    assert load(monkeypatch, ALERT_DELAY_SECONDS="-3").ALERT_DELAY_SECONDS == 0
    assert load(monkeypatch, WEBHOOK_ALERT_TTL_HOURS="0").WEBHOOK_ALERT_TTL_HOURS == 1
    s = load(monkeypatch, ALERT_MEDIUM_PERCENT="60", ALERT_CRITICAL_PERCENT="10")
    assert (s.ALERT_MEDIUM_PERCENT, s.ALERT_CRITICAL_PERCENT) == (60, 61)
    s = load(monkeypatch, ALERT_MEDIUM_PERCENT="150", ALERT_CRITICAL_PERCENT="500")
    assert (s.ALERT_MEDIUM_PERCENT, s.ALERT_CRITICAL_PERCENT) == (99, 100)


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
    assert s.NOTIFY_EMAIL_TO == ["a@x.example", "b@y.example"]
    assert s.NOTIFY_TENANTS == {"acme", "globex"}
    assert s.AUTH_ADMIN_GROUPS == {"net-admins", "ops"}
    assert s.SITE_TIERS == ["gold", "silver"]
    assert s.ALERTMANAGER_DEVICE_LABELS == ["instance", "host"]
    assert s.ACCESS_POINT_ROLES == {"access point", "ap"}
    assert s.MCP_ALLOWED_ORIGINS == ["https://a.example", "https://b.example"]
    defaults = load(monkeypatch)
    assert defaults.ALERT_BOARD_EXCLUDED_LOCATION_TYPES == {"graveyard", "warehouse"}
    assert defaults.ALERTMANAGER_DEVICE_LABELS == ["instance", "hostname", "host", "device"]


def test_an_empty_list_is_empty(monkeypatch):
    """Empty is none, for every list (#760: no default to fall back to)."""
    s = load(monkeypatch, ALERT_BOARD_EXCLUDED_LOCATION_TYPES="", ACCESS_POINT_ROLES="")
    assert s.ALERT_BOARD_EXCLUDED_LOCATION_TYPES == frozenset()
    assert s.ACCESS_POINT_ROLES == frozenset()


def test_tiers_and_levels(monkeypatch, caplog):
    s = load(
        monkeypatch,
        TIER_DEVICE_LEVEL="Gold:Critical, bronze:low, silver:urgent",
        NOTIFY_MIN_LEVEL_BY_TIER="gold:low,silver:loud,bronze",
        WIRELESS_AP_LEVEL="loud",
    )
    assert s.TIER_DEVICE_LEVEL == {"gold": "critical", "bronze": "low"}
    assert "TIER_DEVICE_LEVEL entry 'silver:urgent' ignored" in caplog.text
    assert s.NOTIFY_MIN_LEVEL_BY_TIER == {"gold": "low"}
    assert s.WIRELESS_AP_LEVEL == "low" and "WIRELESS_AP_LEVEL 'loud' ignored" in caplog.text


def test_ignored_with_a_warning(monkeypatch, caplog):
    s = load(
        monkeypatch,
        AUTH_SIGN_OUT_URL="//evil.example/out",
        CASE_URL_TEMPLATE="https://itsm.example/ticket",
        AUTH_DEFAULT_ROLE="superuser",
        AVAILABILITY_BASIS="sometimes",
    )
    assert (s.AUTH_SIGN_OUT_URL, s.CASE_URL_TEMPLATE, s.AUTH_DEFAULT_ROLE) == ("", "", "")
    assert s.AVAILABILITY_BASIS == "any_down"
    assert "AUTH_SIGN_OUT_URL ignored" in caplog.text
    assert "CASE_URL_TEMPLATE ignored" in caplog.text
    s = load(
        monkeypatch,
        AUTH_SIGN_OUT_URL="/oauth2/sign_out",
        CASE_URL_TEMPLATE="https://itsm.example/ticket/{case}",
        AUTH_DEFAULT_ROLE=" Operator ",
        AVAILABILITY_BASIS="CRITICAL",
    )
    assert s.AUTH_SIGN_OUT_URL == "/oauth2/sign_out"
    assert s.CASE_URL_TEMPLATE == "https://itsm.example/ticket/{case}"
    assert (s.AUTH_DEFAULT_ROLE, s.AVAILABILITY_BASIS) == ("operator", "critical")


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
    assert load(monkeypatch, AVAILABILITY_TARGET_PCT="99.95").AVAILABILITY_TARGET_PCT == 99.95
    assert load(monkeypatch).AVAILABILITY_TARGET_PCT == 99.9


def test_trusted_proxies(monkeypatch):
    s = load(monkeypatch, AUTH_TRUSTED_PROXIES="10.0.0.1; 172.16.0.0/12")
    assert s.AUTH_TRUSTED_PROXIES == (
        ipaddress.ip_network("10.0.0.1/32"),
        ipaddress.ip_network("172.16.0.0/12"),
    )


def test_database_url_names(monkeypatch):
    """The current app's NAUTOBOT_MAPS_DATABASE_URL first, DATABASE_URL too; the
    current app's postgresql:// URLs get the asyncpg driver, and so do the psycopg ones
    the new stack took until #664."""
    s = load(monkeypatch, NAUTOBOT_MAPS_DATABASE_URL="postgresql://u:p@db:5432/maps")
    assert s.DATABASE_URL == "postgresql+asyncpg://u:p@db:5432/maps"
    s = load(monkeypatch, DATABASE_URL="postgres://u@db/maps")
    assert s.DATABASE_URL == "postgresql+asyncpg://u@db/maps"
    s = load(monkeypatch, DATABASE_URL="postgresql+psycopg://u@db/maps?sslmode=require")
    assert s.DATABASE_URL == "postgresql+asyncpg://u@db/maps?sslmode=require"
    s = load(monkeypatch, DATABASE_URL="postgresql+asyncpg://u@db/maps")
    assert s.DATABASE_URL == "postgresql+asyncpg://u@db/maps"
    s = load(
        monkeypatch,
        NAUTOBOT_MAPS_DATABASE_URL="postgresql://u@one/maps",
        DATABASE_URL="postgresql://u@two/maps",
    )
    assert s.sqlalchemy_url().host == "one"
    # Empty (compose passes unset variables as ""): DATABASE_URL counts.
    s = load(monkeypatch, NAUTOBOT_MAPS_DATABASE_URL="", DATABASE_URL="postgresql://u@two/maps")
    assert s.sqlalchemy_url().host == "two"
    assert load(monkeypatch, NAUTOBOT_MAPS_DB="/data/maps.db").NAUTOBOT_MAPS_DB == "/data/maps.db"


def test_secrets_are_kept_as_given(monkeypatch):
    """Whitespace is part of a password, as in the current app; only "" means none."""
    s = load(monkeypatch, DATABASE_PASSWORD="   ", SMTP_PASSWORD=" pw ", NAUTOBOT_TOKEN=" t ")
    assert (s.DATABASE_PASSWORD, s.SMTP_PASSWORD, s.NAUTOBOT_TOKEN) == ("   ", " pw ", " t ")
    assert load(monkeypatch, DATABASE_PASSWORD="").DATABASE_PASSWORD is None
    # Other settings: whitespace is empty.
    assert load(monkeypatch, NAUTOBOT_API_VERSION="  ").NAUTOBOT_API_VERSION == ""


def test_cors_origins_json_or_csv(monkeypatch):
    s = load(monkeypatch, CORS_ORIGINS='["https://maps.example"]')
    assert s.CORS_ORIGINS == ["https://maps.example"]
    s = load(monkeypatch, CORS_ORIGINS="https://a.example, https://b.example")
    assert s.CORS_ORIGINS == ["https://a.example", "https://b.example"]


@pytest.mark.parametrize(
    ("name", "instead"),
    [
        ("WIRELESS_CONTROLLERS_FILE", "set WIRELESS_CONTROLLERS"),
        ("CRITICALITY_RULES_FILE", "set CRITICALITY_RULES"),
        ("LDAP_CA_FILE", "set EXTRA_CA_PEM"),
    ],
)
def test_a_file_setting_stops_startup_and_says_what_replaced_it(monkeypatch, name, instead):
    """#739: no files; an old .env says what to set instead."""
    with pytest.raises(RuntimeError, match=f"{name} was removed .*{instead}"):
        load(monkeypatch, **{name: "/config/x"})
    assert name not in SETTING_NAMES


def test_a_proxy_secret_needs_header_mode(monkeypatch):
    """The bundled proxy's API settings are .env's (#739): its secret without
    AUTH_MODE=header (the old overlay set it) stops startup, saying what to set."""
    with pytest.raises(RuntimeError, match="AUTH_MODE=header, AUTH_REQUIRE_VIEWER=true"):
        load(monkeypatch, AUTH_PROXY_SECRET="s3cret")
    assert load(monkeypatch, AUTH_PROXY_SECRET="s3cret", AUTH_MODE="header").AUTH_PROXY_SECRET


def test_criticality_rules(monkeypatch):
    s = load(
        monkeypatch,
        CRITICALITY_RULES='{"_comment": "a note", "Site": ["Core", "router"], "default": []}',
    )
    assert s.CRITICALITY_RULES == {"site": ["core", "router"], "default": []}
    assert load(monkeypatch).CRITICALITY_RULES == {}
    for wrong, message in (
        ('{"site": ["core"', "isn't valid JSON"),
        ('["core"]', "expected"),
        ('{"site": "core"}', "expected"),
    ):
        with pytest.raises(RuntimeError, match=message):
            load(monkeypatch, CRITICALITY_RULES=wrong)


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
    assert load(monkeypatch, MAP_DEFAULT_VIEW=" 55.7,12.6,6 ").MAP_DEFAULT_VIEW == (55.7, 12.6, 6)
    assert load(monkeypatch).MAP_DEFAULT_VIEW is None


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
    ],
)
def test_verify_setting_values(value, expected):
    """Both upstreams accept true/false (#191); a CA is EXTRA_CA_PEM (#739)."""
    assert settings._verify_ssl(value) == expected


def test_verify_ssl_from_the_environment(monkeypatch):
    s = load(monkeypatch, LIBRENMS_VERIFY_SSL="false", NAUTOBOT_VERIFY_SSL="off")
    assert (s.LIBRENMS_VERIFY_SSL, s.NAUTOBOT_VERIFY_SSL) == (False, False)
    assert load(monkeypatch).NAUTOBOT_VERIFY_SSL is True
    # A CA file's path, as before #739: stops startup, naming the setting.
    with pytest.raises(RuntimeError, match=r"LIBRENMS_VERIFY_SSL is true or false.*EXTRA_CA_PEM"):
        load(monkeypatch, LIBRENMS_VERIFY_SSL="/config/certs/internal-ca.pem")


def a_ca_pem(tmp_path) -> str:
    """A self-signed CA certificate, PEM."""
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=Test CA",
            "-keyout",
            str(tmp_path / "key.pem"),
            "-out",
            str(tmp_path / "ca.pem"),
        ],
        check=True,
        capture_output=True,
    )
    return (tmp_path / "ca.pem").read_text()


def test_extra_ca_pem_as_text_one_line_or_base64(tmp_path, monkeypatch):
    """EXTRA_CA_PEM (#739): the certificate itself, as it is, with \\n for its line
    breaks (one line in .env) or base64; checked at startup."""
    pem = a_ca_pem(tmp_path)
    one_line = pem.strip().replace("\n", "\\n")
    encoded = base64.b64encode(pem.encode()).decode()
    for raw in (pem, one_line, encoded):
        assert load(monkeypatch, EXTRA_CA_PEM=raw).EXTRA_CA_PEM == pem.strip()
    assert load(monkeypatch).EXTRA_CA_PEM == ""
    with pytest.raises(RuntimeError, match="expected PEM certificates"):
        load(monkeypatch, EXTRA_CA_PEM="/config/certs/internal-ca.pem")
    lines = pem.splitlines()
    broken = "\n".join([lines[0], lines[1][::-1], *lines[2:]])
    with pytest.raises(RuntimeError, match="EXTRA_CA_PEM can't be read as certificates"):
        load(monkeypatch, EXTRA_CA_PEM=broken)


# --- Ported: the wireless controllers file (tests/test_wireless.py) ---------------------


def write(tmp_path, text: str) -> str:
    """WIRELESS_CONTROLLERS's JSON, from YAML (easier to read here)."""
    return json.dumps(yaml.safe_load(text))


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
    assert load(monkeypatch, WIRELESS_CONTROLLERS=path).WIRELESS_CONTROLLERS == (dk, se)


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
        ("[]", "expected an object"),
    ],
)
def test_a_controller_mistake_stops_startup_and_names_it(tmp_path, text, message):
    with pytest.raises(RuntimeError) as caught:
        settings._wireless_controllers(write(tmp_path, text))
    assert message in str(caught.value)


def test_not_json_says_where_never_what(monkeypatch):
    raw = '{"catalyst_center": [{"name": "dk", "password": "hunter2",}]}'
    with pytest.raises(RuntimeError, match=r"isn't valid JSON \(line 1, column") as caught:
        load(monkeypatch, WIRELESS_CONTROLLERS=raw)
    assert "hunter2" not in str(caught.value)


def test_a_password_is_never_in_the_message(tmp_path, monkeypatch):
    path = write(
        tmp_path, "catalyst_center: [{name: dk, url: 'nope', username: u, password: hunter2}]"
    )
    with pytest.raises(RuntimeError) as caught:
        settings._wireless_controllers(path)
    assert "hunter2" not in str(caught.value)
    # Nor when it stops startup through the settings.
    with pytest.raises(RuntimeError) as caught:
        load(monkeypatch, WIRELESS_CONTROLLERS=path)
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
