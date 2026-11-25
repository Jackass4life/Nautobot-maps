"""Identities (#715): someone signed in through the proxy is looked up by name and
gets their identity's roles and own scopes; without one, their groups' role
(AUTH_GROUP_ROLES) or no access. The CLI manages them."""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app import __main__ as cli
from app.api import app
from app.services import identity_service as identities
from app.services import role_service
from tests import db as adb
from tests.test_auth import PROXY, as_user, me


@pytest.fixture
def signed_in(override_settings, pg_database):
    """Header mode with the roles in the database, as a migrated one has them."""
    adb.sql("TRUNCATE roles, permissions, identities, identities_to_roles CASCADE")
    adb.call(role_service.seed_defaults)
    override_settings(AUTH_MODE="header", AUTH_VIEWER_GROUPS="staff", AUTH_ADMIN_GROUPS="netadmins")


@pytest.fixture
def via_proxy():
    with TestClient(app, client=PROXY) as c:
        yield c


def run(*args: str) -> int:
    return cli.main(["identity", *args])


def test_an_identity_gives_its_roles_and_own_scopes(signed_in, via_proxy, capsys):
    assert (
        run("create", "Jane", "--role", "customer", "--role", "noc", "--common-name", "Jane D") == 0
    )
    assert run("add-scope", "jane", "maintenance:write") == 0
    capsys.readouterr()
    # Her groups say admin: her identity decides.
    answer = me(via_proxy, as_user("JANE", "netadmins"))
    access = answer["access"]
    assert answer["role"] == "noc" and access["roles"] == ["noc", "customer"]
    assert access["identity"] == "jane" and access["label"] == "NOC" and access["landing"] == "/noc"
    assert {"alerts:ack", "tenant:status:view", "maintenance:write"} <= set(access["permissions"])
    assert "admin" not in access["permissions"]
    # Her own scope works on its route; the role's limits elsewhere.
    headers = as_user("jane")
    assert via_proxy.get("/api/v1/tokens", headers=headers).status_code == 403


def test_without_an_identity_the_groups_role_logged_once(signed_in, via_proxy, caplog):
    caplog.set_level(logging.INFO, logger="app.services.identity_service")
    assert me(via_proxy, as_user("bob", "staff"))["role"] == "viewer"
    assert me(via_proxy, as_user("bob", "staff"))["access"]["identity"] is None
    assert caplog.text.count("Signed in without an identity: 'bob' (role from groups: viewer)") == 1


def test_without_an_identity_no_access_when_groups_are_off(signed_in, via_proxy, override_settings):
    override_settings(
        AUTH_MODE="header",
        AUTH_VIEWER_GROUPS="staff",
        AUTH_GROUP_ROLES="false",
        AUTH_REQUIRE_VIEWER="true",
    )
    headers = as_user("bob", "staff")
    refused = via_proxy.get("/api/v1/alerts", headers=headers)
    assert refused.status_code == 403 and refused.json()["error"] == "No access"
    assert "ask an admin to add you" in refused.json()["detail"]
    assert via_proxy.get("/api/v1/me", headers=headers).json()["error"] == "No access"
    # Without AUTH_REQUIRE_VIEWER reads are open to anyone (#592): those, no more.
    override_settings(AUTH_REQUIRE_VIEWER="false")
    answer = me(via_proxy, headers)
    assert answer["role"] is None and answer["access"]["label"] == "No identity"
    assert "alerts:ack" not in answer["access"]["permissions"]
    refused = via_proxy.post("/api/v1/alert-acks", json={}, headers=headers)
    assert refused.status_code == 403 and refused.json()["error"] == "No access"
    override_settings(AUTH_MODE="header", AUTH_GROUP_ROLES="false", AUTH_REQUIRE_VIEWER="true")
    # Added: in (a fresh lookup; the running app's within 30 s).
    assert run("create", "bob", "--role", "viewer") == 0
    identities.forget()
    assert via_proxy.get("/api/v1/alerts", headers=headers).status_code != 403


def test_lookups_are_cached(signed_in, via_proxy, monkeypatch):
    assert run("create", "jane", "--role", "viewer") == 0
    assert me(via_proxy, as_user("jane"))["role"] == "viewer"
    assert run("add-role", "jane", "admin") == 0
    assert me(via_proxy, as_user("jane"))["role"] == "viewer", "within 30 s: the copy"
    monkeypatch.setattr(identities, "CACHE_SECONDS", 0.0)
    assert me(via_proxy, as_user("jane"))["role"] == "admin"


def test_an_unreadable_database_keeps_the_last_lookup(signed_in, monkeypatch):
    assert run("create", "jane", "--role", "noc") == 0
    assert asyncio.run(identities.lookup("jane")).roles == ("noc",)
    monkeypatch.setattr(identities, "CACHE_SECONDS", 0.0)

    async def broken(conn, name):
        raise OSError("down")

    monkeypatch.setattr(identities, "read", broken)
    assert asyncio.run(identities.lookup("jane")).roles == ("noc",)
    assert asyncio.run(identities.lookup("someone")) is None


def test_the_cli(signed_in, capsys):
    assert run("list") == 0 and capsys.readouterr().out.strip() == "No identities."
    # Another admin: deleting the last one is refused (#721).
    assert run("create", "root", "--role", "admin") == 0
    capsys.readouterr()
    assert (
        run("create", "jane", "--role", "noc", "--scope", "Alerts:*", "--common-name", "Jane D")
        == 0
    )
    assert capsys.readouterr().out.strip() == "jane (Jane D): noc; scopes alerts:*"
    assert run("add-role", "jane", "admin") == 0
    assert capsys.readouterr().out.strip() == "jane (Jane D): admin, noc; scopes alerts:*"
    assert run("add-scope", "jane", "alerts:*") == 0, "twice: once"
    assert run("remove-role", "jane", "noc") == 0
    assert run("remove-scope", "jane", "alerts:*") == 0
    capsys.readouterr()
    assert run("list") == 0
    assert capsys.readouterr().out.strip() == "jane (Jane D): admin\nroot: admin"
    assert run("delete", "jane") == 0 and "deleted" in capsys.readouterr().out
    assert (
        adb.sql("SELECT count(*) AS n FROM permissions WHERE identity_id IS NOT NULL")[0]["n"] == 0
    )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["create", "jane", "--role", "root"], "no role 'root'"),
        (["create", "jane doe"], "no spaces"),
        (["create", "jane", "--scope", "alerts ack"], "scope 'alerts ack'"),
        (["create", "jane", "--scope", "alerts::ack"], "scope 'alerts::ack'"),
        (["add-role", "nobody", "noc"], "no identity 'nobody'"),
        (["delete", "nobody"], "no identity 'nobody'"),
    ],
)
def test_the_cli_refuses(signed_in, capsys, args, message):
    assert run(*args) == 1
    assert message in capsys.readouterr().err


def test_a_name_twice(signed_in, capsys):
    assert run("create", "jane") == 0
    assert run("create", "JANE") == 1
    assert "exists already" in capsys.readouterr().err


def test_the_highest_role_first():
    assert identities.by_priority(["viewer", "auditor", "operator", "admin", "beta"]) == (
        "admin",
        "operator",
        "viewer",
        "auditor",
        "beta",
    )


def test_the_api(signed_in, via_proxy):
    """/api/v1/identities (#715): what the CLI does, admin only."""
    run("create", "root", "--role", "admin")
    admin = as_user("root")
    made = via_proxy.post(
        "/api/v1/identities",
        json={"name": "Jane", "roles": ["noc"], "common_name": "Jane D"},
        headers=admin,
    )
    assert made.status_code == 201
    assert made.json()["identity"] == {
        "unique_name": "jane",
        "common_name": "Jane D",
        "description": "",
        "roles": ["noc"],
        "scopes": [],
        "keys": 0,
        "for_a_key": False,
    }
    added = via_proxy.post(
        "/api/v1/identities/jane/roles", json={"role": "customer"}, headers=admin
    )
    assert added.json()["identity"]["roles"] == ["noc", "customer"]
    scoped = via_proxy.post(
        "/api/v1/identities/jane/scopes", json={"scope": "alerts:*"}, headers=admin
    )
    assert scoped.json()["identity"]["scopes"] == ["alerts:*"]
    # Changed here: this worker reads her again at once.
    assert me(via_proxy, as_user("jane"))["access"]["roles"] == ["noc", "customer"]
    removed = via_proxy.delete("/api/v1/identities/jane/scopes/alerts:*", headers=admin)
    assert removed.json()["identity"]["scopes"] == []
    removed = via_proxy.delete("/api/v1/identities/jane/roles/noc", headers=admin)
    assert removed.json()["identity"]["roles"] == ["customer"]
    listed = via_proxy.get("/api/v1/identities", headers=admin).json()["identities"]
    assert [each["unique_name"] for each in listed] == ["jane", "root"]
    # Jane, a customer now, may not.
    assert via_proxy.get("/api/v1/identities", headers=as_user("jane")).status_code == 403
    assert via_proxy.delete("/api/v1/identities/jane", headers=admin).json() == {"deleted": "jane"}
    assert via_proxy.delete("/api/v1/identities/jane", headers=admin).status_code == 404
    bad = via_proxy.post("/api/v1/identities", json={"name": "x", "roles": ["root"]}, headers=admin)
    assert bad.status_code == 400 and "no role 'root'" in bad.json()["error"]
    assert via_proxy.post("/api/v1/identities", json=[], headers=admin).status_code == 400


def test_a_browser_gets_a_page_saying_so(signed_in, via_proxy, override_settings):
    """No identity, AUTH_GROUP_ROLES=false: a page, not JSON, with the way out."""
    override_settings(
        AUTH_MODE="header",
        AUTH_GROUP_ROLES="false",
        AUTH_REQUIRE_VIEWER="true",
        AUTH_SIGN_OUT_URL="/oauth2/sign_out",
    )
    headers = {**as_user("<bob>"), "Accept": "text/html"}
    page = via_proxy.get("/", headers=headers)
    assert page.status_code == 403 and page.headers["content-type"].startswith("text/html")
    assert "<h1>No access</h1>" in page.text and "&lt;bob&gt;" in page.text
    assert 'href="/oauth2/sign_out"' in page.text and "<script" not in page.text
    assert via_proxy.get("/api/v1/me", headers=headers).json()["error"] == "No access"
