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

from app.core import settings
from app.core.settings import SETTING_NAMES, Settings

API_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = API_ROOT.parent

# Read only to warn that it is still set (#153): neither passed nor documented.
LEGACY_SETTINGS = {"NAUTOBOT_MAPS_DB"}
# Not in .env.example (docs/configuration.md has them): only for the development server.
NOT_IN_ENV_EXAMPLE = {
    # The development server's (python run.py), and development's SQL log.
    "FLASK_DEBUG",
    "FLASK_RUN_PORT",
    "SQL_ECHO",
    # The development server's: where `bun run generate` put the pages (#674: the API
    # image has none; the web image serves them).
    "WEB_BUILD_DIR",
    # The new stack's name; compose's .env has NAUTOBOT_MAPS_DATABASE_URL, which wins.
    "DATABASE_URL",
    *LEGACY_SETTINGS,
}
# Every compose file of the app (#739): settings come from an env file, never from an
# environment: block.
COMPOSE_FILES = (
    "docker-compose.yml",
    "reverse-proxy/compose.yml",
    "reverse-proxy/compose.app-sign-in.yml",
    "reverse-proxy/ci/compose.yml",
    "reverse-proxy/ci/oauth2-proxy.compose.yml",
    "reverse-proxy/ci/images.compose.yml",
    "demo/docker-compose.yml",
)


class _ComposeLoader(yaml.SafeLoader):
    """Compose's own tags (``!reset``), kept as plain values."""


_ComposeLoader.add_constructor("!reset", lambda loader, node: None)


def env_example_names() -> set[str]:
    """The settings .env.example names, set or commented out."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^#? ?([A-Z][A-Z0-9_]*)=", text, flags=re.MULTILINE))


def load(monkeypatch, **env: str) -> Settings:
    """Settings from exactly this environment (and no .env file)."""
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


# --- Plumbing ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_no_environment_in_the_compose_files(name):
    """#739: every setting is in .env (each container's env_file), none in compose."""
    compose = yaml.load((REPO_ROOT / name).read_text("utf-8"), Loader=_ComposeLoader)
    for service, definition in compose["services"].items():
        assert "environment" not in (definition or {}), f"{name}: {service}"


def test_the_development_stacks_app_reads_its_env_file():
    """development/docker-compose.yml (#747): Nautobot's containers keep their own settings
    there; the app's read maps.env."""
    compose = yaml.safe_load((REPO_ROOT / "development/docker-compose.yml").read_text("utf-8"))
    for service in ("maps-postgres", "web", "api"):
        definition = compose["services"][service]
        assert definition["env_file"] == "maps.env", service
        assert "environment" not in definition, service


def test_the_api_and_web_read_dot_env():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text("utf-8"))
    for service in ("postgres", "web", "api"):
        assert compose["services"][service]["env_file"] == ".env", service
    # No ./config any more (#739): nothing mounted into the API.
    assert "volumes" not in compose["services"]["api"]
    assert not (REPO_ROOT / "config").exists()


def test_env_example_lists_every_setting():
    """.env.example is the one place to start from (#739): every setting, with the
    compose stack's own (the database, the cache, the pages' API_URL)."""
    names = env_example_names()
    missing = sorted(set(SETTING_NAMES) - names - NOT_IN_ENV_EXAMPLE)
    assert not missing, f"add to .env.example: {missing}"
    stack = {"POSTGRES_PASSWORD", "NAUTOBOT_MAPS_DATABASE_URL", "CACHE_TYPE", "API_URL"}
    assert stack <= names
    assert not (API_ROOT / ".env.example").exists()
    assert not (REPO_ROOT / "web" / ".env.example").exists()


def test_env_example_parses_as_settings(monkeypatch):
    """Its values, as compose passes them, start the app: the JSON settings too."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    values = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", text, flags=re.MULTILINE))
    known = {name: value for name, value in values.items() if name in SETTING_NAMES}
    assert load(monkeypatch, **known).CACHE_TYPE == "RedisCache"
    # The commented examples parse too.
    examples = dict(re.findall(r"^# ([A-Z][A-Z0-9_]*)=('\{.*\}')$", text, flags=re.MULTILINE))
    assert {"WIRELESS_CONTROLLERS", "CRITICALITY_RULES"} <= set(examples)
    parsed = load(monkeypatch, **{name: value.strip("'") for name, value in examples.items()})
    assert parsed.WIRELESS_CONTROLLERS and parsed.CRITICALITY_RULES


def test_every_setting_is_documented():
    docs = "\n".join(
        (path).read_text(encoding="utf-8")
        for path in (REPO_ROOT / "docs" / "configuration.md", API_ROOT / "README.md")
    )
    missing = sorted(
        name for name in set(SETTING_NAMES) - LEGACY_SETTINGS if f"`{name}`" not in docs
    )
    assert not missing, f"document in docs/configuration.md or api/README.md: {missing}"


def config_rows() -> list[tuple[list[str], str]]:
    """docs/configuration.md's settings tables: each row's names and its Read by cell."""
    text = (REPO_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    rows, read_by = [], None
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not line.startswith("|"):
            read_by = None
        elif cells[0] == "Variable":
            read_by = cells.index("Read by") if "Read by" in cells else None
            assert read_by is not None, f"a settings table without Read by: {line}"
        elif read_by is not None and not cells[0].startswith("---"):
            rows.append((re.findall(r"`([A-Z][A-Z0-9_]*)`", cells[0]), cells[read_by]))
    return rows


def _text(*patterns: str) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for pattern in patterns
        for path in sorted(REPO_ROOT.glob(pattern))
    )


def nginx_runtime_names() -> set[str]:
    """What nginx's container fills in when it starts (NGINX_ENVSUBST_FILTER); the rest
    of its templates are filled in when the image is built, from build arguments."""
    dockerfile = (REPO_ROOT / "reverse-proxy" / "Dockerfile").read_text(encoding="utf-8")
    names = re.search(r'NGINX_ENVSUBST_FILTER="\^\(([A-Z0-9_|]+)\)\$"', dockerfile)
    assert names, "reverse-proxy/Dockerfile: NGINX_ENVSUBST_FILTER moved"
    return set(names.group(1).split("|"))


def _reads(reader: str, name: str) -> bool:
    """Whether *reader* (a Read by entry) really reads *name*, by its source (#749)."""
    base = reader.split(" (")[0]
    if base in {"api", "dev server"}:
        return name in SETTING_NAMES or name in _text("api/docker-entrypoint.sh", "api/Dockerfile")
    if base == "gunicorn":
        return f'"{name}"' in _text("api/gunicorn_config.py")
    if base == "web":
        return f"process.env.{name}" in _text("web/server/**/*.ts")
    if base == "postgres":
        return name.startswith("POSTGRES_")
    if base == "oauth2-proxy":
        return name.startswith("OAUTH2_PROXY_")
    if base == "nginx":
        return name in nginx_runtime_names()
    if base == "compose":
        return name == "COMPOSE_FILE" or f"${{{name}" in _text(*COMPOSE_FILES)
    if base == "build":
        return name in _text("web/nuxt.config.ts")
    path = reader.strip("`")
    assert (REPO_ROOT / path).is_file(), f"Read by names no part or file: {reader}"
    return name in (REPO_ROOT / path).read_text(encoding="utf-8")


def test_every_env_example_setting_is_in_a_settings_table():
    """Not only the API's: the containers' and compose's too (#749)."""
    documented = {name for names, _ in config_rows() for name in names}
    missing = sorted(env_example_names() - documented)
    assert not missing, f"add to a table in docs/configuration.md: {missing}"


def test_read_by_names_the_parts_that_read_it():
    """Each row's Read by (#749) matches the code: every part it names reads the
    setting, and the API, the pages, nginx and compose aren't left out where they do.
    "a / b" pairs each name with its own readers."""
    compose = _text(*COMPOSE_FILES)
    web = _text("web/server/**/*.ts")
    for names, cell in config_rows():
        assert cell, f"no Read by: {names}"
        groups = cell.split(" / ")
        if len(groups) != len(names):
            groups = [cell] * len(names)
        for name, group in zip(names, groups, strict=True):
            readers = [reader.strip() for reader in re.split(r",(?![^(]*\))", group)]
            for reader in readers:
                assert _reads(reader, name), f"{name}: {reader} doesn't read it"
            bases = {reader.split(" (")[0] for reader in readers}
            # Scripts' rows ("Not in .env") name files: the containers' rows are checked.
            if name in env_example_names() and not all("/" in reader for reader in readers):
                expected = {
                    "api": name in SETTING_NAMES,
                    "compose": f"${{{name}" in compose,
                    "nginx": name in nginx_runtime_names(),
                    "web": f"process.env.{name}" in web,
                }
                # The development server is the API too.
                if "dev server" in bases:
                    bases.add("api")
                for base, reads in expected.items():
                    assert not reads or base in bases, f"{name}: add {base} to Read by"


def test_the_scripts_settings_are_documented():
    """Every variable a script or test reads from the environment, outside the API's
    settings, is on docs/configuration.md ("Not in .env", #749)."""
    docs = (REPO_ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    skip = {".venv", "node_modules", "example", ".git"}
    names: dict[str, str] = {}
    for path in sorted(REPO_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        if skip & set(relative.parts) or relative.parts[:2] == ("api", "app"):
            continue
        source = path.read_text(encoding="utf-8")
        for name in re.findall(
            r"""os\.(?:getenv|environ\.get|environ\[)\(?\s*["']([A-Z][A-Z0-9_]*)["']""", source
        ):
            names.setdefault(name, str(relative))
    missing = sorted(f"{name} ({at})" for name, at in names.items() if f"`{name}`" not in docs)
    assert not missing, f"document in docs/configuration.md: {missing}"


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
    assert defaults.CACHE_TTL == 300 and defaults.INVENTORY_SYNC_INTERVAL_SECONDS == 300


def test_empty_is_a_value_where_it_was_in_compose():
    """These were passed with ${NAME-default} (#588): unset is the default, set empty
    means none, now that .env passes them as written (#739)."""
    for field in settings.EMPTY_IS_A_VALUE:
        assert Settings.model_fields[field].default, field


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


def test_empty_exclusions_exclude_nothing(monkeypatch):
    s = load(monkeypatch, ALERT_BOARD_EXCLUDED_LOCATION_TYPES="")
    assert s.ALERT_BOARD_EXCLUDED_LOCATION_TYPES == frozenset()
    # compose passes this one with ${NAME:-default}: empty is the default.
    assert load(monkeypatch, ACCESS_POINT_ROLES="").ACCESS_POINT_ROLES == {"access point"}


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
    # Other settings: whitespace is empty, the default.
    assert load(monkeypatch, CACHE_TTL="  ").CACHE_TTL == 300


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
