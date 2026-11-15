"""The two bundled-proxy setups (#723): reverse-proxy/compose.yml (company sign-in
through oauth2-proxy) and compose.app-sign-in.yml (the app's own, AD on /auth) are
the same but for the sign-in, so a fix to one reaches the other."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROXY = REPO_ROOT / "reverse-proxy"


class _Loader(yaml.SafeLoader):
    """Compose's own tags (``!reset``), kept as plain values."""


_Loader.add_constructor("!reset", lambda loader, node: {"!reset": loader.construct_sequence(node)})


def _load(name: str) -> dict:
    return yaml.load((PROXY / name).read_text("utf-8"), Loader=_Loader)


def _env(service: dict) -> dict[str, str]:
    return dict(item.split("=", 1) for item in service.get("environment", []))


OIDC = _load("compose.yml")
APP = _load("compose.app-sign-in.yml")


def test_the_same_but_for_the_sign_in():
    assert OIDC["networks"] == APP["networks"]
    assert OIDC["services"]["web"] == APP["services"]["web"]
    assert "oauth2-proxy" not in APP["services"]
    oidc, app = OIDC["services"]["nginx"], APP["services"]["nginx"]
    for key in ("restart", "logging", "ports", "volumes", "networks"):
        assert oidc[key] == app[key], key
    assert app["build"]["args"].pop("SIGN_IN") == "app"
    assert oidc["build"] == app["build"]
    assert app["depends_on"] == [name for name in oidc["depends_on"] if name != "oauth2-proxy"]
    assert _env(app)["AUTH_PROXY_SECRET"] == _env(oidc)["AUTH_PROXY_SECRET"]
    assert not {"OIDC_ISSUER_URL", "OIDC_LOGOUT_URL"} & set(_env(app))


def test_the_api_trusts_only_the_proxy_in_both():
    oidc, app = _env(OIDC["services"]["api"]), _env(APP["services"]["api"])
    for key in ("AUTH_MODE", "AUTH_REQUIRE_VIEWER", "AUTH_TRUSTED_PROXIES", "AUTH_PROXY_SECRET"):
        assert oidc[key] == app[key], key
    assert OIDC["services"]["api"]["networks"] == APP["services"]["api"]["networks"]
    # AD sign-in can't start without these (#717).
    for key in ("LDAP_URL", "LDAP_BASE_DN", "JWT_SIGNING_SECRET"):
        assert app[key].startswith(f"${{{key}:?"), key
    # The sign-out is the app's own (/auth?signed_out=1), not oauth2-proxy's.
    assert "AUTH_SIGN_OUT_URL" not in app
