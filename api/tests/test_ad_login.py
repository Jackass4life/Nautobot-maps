"""Sign-in with AD and the app's own token (#717): the login, not the directory (#774).
ad_service.authenticate is faked, so these hold for any AD service with its interface
(a user, None, or DirectoryUnavailable); the sit_core one: test_ad_service.py, a real
AD: test_ad_live.py."""

import logging
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.core import settings as config
from app.services import ad_service, identity_service, login_service, role_service
from tests import db as adb
from tests.test_auth import PROXY

SECRET = "s" * 40
USERS = {
    "jdoe": ("Jane-Pass-1!", "Jane Doe"),
    "bob": ("Bob-Pass-1!", "Bob Builder"),
}


@pytest.fixture
def directory(monkeypatch):
    """A pretend AD: jdoe and bob with their passwords; ``state["down"]`` breaks it."""
    state = {"down": False, "asked": []}

    def authenticate(username, password, settings):
        state["asked"].append(username)
        if state["down"]:
            raise ad_service.DirectoryUnavailable("down")
        name = username.strip().lower()
        if name not in USERS or USERS[name][0] != password:
            return None
        return ad_service.AdUser(unique_name=name, common_name=USERS[name][1], dn="")

    monkeypatch.setattr(ad_service, "authenticate", authenticate)
    return state


@pytest.fixture
def signed_in_mode(override_settings, pg_database, directory):
    adb.sql("TRUNCATE roles, permissions, identities, identities_to_roles CASCADE")
    adb.call(role_service.seed_defaults)
    override_settings(
        AUTH_MODE="header",
        AUTH_REQUIRE_VIEWER="true",
        AD_HOST="fake-ad",
        JWT_SIGNING_SECRET=SECRET,
    )
    adb.call(identity_service.create, "jdoe", ["noc"], [], "Jane Doe")
    login_service.forget_failures()
    yield
    login_service.forget_failures()


@pytest.fixture
def client():
    with TestClient(app, base_url="https://maps.test") as c:
        yield c


def login(client, username="jdoe", password="Jane-Pass-1!", **kwargs):
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}, **kwargs
    )


def test_sign_in_and_be_the_identity(signed_in_mode, client):
    answer = login(client)
    assert answer.status_code == 200 and answer.headers["cache-control"] == "no-store"
    body = answer.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 28800
    cookie = answer.headers["set-cookie"].lower()
    for part in ("nm_session=", "httponly", "secure", "samesite=strict", "path=/"):
        assert part in cookie, part
    # The cookie: the pages.
    me = client.get("/api/v1/me").json()
    assert me["username"] == "jdoe" and me["role"] == "noc"
    assert me["access"]["identity"] == "jdoe" and me["access"]["landing"] == "/noc"
    # The token in Authorization: scripts.
    bare = TestClient(app, base_url="https://maps.test")
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert bare.get("/api/v1/me", headers=headers).json()["role"] == "noc"
    claims = bare.get("/api/v1/auth/verify", headers=headers).json()
    assert claims["sub"] == "jdoe" and claims["common_name"] == "Jane Doe"
    assert "alerts:ack" in claims["scopes"] and claims["exp"] - claims["iat"] == 28800
    # The top bar's Sign out ends it on the sign-in page.
    assert client.get("/api/v1/page-config").json()["user"]["sign_out_url"] == "/auth?signed_out=1"


def test_a_form(signed_in_mode, client):
    answer = client.post(
        "/api/v1/auth/login", data={"username": "jdoe", "password": "Jane-Pass-1!"}
    )
    assert answer.status_code == 200


def test_wrong_password_and_unknown_user_answer_the_same(signed_in_mode, client, directory):
    wrong = login(client, password="nope")
    unknown = login(client, username="nobody")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"error": "Wrong user name or password"}


def test_the_first_sign_in_makes_a_viewer(signed_in_mode, client):
    """#782 (the example's _create_basic_reader_user): viewer, nothing more."""
    answer = login(client, username="bob", password="Bob-Pass-1!")
    assert answer.status_code == 200
    me = client.get("/api/v1/me").json()
    assert me["username"] == "bob" and me["role"] == "viewer" and not me["can_write"]
    made = adb.call(identity_service.read, "bob")
    assert made.roles == ("viewer",) and made.common_name == "Bob Builder" and not made.scopes
    assert login(client, username="bob", password="Bob-Pass-1!").status_code == 200, "again"


def test_no_viewer_role_no_access(signed_in_mode, client):
    adb.sql("DELETE FROM roles WHERE name = 'viewer'")
    answer = login(client, username="bob", password="Bob-Pass-1!")
    assert answer.status_code == 403 and answer.json()["error"] == "No access"
    assert "set-cookie" not in answer.headers
    assert adb.call(identity_service.read, "bob") is None


def test_failed_sign_ins_wait(signed_in_mode, client):
    for _ in range(login_service.FREE_FAILURES):
        assert login(client, password="nope").status_code == 401
    held = login(client)
    assert held.status_code == 429 and int(held.headers["retry-after"]) >= 29
    # Another address isn't held up, nor is another name.
    with TestClient(app, base_url="https://maps.test", client=("10.1.1.1", 5000)) as other:
        assert login(other).status_code == 200
    assert login(client, username="bob", password="Bob-Pass-1!").status_code == 200


def test_a_token_that_is_not_ours_or_not_any_more(signed_in_mode, client, monkeypatch):
    token = login(client).json()["access_token"]
    bare = TestClient(app, base_url="https://maps.test")
    claims = jwt.decode(token, options={"verify_signature": False})
    forged = jwt.encode({**claims, "sub": "root"}, "another secret" * 4, algorithm="HS256")
    none_alg = jwt.encode({**claims, "sub": "root"}, None, algorithm="none")
    expired = jwt.encode({**claims, "exp": int(time.time()) - 120}, SECRET, algorithm="HS256")
    other_aud = jwt.encode({**claims, "aud": "elsewhere"}, SECRET, algorithm="HS256")
    for bad in (forged, none_alg, expired, other_aud, "x.y.z"):
        refused = bare.get("/api/v1/alerts", headers={"Authorization": f"Bearer {bad}"})
        assert refused.status_code == 401
        assert refused.json() == {"error": "Authentication required", "sign_in": "/auth"}


def test_a_removed_role_ends_before_the_token(signed_in_mode, client):
    login(client)
    assert client.get("/api/v1/me").json()["role"] == "noc"
    adb.call(identity_service.remove_role, "jdoe", "noc")
    identity_service.forget()  # the other workers: within 30 s
    me = client.get("/api/v1/me")
    assert me.status_code == 403
    adb.call(identity_service.delete, "jdoe")
    identity_service.forget()
    assert client.get("/api/v1/alerts").json()["error"] == "No access"


def test_sign_out(signed_in_mode, client):
    login(client)
    out = client.post("/api/v1/auth/logout")
    assert out.json() == {"signed_out": True} and 'nm_session=""' in out.headers["set-cookie"]
    assert client.get("/api/v1/alerts").status_code == 401


def test_a_browser_not_signed_in_goes_to_sign_in(
    signed_in_mode, client, override_settings, tmp_path
):
    # A build of its own: the API's CI job doesn't build web/.
    (tmp_path / "200.html").write_text("<!DOCTYPE html><p>shell</p>")
    override_settings(WEB_BUILD_DIR=str(tmp_path))
    page = client.get(
        "/alerts?tenant=Acme", headers={"Accept": "text/html"}, follow_redirects=False
    )
    assert page.status_code == 303
    assert page.headers["location"] == "/auth?redirect=%2Falerts%3Ftenant%3DAcme"
    sign_in = client.get("/auth", headers={"Accept": "text/html"})
    assert sign_in.status_code == 200 and "shell" in sign_in.text


def test_cross_site_writes_with_the_cookie_are_refused(signed_in_mode, client):
    token = login(client).json()["access_token"]
    elsewhere = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    refused = client.post("/api/v1/alert-acks", json={}, headers=elsewhere)
    assert refused.status_code == 403 and "another site" in refused.json()["error"]
    # A token in Authorization can't come from a page elsewhere.
    allowed = client.post(
        "/api/v1/alert-acks", json={}, headers={**elsewhere, "Authorization": f"Bearer {token}"}
    )
    assert allowed.status_code != 403


def test_the_directory_down_is_not_the_users_fault(signed_in_mode, client, directory):
    directory["down"] = True
    answer = login(client)
    assert answer.status_code == 503
    assert login_service.wait_seconds("jdoe", "testclient") == 0


def test_the_proxy_wins(signed_in_mode, client):
    login(client)
    with TestClient(app, base_url="https://maps.test", client=PROXY) as proxied:
        proxied.cookies.update(client.cookies)
        adb.call(identity_service.create, "olga", ["viewer"])
        me = proxied.get("/api/v1/me", headers={"X-Forwarded-User": "olga"}).json()
    assert me["username"] == "olga" and me["role"] == "viewer"


def test_no_password_in_the_log(signed_in_mode, client, caplog):
    caplog.set_level(logging.DEBUG)
    login(client)
    login(client, password="Wrong-Secret-9!")
    assert "Jane-Pass-1!" not in caplog.text and "Wrong-Secret-9!" not in caplog.text
    assert SECRET not in caplog.text
    assert "AD sign-in: 'jdoe'" in caplog.text


def test_off_without_ad_host(override_settings, client):
    override_settings(AUTH_MODE="header")
    assert login(client).status_code == 404
    assert client.get("/auth", headers={"Accept": "text/html"}).status_code == 404


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"AD_HOST": "dc1", "JWT_SIGNING_SECRET": "short"}, "at least 32"),
        # An old .env (#782): AD on as before would be quietly off.
        ({"LDAP_URL": "ldaps://dc1", "JWT_SIGNING_SECRET": SECRET}, "set AD_HOST"),
    ],
)
def test_an_unsafe_setup_is_refused(monkeypatch, env, message):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(Exception, match=message):
        config.Settings()
