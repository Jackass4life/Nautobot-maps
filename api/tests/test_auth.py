import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

PROXY = ("127.0.0.1", 40000)
ELSEWHERE = ("10.9.8.7", 40000)


def as_user(user: str, groups: str = "", **extra: str) -> dict[str, str]:
    return {"X-Forwarded-User": user, "X-Forwarded-Groups": groups, **extra}


@pytest.fixture
def header_mode(configure):
    configure(
        AUTH_MODE="header",
        AUTH_VIEWER_GROUPS="staff",
        AUTH_OPERATOR_GROUPS="noc, Ops",
        AUTH_ADMIN_GROUPS="netadmins",
    )


@pytest.fixture
def via_proxy():
    with TestClient(app, client=PROXY) as c:
        yield c


@pytest.fixture
def direct():
    with TestClient(app, client=ELSEWHERE) as c:
        yield c


def me(client, headers=None):
    return client.get("/api/v1/me", headers=headers or {}).json()


def test_disabled_mode_opens_reads_and_the_boards_changes(client, configure):
    """As the current app (#188): nobody is signed in; reads and the board's own
    changes are open, administrative ones need ALLOW_UNAUTHENTICATED_WRITES."""
    assert me(client) == {
        "username": None,
        "role": None,
        "groups": [],
        "auth_mode": "disabled",
        "can_write": True,
        "can_administer": False,
    }
    assert client.post("/api/v1/items", json={"name": "open"}).status_code == 201
    refused = client.post("/api/v1/tokens", json={"name": "x", "role": "viewer"})
    assert refused.status_code == 403
    assert refused.json()["error"] == "Authentication required for this change"
    assert "ALLOW_UNAUTHENTICATED_WRITES=true" in refused.json()["detail"]
    configure(ALLOW_UNAUTHENTICATED_WRITES="true")
    assert me(client)["can_administer"] is True
    assert client.post("/api/v1/tokens", json={"name": "x", "role": "viewer"}).status_code == 201


@pytest.mark.parametrize(
    ("groups", "role"),
    [
        ("staff", "viewer"),
        ("NOC", "operator"),
        ("ops,staff", "operator"),
        ("staff,netadmins", "admin"),
        ("", None),
    ],
)
def test_role_from_groups(header_mode, via_proxy, groups, role):
    body = me(via_proxy, as_user("alice", groups))
    assert body["username"] == "alice" and body["role"] == role and body["auth_mode"] == "header"


def test_groups_separated_by_semicolons_too(header_mode, via_proxy):
    assert me(via_proxy, as_user("alice", "staff; noc"))["groups"] == ["staff", "noc"]


def test_default_role_for_users_in_no_group(header_mode, configure, via_proxy):
    configure(AUTH_DEFAULT_ROLE="Viewer")
    assert me(via_proxy, as_user("bob"))["role"] == "viewer"
    assert me(via_proxy)["role"] is None  # not signed in: no default role


def test_headers_from_an_untrusted_peer_are_ignored(header_mode, direct):
    assert me(direct, as_user("mallory", "netadmins")) == {
        "username": None,
        "role": None,
        "groups": [],
        "auth_mode": "header",
        "can_write": False,
        "can_administer": False,
    }
    assert (
        direct.post(
            "/api/v1/items", json={"name": "x"}, headers=as_user("mallory", "netadmins")
        ).status_code
        == 401
    )


def test_trusted_proxies_setting(header_mode, configure, direct):
    configure(AUTH_TRUSTED_PROXIES="10.9.0.0/16, 172.16.0.1")
    assert me(direct, as_user("carol", "noc"))["role"] == "operator"


def test_ipv4_mapped_peer(header_mode):
    with TestClient(app, client=("::ffff:127.0.0.1", 1)) as c:
        assert me(c, as_user("dave", "noc"))["role"] == "operator"


def test_proxy_secret(header_mode, configure, via_proxy):
    configure(AUTH_PROXY_SECRET="s3cret")
    assert me(via_proxy, as_user("erin", "noc"))["username"] is None
    assert (
        me(via_proxy, as_user("erin", "noc", **{"X-Auth-Proxy-Secret": "wrong"}))["username"]
        is None
    )
    assert (
        me(via_proxy, as_user("erin", "noc", **{"X-Auth-Proxy-Secret": "s3cret"}))["role"]
        == "operator"
    )


def test_writes_need_operator(header_mode, via_proxy):
    anonymous = via_proxy.post("/api/v1/items", json={"name": "a"})
    assert anonymous.status_code == 401
    assert anonymous.json() == {"error": "Authentication required"}
    viewer = via_proxy.post("/api/v1/items", json={"name": "a"}, headers=as_user("v", "staff"))
    assert viewer.status_code == 403
    assert viewer.json() == {
        "error": "Insufficient permissions",
        "required_role": "operator",
        "current_role": "viewer",
    }
    created = via_proxy.post("/api/v1/items", json={"name": "a"}, headers=as_user("o", "noc"))
    assert created.status_code == 201
    item_id = created.json()["id"]
    assert (
        via_proxy.delete(f"/api/v1/items/{item_id}", headers=as_user("v", "staff")).status_code
        == 403
    )
    assert (
        via_proxy.delete(f"/api/v1/items/{item_id}", headers=as_user("a", "netadmins")).status_code
        == 204
    )


def test_reads_public_unless_require_viewer(header_mode, configure, via_proxy):
    assert via_proxy.get("/api/v1/items").status_code == 200
    configure(AUTH_REQUIRE_VIEWER="true")
    assert via_proxy.get("/api/v1/items").status_code == 401
    nobody = via_proxy.get("/api/v1/items", headers=as_user("nobody"))
    assert nobody.status_code == 403
    assert nobody.json() == {"error": "Insufficient permissions", "required_role": "viewer"}
    assert via_proxy.get("/api/v1/items", headers=as_user("v", "staff")).status_code == 200
    # As every page and API of the current app: the page reads a 401 as "sign in".
    assert via_proxy.get("/api/v1/me").status_code == 401
    # Probes stay open.
    assert via_proxy.get("/api/healthz").status_code == 200


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}, 403),
        ({"Sec-Fetch-Site": "same-site", "Origin": "http://localhost:4000"}, 403),
        ({"Origin": "https://evil.example"}, 403),
        ({"Origin": "null"}, 403),
        # The host alone (#592, as the current app): behind the proxy the app sees
        # http:// for the browser's https://, and the Host may lack the port.
        ({"Origin": "https://testserver"}, 201),
        ({"Origin": "http://testserver:8443"}, 201),
        ({"Origin": "https://elsewhere.example"}, 403),
        # web/'s origin (CORS_ORIGINS), other origin than the API but allowed
        ({"Sec-Fetch-Site": "same-site", "Origin": "http://localhost:3000"}, 201),
        ({"Sec-Fetch-Site": "same-origin"}, 201),
        ({}, 201),  # scripts send neither
    ],
)
def test_cross_site_writes(client, headers, status):
    response = client.post("/api/v1/items", json={"name": f"x{uuid.uuid4().hex}"}, headers=headers)
    assert response.status_code == status
    if status == 403:
        assert response.json() == {"error": "Changes from another site are refused"}


def test_cross_site_reads_are_not_refused(client):
    assert client.get("/api/v1/items", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_settings_are_validated(configure):
    from app.utils.config import get_settings

    configure(AUTH_MODE="sometimes")
    with pytest.raises(ValueError):
        get_settings()


def test_cors_allows_cookies_from_web(client):
    # A cookie-based sign-in proxy in front of an API on another origin (#582).
    response = client.options(
        "/api/v1/items",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"
