"""Changing roles (#721): the API the admin page uses and ``role ...``, admin only.
The built-in roles are never deleted, and no change leaves nobody an admin."""

import json
from pathlib import Path

import pytest

from app import __main__ as cli
from app.auth import permissions
from app.services import identity_service as identities
from app.services import role_service
from tests import db as adb
from tests import test_identities
from tests.test_auth import as_user, me
from tests.test_identities import run as identity

signed_in = test_identities.signed_in
via_proxy = test_identities.via_proxy


def role(*args: str) -> int:
    return cli.main(["role", *args])


@pytest.fixture
def admin(signed_in):
    assert identity("create", "root", "--role", "admin") == 0
    return as_user("root")


def test_the_api(admin, via_proxy):
    listed = via_proxy.get("/api/v1/roles", headers=admin).json()["roles"]
    assert [each["name"] for each in listed] == ["admin", "operator", "noc", "customer", "viewer"]
    assert listed[0]["built_in"] and listed[0]["identities"] == 1
    assert listed[0]["scopes"] == ["*", "*:*", "*:*:*"]
    made = via_proxy.post(
        "/api/v1/roles",
        json={"name": "Auditor", "label": "Auditor", "landing": "/noc", "scopes": ["alerts:*"]},
        headers=admin,
    )
    assert made.status_code == 201
    assert made.json()["role"] == {
        "name": "auditor",
        "label": "Auditor",
        "landing": "/noc",
        "description": "",
        "built_in": False,
        "scopes": ["alerts:*", "session:basic"],
        "identities": 0,
    }
    added = via_proxy.post(
        "/api/v1/roles/auditor/scopes", json={"scope": "page:wall"}, headers=admin
    )
    assert added.json()["role"]["scopes"] == ["alerts:*", "page:wall", "session:basic"]
    again = via_proxy.post(
        "/api/v1/roles/auditor/scopes", json={"scope": "page:wall"}, headers=admin
    )
    assert again.json()["role"]["scopes"] == ["alerts:*", "page:wall", "session:basic"], "once"
    changed = via_proxy.patch(
        "/api/v1/roles/auditor", json={"description": "Reads the wall"}, headers=admin
    )
    assert changed.json()["role"]["description"] == "Reads the wall"
    assert changed.json()["role"]["label"] == "Auditor", "the others stay"
    # Given to someone: at once on this worker, with its label and start page.
    assert identity("create", "jane", "--role", "auditor") == 0
    access = me(via_proxy, as_user("jane"))["access"]
    assert access["roles"] == ["auditor"] and access["label"] == "Auditor"
    assert access["landing"] == "/noc" and "alerts:ack" in access["permissions"]
    removed = via_proxy.delete("/api/v1/roles/auditor/scopes/alerts:*", headers=admin)
    assert removed.json()["role"]["scopes"] == ["page:wall", "session:basic"]
    assert "alerts:ack" not in me(via_proxy, as_user("jane"))["access"]["permissions"]
    # Jane may not change roles.
    assert via_proxy.get("/api/v1/roles", headers=as_user("jane")).status_code == 403
    gone = via_proxy.delete("/api/v1/roles/auditor", headers=admin)
    assert gone.json() == {"deleted": "auditor", "identities": 1}
    assert me(via_proxy, as_user("jane"))["access"]["label"] == "No role"
    assert "auditor" not in permissions.ROLES, "read again on that request"
    assert via_proxy.delete("/api/v1/roles/auditor", headers=admin).status_code == 404


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"name": "noc"}, "exists already"),
        ({"name": "status"}, "a token's role"),
        ({"name": "9lives"}, "a-z first"),
        ({"name": "a b"}, "a-z first"),
        ({"name": "x", "scopes": ["alerts ack"]}, "scope 'alerts ack'"),
        ({"name": "x", "landing": "//evil.example"}, "a page of this app"),
        ({"name": "x", "landing": "https://evil.example"}, "a page of this app"),
        ([], "Expected {name"),
    ],
)
def test_a_new_role_refused(admin, via_proxy, body, message):
    refused = via_proxy.post("/api/v1/roles", json=body, headers=admin)
    assert refused.status_code == 400 and message in refused.json()["error"]
    assert adb.sql("SELECT count(*) AS n FROM roles")[0]["n"] == 5


def test_a_built_in_role_is_never_deleted(admin, via_proxy):
    refused = via_proxy.delete("/api/v1/roles/viewer", headers=admin)
    assert refused.status_code == 400 and "built in" in refused.json()["error"]
    # Its scopes can change.
    changed = via_proxy.delete("/api/v1/roles/viewer/scopes/export:csv", headers=admin)
    assert "export:csv" not in changed.json()["role"]["scopes"]


def test_the_admin_scope_kept(admin, via_proxy):
    # admin (one part) is covered by * alone: without it no role would be an admin.
    assert via_proxy.delete("/api/v1/roles/admin/scopes/*:*", headers=admin).is_success
    refused = via_proxy.delete("/api/v1/roles/admin/scopes/*", headers=admin)
    assert refused.status_code == 400 and "no role would hold" in refused.json()["error"]
    assert "*" in permissions.ROLES["admin"].scopes, "rolled back"
    # With admin written out, * can go.
    added = via_proxy.post("/api/v1/roles/admin/scopes", json={"scope": "admin"}, headers=admin)
    assert added.is_success
    assert via_proxy.delete("/api/v1/roles/admin/scopes/*", headers=admin).is_success
    assert via_proxy.get("/api/v1/roles", headers=admin).is_success


def test_the_last_admin_identity_kept(admin, via_proxy):
    for change in (
        lambda: via_proxy.delete("/api/v1/identities/root/roles/admin", headers=admin),
        lambda: via_proxy.delete("/api/v1/identities/root", headers=admin),
    ):
        refused = change()
        assert refused.status_code == 400 and "nobody would be an admin" in refused.json()["error"]
    assert identity("delete", "root") == 1
    # Another admin first, by a scope of their own: then root may go.
    assert identity("create", "jane", "--scope", "admin") == 0
    assert via_proxy.delete("/api/v1/identities/root", headers=admin).is_success


def test_the_cli(admin, capsys):
    capsys.readouterr()
    assert role("create", "Auditor", "--scope", "alerts:*", "--landing", "/noc") == 0
    assert capsys.readouterr().out.splitlines() == [
        "auditor (auditor), opens /noc; 0 identities",
        "  alerts:*",
        "  session:basic",
    ]
    assert role("update", "auditor", "--label", "Auditors") == 0
    assert role("add-scope", "auditor", "page:wall") == 0
    assert role("remove-scope", "auditor", "alerts:*") == 0
    capsys.readouterr()
    assert role("list") == 0
    out = capsys.readouterr().out
    assert "admin (Admin, built in), opens /; 1 identities" in out
    assert "auditor (Auditors), opens /noc; 0 identities\n  page:wall\n  session:basic" in out
    assert role("delete", "auditor") == 0
    assert "deleted; 0 identities had it" in capsys.readouterr().out
    assert role("delete", "viewer") == 1 and "built in" in capsys.readouterr().err
    assert role("remove-scope", "admin", "*") == 1
    assert "no role would hold" in capsys.readouterr().err
    assert role("add-scope", "nobody", "x") == 1 and "no role 'nobody'" in capsys.readouterr().err


CASES = json.loads(
    (Path(__file__).parents[2] / "web" / "tests" / "fixtures" / "access-cases.json").read_text()
)


@pytest.mark.parametrize(("typed", "stored"), CASES["scopes"].items())
def test_a_scope_as_the_page_checks_it(typed, stored):
    """The Admin page checks a scope as typed (web/utils/access.ts, the same cases)."""
    if stored is None:
        with pytest.raises(identities.IdentityError):
            identities.check_scope(typed)
    else:
        assert identities.check_scope(typed) == stored


@pytest.mark.parametrize(("name", "valid"), CASES["role_names"].items())
def test_a_role_name_as_the_page_checks_it(signed_in, name, valid):
    async def make(conn):  # type: ignore[no-untyped-def]
        await role_service.create(conn, name)

    if valid:
        adb.call(make)
    else:
        with pytest.raises(identities.IdentityError):
            adb.call(make)
