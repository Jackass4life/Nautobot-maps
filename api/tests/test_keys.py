"""API keys belong to identities (#719): the upgrade gives each token an identity, a
key has exactly its identity's rights (read live), by Bearer or X-API-KEY."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic import command
from app import __main__ as cli
from app import migrate
from app.api import app
from app.services import identity_service, token_service
from tests import db as adb
from tests.test_schema import scratch  # noqa: F401 - a fixture
from tests.test_tokens import clock  # noqa: F401 - a fixture

BEFORE = "8fef86e0beb4"  # roles and identities, before keys had one


def test_the_upgrade_gives_every_token_an_identity(scratch):  # noqa: F811
    db, url = scratch
    config = migrate.alembic_config(url)
    command.upgrade(config, BEFORE)
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO api_tokens (name, token_hash, prefix, role, tenant) VALUES "
                "('LibreNMS push', 'h1', 'nmt_a', 'operator', NULL), "
                "('wall', 'h2', 'nmt_b', 'noc', NULL), "
                "('acme', 'h3', 'nmt_c', 'status', 'Acme Corp')"
            )
        )
        # A person already called "wall": the token's identity is "wall-key".
        conn.execute(text("INSERT INTO identities (unique_name) VALUES ('wall')"))
    command.upgrade(config, "head")
    with db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT t.name, t.token_hash, i.unique_name, "
                "(SELECT string_agg(r.name, ',') FROM identities_to_roles ir "
                " JOIN roles r ON r.id = ir.role_id WHERE ir.identity_id = i.id) AS roles "
                "FROM api_tokens t LEFT JOIN identities i ON i.id = t.identity_id ORDER BY t.name"
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("LibreNMS push", "h1", "librenms-push", "operator"),
        ("acme", "h3", None, None),
        ("wall", "h2", "wall-key", "noc"),
    ]
    command.downgrade(config, BEFORE)
    with db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM api_tokens")).scalar() == 3
        names = conn.execute(text("SELECT unique_name FROM identities")).scalars().all()
    assert names == ["wall"], "the person's stays, the tokens' go"


@pytest.fixture
def client(clock):  # noqa: F811
    with TestClient(app) as c:
        yield c


def make(**body):
    return adb.run(token_service.create, {"name": "k", **body}, "admin-user")


def test_a_key_has_its_identitys_rights_read_live(client):
    key = make(role="viewer")["token"]
    for headers in ({"Authorization": f"Bearer {key}"}, {"X-API-KEY": key}):
        assert client.get("/api/v1/tokens", headers=headers).status_code == 403
    adb.call(identity_service.add_role, "k", "admin")
    identity_service.forget()  # the other workers: within 30 s
    for headers in ({"Authorization": f"Bearer {key}"}, {"X-API-KEY": key}):
        assert client.get("/api/v1/tokens", headers=headers).status_code == 200
    adb.call(identity_service.delete, "k")
    identity_service.forget()
    refused = client.get("/api/v1/alerts", headers={"X-API-KEY": key})
    assert refused.status_code == 401, "its keys went with the identity"


def test_a_key_for_an_existing_identity(client, capsys):
    adb.call(identity_service.create, "jdoe", ["noc"], ["maintenance:write"])
    assert cli.main(["token", "create", "jdoe-script", "--identity", "JDoe"]) == 0
    key = capsys.readouterr().out.strip()
    me = client.get("/api/v1/me", headers={"X-API-KEY": key}).json()
    assert me["role"] == "noc" and me["access"]["identity"] == "jdoe"
    assert "maintenance:write" in me["access"]["permissions"]
    assert cli.main(["token", "list"]) == 0
    assert "jdoe-script  identity jdoe" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"role": "noc", "identity": "jdoe"}, "a role or an identity, not both"),
        ({"identity": "nobody"}, "no identity 'nobody'"),
        ({"identity": ""}, "identity: the name"),
    ],
)
def test_refused(client, body, message):
    with pytest.raises(token_service.TokenError, match=message):
        make(**body)


def test_a_persons_name_is_not_taken_over(client):
    adb.call(identity_service.create, "k", ["viewer"])
    with pytest.raises(token_service.TokenError, match="identity 'k' exists already"):
        make(role="admin")


def test_a_purge_frees_the_name_and_its_identity(client):
    made = make(role="noc")
    adb.call(identity_service.create, "person", ["viewer"])
    adb.run(token_service.revoke, made["id"], "admin-user")
    with pytest.raises(token_service.TokenError, match="already exists"):
        make(role="noc")
    adb.run(token_service.purge, "admin-user")
    names = [row["unique_name"] for row in adb.sql("SELECT unique_name FROM identities")]
    assert names == ["person"]
    assert make(role="viewer")["identity"] == "k"


def test_a_status_token_has_no_identity(client, capsys):
    from app.services import inventory_service as inventory

    adb.call(inventory.write_tenants, [{"tenant_id": "t1", "name": "Acme"}])
    made = make(role="status", tenant="Acme")
    assert made["identity"] is None
    assert client.get("/api/v1/status/x", headers={"X-API-KEY": made["token"]}).status_code != 401
