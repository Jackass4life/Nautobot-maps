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
