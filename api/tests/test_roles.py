"""Roles in the database (#713): the migration's five roles are the code's, a change in
the database reaches the checks after a refresh, and a database without roles (or
none at all) leaves the built-in ones."""

import asyncio
import importlib.util
import pathlib

import pytest

from app.auth import permissions
from app.services import role_service
from tests import db as adb

API_ROOT = pathlib.Path(__file__).resolve().parents[1]


def migration_seed() -> list:
    (path,) = API_ROOT.glob("alembic/versions/*_roles_and_identities.py")
    spec = importlib.util.spec_from_file_location("roles_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SEED


def test_the_migration_seeds_the_codes_roles():
    """Written out in the revision, compared role by role, scope by scope."""
    seeded = {
        name: (label, landing, text, set(scopes))
        for name, label, landing, text, scopes in migration_seed()
    }
    assert seeded == {
        name: (role.label, role.landing, role.description, {str(s) for s in role.scopes})
        for name, role in permissions.DEFAULT_ROLES.items()
    }


def empty_roles():
    adb.sql("TRUNCATE roles, permissions, identities, identities_to_roles CASCADE")


@pytest.fixture
def seeded():
    """The tables as a migrated database has them (each test empties them)."""
    empty_roles()
    adb.call(role_service.seed_defaults)
    role_service.forget()


def refresh():
    asyncio.run(role_service.refresh())


def test_read_from_the_database_they_are_the_matrix(seeded):
    permissions.ROLES.clear()
    refresh()
    assert permissions.ROLES == permissions.DEFAULT_ROLES
    for name in permissions.DEFAULT_ROLES:
        assert permissions.role_permissions(name) == permissions.DEFAULT_ROLES[name].permissions


def test_a_change_in_the_database_reaches_the_checks(seeded, monkeypatch):
    refresh()
    adb.sql(
        "INSERT INTO permissions (scope, role_id) "
        "SELECT 'alerts:*', id FROM roles WHERE name = 'viewer'"
    )
    adb.sql("UPDATE roles SET label = 'Read only' WHERE name = 'viewer'")
    adb.sql("INSERT INTO roles (name, label, landing) VALUES ('auditor', 'Auditor', '/')")
    # Within CACHE_SECONDS: the copy stands.
    monkeypatch.setattr(role_service, "CACHE_SECONDS", 30.0)
    refresh()
    assert "alerts:ack" not in permissions.role_permissions("viewer")
    role_service.forget()
    refresh()
    assert "alerts:ack" in permissions.role_permissions("viewer")
    assert permissions.ROLES["viewer"].label == "Read only"
    assert permissions.ROLES["auditor"].scopes == frozenset()


def test_without_roles_in_the_database_the_built_in_ones(caplog):
    empty_roles()
    permissions.ROLES.clear()
    role_service.forget()
    refresh()
    assert permissions.ROLES == permissions.DEFAULT_ROLES
    assert "No roles in the database" in caplog.text


def test_an_unreadable_database_keeps_the_last_ones(seeded, monkeypatch, caplog):
    refresh()
    permissions.ROLES["viewer"] = permissions.RoleDef("Kept", "/", frozenset())

    async def broken(conn):
        raise OSError("down")

    monkeypatch.setattr(role_service, "load", broken)
    role_service.forget()
    refresh()
    assert permissions.ROLES["viewer"].label == "Kept"
    assert "Roles could not be read, the last ones kept: OSError" in caplog.text


def test_seed_defaults_only_into_an_empty_table():
    empty_roles()
    assert adb.call(role_service.seed_defaults) == 5
    assert adb.call(role_service.seed_defaults) == 0
    assert adb.sql("SELECT count(*) AS n FROM roles WHERE built_in")[0]["n"] == 5
