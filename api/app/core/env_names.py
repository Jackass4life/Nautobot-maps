"""Which names in the environment (or a .env file) anything reads (#758).

Every container gets the whole ``.env``; the API's settings are ``Settings``' fields,
the rest are read by the other containers, by ``docker compose`` itself, or only by
tests, the demo and CI. A name nothing reads is a leftover or a typo: the API warns
at startup about those that look like ours (``unknown()``), and ``python -m
app.check_env`` lists every one in a file. docs/configuration.md documents each name
(tests/test_env_names.py keeps the two the same).
"""

import difflib

from app.core.settings import REMOVED_FILE_SETTINGS, SETTING_NAMES, env_name

# Read by another part, not the API's settings (docs/configuration.md, "Read by").
OTHER_PARTS = frozenset(
    {
        # docker compose itself, PostgreSQL, the pages' container and its build.
        "COMPOSE_FILE",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "API_URL",
        "NUXT_PUBLIC_API_BASE",
        # The API image: its entrypoint, health check and gunicorn.
        "MIGRATE_ON_START",
        "HEALTHCHECK_PORT",
        "GUNICORN_WORKERS",
        "GUNICORN_TIMEOUT",
        "GUNICORN_BIND",
        # The bundled proxy: compose, nginx and oauth2-proxy.
        "PUBLIC_HOSTNAME",
        "OIDC_ISSUER_URL",
        "OIDC_CLIENT_ID",
        "OIDC_GROUPS_CLAIM",
        "OIDC_EMAIL_CLAIM",
        "OIDC_SCOPE",
        "OIDC_CA_FILE",
        "OIDC_LOGOUT_URL",
        "OAUTH2_PROXY_CLIENT_SECRET",
        "OAUTH2_PROXY_COOKIE_SECRET",
        "OAUTH2_PROXY_PROVIDER_CA_FILES",
        "PROXY_HTTP_PORT",
        "PROXY_HTTPS_PORT",
        "PROXY_SUBNET",
        "PROXY_IP",
    }
)

# Never in .env: only tests, the demo and CI read them ("Not in .env").
NOT_IN_ENV = frozenset(
    {
        "API_TEST_DATABASE_URL",
        "NAUTOBOT_LIVE_URL",
        "NAUTOBOT_LIVE_TOKEN",
        "AD_LIVE_URL",
        "AD_LIVE_CA_FILE",
        "E2E_BASE_URL",
        "E2E_OPERATOR_TOKEN",
        "E2E_ADMIN_TOKEN",
        "E2E_NOC_TOKEN",
        "E2E_CUSTOMER_TOKEN",
        "E2E_AD_BASE_URL",
        "MOCK_SCALE",
        "MOCK_AP_DHCP",
        "STATUS_LINK",
        "STATUS_TOKEN",
        "SESSION",
    }
)

# What the other parts use when a name is unset or empty (compose's ${NAME:-default},
# the image's entrypoint), for "the default anyway" in check_env.
OTHER_PARTS_DEFAULTS = {
    "MIGRATE_ON_START": "true",
    "HEALTHCHECK_PORT": "5000",
    "OIDC_GROUPS_CLAIM": "groups",
    "OIDC_EMAIL_CLAIM": "email",
    "OIDC_SCOPE": "openid email profile",
    "PROXY_HTTP_PORT": "80",
    "PROXY_HTTPS_PORT": "443",
    "PROXY_SUBNET": "172.30.0.0/24",
    "PROXY_IP": "172.30.0.10",
}

# Removed (#739): set, they stop the API at startup and say what replaced them.
REMOVED = frozenset(env_name(field) for field in REMOVED_FILE_SETTINGS)

KNOWN = frozenset(SETTING_NAMES) | OTHER_PARTS | NOT_IN_ENV | REMOVED

# A name that starts like one of ours (NAUTOBOT_, AUTH_, DB_, ...) and isn't one is
# probably meant to be: a typo, or a setting of another app. Other names in the
# container's environment (PATH, HOSTNAME, PYTHON_VERSION, ...) aren't ours to judge.
PREFIXES = frozenset(name.split("_")[0] + "_" for name in KNOWN if "_" in name)


def suggestion(name: str) -> str | None:
    """The known name *name* most likely meant, if one is close."""
    close = difflib.get_close_matches(name, sorted(KNOWN - NOT_IN_ENV), n=1, cutoff=0.8)
    return close[0] if close else None


def unknown(names: list[str]) -> list[tuple[str, str | None]]:
    """The names that look like ours but that nothing reads, each with what it may
    have meant: for the startup warning, which only sees the environment."""
    return [
        (name, suggestion(name))
        for name in sorted(names)
        if name not in KNOWN and any(name.startswith(prefix) for prefix in PREFIXES)
    ]
