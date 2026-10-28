"""The bundled nginx + oauth2-proxy setup (#423): what the files must keep
true as the app changes.  CI's proxy-smoke job runs the real nginx."""

import pathlib
import re

from nautobot_maps import auth

PROXY = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "proxy"
SITE = (PROXY / "templates" / "default.conf.template").read_text(encoding="utf-8")
TO_APP = (PROXY / "templates" / "proxy-to-app.inc.template").read_text(encoding="utf-8")
COMPOSE = (PROXY / "compose.yml").read_text(encoding="utf-8")


def test_the_status_page_files_skip_the_login_and_no_others():
    """The public status page's files (the Nuxt build's, #463) load without the
    company login, as the app serves them; no other static files do."""
    skipped = re.findall(r"location (?:= |~ )?(\S+) \{\n        include /etc/nginx/conf.d/proxy-to-app.inc;", SITE)
    assert auth.FRONTEND_ASSETS_PREFIX in skipped
    assert not [path for path in skipped if "static" in path], skipped


def test_identity_headers_always_come_from_nginx():
    for header in ("X-Forwarded-User", "X-Forwarded-Groups", "X-Auth-Proxy-Secret"):
        assert f"proxy_set_header {header} " in TO_APP, header
    # Every location that reaches the app goes through the include.
    assert SITE.count("proxy_pass http://nautobot-maps") == 0
    assert TO_APP.count("proxy_pass http://nautobot-maps:5000;") == 1


def test_only_the_two_settings_are_filled_in():
    used = set(re.findall(r"\$\{([A-Z_]+)\}", SITE + TO_APP))
    assert used == {"PUBLIC_HOSTNAME", "AUTH_PROXY_SECRET"}
    assert "NGINX_ENVSUBST_FILTER=^(PUBLIC_HOSTNAME|AUTH_PROXY_SECRET)$" in COMPOSE


def test_a_token_skips_the_login_by_its_header_not_a_path():
    assert '"~*^Bearer\\s+nmt_" 1;' in SITE
    assert "if ($api_token) {\n            return 202;" in SITE
    assert "location = /metrics {\n        return 404;" in SITE


def test_images_are_pinned_and_the_app_trusts_only_nginx():
    assert re.search(r"image: nginx:\d+\.\d+\.\d+-alpine", COMPOSE)
    assert re.search(r"image: quay\.io/oauth2-proxy/oauth2-proxy:v\d+\.\d+\.\d+", COMPOSE)
    assert "AUTH_TRUSTED_PROXIES=${PROXY_IP:-172.30.0.10}/32" in COMPOSE
    assert "ipv4_address: ${PROXY_IP:-172.30.0.10}" in COMPOSE
    assert "AUTH_REQUIRE_VIEWER=true" in COMPOSE


def test_a_company_ca_for_the_identity_provider():
    """#526: OIDC_CA_FILE is mounted into oauth2-proxy and trusted with the
    public CAs; unset, no CA file is set at all (oauth2-proxy then uses the
    public ones), and a missing file is an error, not a directory."""
    assert "OAUTH2_PROXY_PROVIDER_CA_FILES=${OIDC_CA_FILE:+/etc/oauth2-proxy/provider-ca.pem}" in COMPOSE
    assert "OAUTH2_PROXY_USE_SYSTEM_TRUST_STORE=true" in COMPOSE
    oauth2 = re.search(r"\n  oauth2-proxy:\n(.*?)\n  nginx:", COMPOSE, re.S).group(1)
    mount = re.search(r"- type: bind\n(.*?)create_host_path: false", oauth2, re.S)
    assert mount, "a bind mount that never creates the host path"
    assert "source: ${OIDC_CA_FILE:-/dev/null}" in mount.group(1)
    assert "target: /etc/oauth2-proxy/provider-ca.pem" in mount.group(1)
    assert "read_only: true" in mount.group(1)
    assert "SSL_INSECURE_SKIP_VERIFY" not in COMPOSE
