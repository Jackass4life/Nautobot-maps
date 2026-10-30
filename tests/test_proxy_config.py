"""The bundled nginx + oauth2-proxy setup (#423): what the files must keep
true as the app changes.  CI's proxy-smoke job runs the real nginx."""

import pathlib
import re
import subprocess

from nautobot_maps import auth

PROXY = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "proxy"
SITE = (PROXY / "templates" / "default.conf.template").read_text(encoding="utf-8")
TO_APP = (PROXY / "templates" / "proxy-to-app.inc.template").read_text(encoding="utf-8")
COMPOSE = (PROXY / "compose.yml").read_text(encoding="utf-8")
IPV6_SCRIPT = PROXY / "entrypoint" / "30-listen-ipv6-only-if-available.sh"


def test_the_status_page_files_skip_the_login_and_no_others():
    """The public status page's files (the Nuxt build's, #463) load without the
    company login, as the app serves them; no other static files do."""
    skipped = re.findall(r"location (?:= |~ )?(\S+) \{\n        include /etc/nginx/conf.d/proxy-to-app.inc;", SITE)
    assert auth.FRONTEND_ASSETS_PREFIX in skipped
    assert not [path for path in skipped if "static" in path], skipped


def test_the_status_api_passes_on_who_is_signed_in_but_never_asks_to_sign_in():
    """#527: staff open a tenant's page from Availability without a key; a
    customer without a company login still gets to the app (no redirect)."""
    status_api = re.search(r"location /api/status/ \{(.*?)\n    \}", SITE, re.S).group(1)
    assert "auth_request /oauth2/auth-optional;" in status_api
    assert "error_page" not in status_api, "never the login"
    for variable in ("$auth_user", "$auth_email", "$auth_groups"):
        assert f"auth_request_set {variable} " in status_api
    optional = re.search(r"location = /oauth2/auth-optional \{(.*?)\n    \}", SITE, re.S).group(1)
    assert "internal;" in optional and "proxy_intercept_errors on;" in optional
    assert "error_page 401 403 = @anonymous;" in optional
    assert "if ($api_token) {\n            return 202;" in optional
    assert "location @anonymous {\n        return 202;" in SITE


def test_pkce():
    assert "OAUTH2_PROXY_CODE_CHALLENGE_METHOD=S256" in COMPOSE


def run_ipv6_script(tmp_path, if_inet6: str | None) -> str:
    conf = tmp_path / "conf.d"
    conf.mkdir(parents=True)
    (conf / "default.conf").write_text(SITE)
    env = {"PATH": "/usr/bin:/bin", "CONF_DIR": str(conf), "IF_INET6": str(tmp_path / "if_inet6")}
    if if_inet6 is not None:
        (tmp_path / "if_inet6").write_text(if_inet6)
    subprocess.run(["sh", str(IPV6_SCRIPT)], env=env, check=True, capture_output=True)
    return (conf / "default.conf").read_text()


def test_nginx_listens_on_ipv6_only_where_the_host_has_it(tmp_path):
    """#527: on a host without IPv6, nginx would stop at listen [::]:80."""
    assert "./deploy/proxy/entrypoint/30-listen-ipv6-only-if-available.sh:/docker-entrypoint.d/" in COMPOSE
    assert IPV6_SCRIPT.stat().st_mode & 0o111, "the image runs only executable scripts"
    loopback = "00000000000000000000000000000001 01 80 10 80       lo\n"
    assert run_ipv6_script(tmp_path / "with", loopback) == SITE
    for case, content in (("no-file", None), ("off-in-docker", "")):
        conf = run_ipv6_script(tmp_path / case, content)
        assert "[::]" not in conf, case
        assert "listen 80;" in conf and "listen 443 ssl;" in conf, case


def test_identity_headers_always_come_from_nginx():
    for header in ("X-Forwarded-User", "X-Forwarded-Groups", "X-Auth-Proxy-Secret"):
        assert f"proxy_set_header {header} " in TO_APP, header
    # Every location that reaches the app goes through the include.
    assert SITE.count("proxy_pass http://nautobot-maps") == 0
    assert TO_APP.count("proxy_pass http://nautobot-maps:5000;") == 1


def test_only_these_settings_are_filled_in():
    used = set(re.findall(r"\$\{([A-Z_]+)\}", SITE + TO_APP))
    assert used == {"PUBLIC_HOSTNAME", "AUTH_PROXY_SECRET", "OIDC_ISSUER_URL", "OIDC_LOGOUT_URL"}
    assert "NGINX_ENVSUBST_FILTER=^(PUBLIC_HOSTNAME|AUTH_PROXY_SECRET|OIDC_ISSUER_URL|OIDC_LOGOUT_URL)$" in COMPOSE


def test_sign_out_ends_up_at_the_identity_providers_logout():
    """#546: the top bar's Sign out is nginx's own sign-out location, which
    sends oauth2-proxy's "/" on to OIDC_LOGOUT_URL, else to /signed-out (not
    the app, where the identity provider would sign the user straight back in)."""
    assert "AUTH_SIGN_OUT_URL=${AUTH_SIGN_OUT_URL:-/oauth2/sign_out}" in COMPOSE
    assert "OIDC_LOGOUT_URL=${OIDC_LOGOUT_URL:-}" in COMPOSE
    sign_out = re.search(r"location = /oauth2/sign_out \{(.*?)\n    \}", SITE, re.S).group(1)
    assert "proxy_pass http://oauth2-proxy:4180;" in sign_out
    # Not $request_uri, as /oauth2/ has: oauth2-proxy would send it back here.
    assert "proxy_set_header X-Auth-Request-Redirect /;" in sign_out
    assert "proxy_redirect ~^/$ $sign_out_next;" in sign_out
    assert (
        'map "${OIDC_LOGOUT_URL}" $sign_out_next {\n    "" /signed-out;\n    default "${OIDC_LOGOUT_URL}";\n}' in SITE
    )


def test_the_signed_out_page_never_signs_in():
    """GitLab has no logout address (#546): the page after Sign out needs no
    login, carries no data, links the identity provider to sign out there."""
    page = re.search(r"location = /signed-out \{(.*?)\n    \}", SITE, re.S).group(1)
    assert "auth_request" not in page and "proxy-to-app" not in page
    assert "return 200 '<!DOCTYPE html>" in page
    assert 'href="/">Sign in again</a>' in page
    assert '<a href="${OIDC_ISSUER_URL}">' in page
    assert "default-src 'none'; style-src 'unsafe-inline'" in page
    assert "<script" not in page
    assert 'add_header Strict-Transport-Security "max-age=31536000" always;' in page


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
