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


OIDC = _load("compose.yml")
APP = _load("compose.app-sign-in.yml")


def test_the_same_but_for_the_sign_in():
    assert OIDC["networks"] == APP["networks"]
    assert OIDC["services"]["web"] == APP["services"]["web"]
    assert "oauth2-proxy" not in APP["services"]
    oidc, app = OIDC["services"]["nginx"], APP["services"]["nginx"]
    for key in ("restart", "logging", "ports", "volumes", "networks", "env_file"):
        assert oidc[key] == app[key], key
    assert app["build"]["args"].pop("SIGN_IN") == "app"
    assert oidc["build"] == app["build"]
    assert app["depends_on"] == [name for name in oidc["depends_on"] if name != "oauth2-proxy"]


def test_every_setting_is_in_dot_env():
    """#739: the API's settings for the proxy (AUTH_MODE=header, ...) are .env's, as
    every other; nginx and oauth2-proxy read it too. Only the network is set here."""
    for compose in (OIDC, APP):
        assert compose["services"]["api"] == {"networks": ["default", "edge"]}
        assert compose["services"]["nginx"]["env_file"] == ".env"
        assert all("environment" not in service for service in compose["services"].values())
    oauth2_proxy = OIDC["services"]["oauth2-proxy"]
    assert oauth2_proxy["env_file"] == ".env"
    # Its secrets come from .env under its own names, never on its command line.
    flags = {flag.split("=", 1)[0] for flag in oauth2_proxy["command"]}
    assert not {"--client-secret", "--cookie-secret"} & flags
    assert "--oidc-issuer-url" in flags and "--redirect-url" in flags
