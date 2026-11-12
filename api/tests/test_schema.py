"""Today's schema, and taking over a database the current app migrated (#590)."""

import json
import pathlib
import re

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import make_url, text

from alembic import command
from app import migrate
from app.models import Base
from tests import db as adb
from tests.conftest import TEST_DATABASE_URL
from tests.schema_snapshot import snapshot

SNAPSHOT = json.loads((pathlib.Path(__file__).parent / "schema_v33.json").read_text("utf-8"))
SCRATCH = make_url(TEST_DATABASE_URL).database + "_scratch"


def test_alembic_builds_the_current_apps_schema():
    """Column by column, constraint by constraint, index by index (and sequences)."""
    engine = adb.SyncEngine(TEST_DATABASE_URL)
    with engine.connect() as conn:
        assert json.loads(json.dumps(conn.run_sync(snapshot))) == SNAPSHOT


def test_the_models_match_the_migrations():
    engine = adb.SyncEngine(TEST_DATABASE_URL)
    with engine.connect() as conn:
        differences = conn.run_sync(
            lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
        )
        assert differences == []


@pytest.fixture
def scratch():
    """An empty database of its own: (engine, url), dropped afterwards."""
    admin = adb.SyncEngine(
        make_url(TEST_DATABASE_URL).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{SCRATCH}"'))
    url = make_url(TEST_DATABASE_URL).set(database=SCRATCH).render_as_string(hide_password=False)
    db = adb.SyncEngine(url)
    yield db, url
    db.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)'))
    admin.dispose()


def current_app_database(db, url: str, version: int = 33) -> None:
    """What the current app leaves: its schema (= the baseline), its migration
    record, no alembic_version, and some data."""
    command.upgrade(migrate.alembic_config(url), migrate.BASELINE)
    with db.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(
            text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"),
            [{"v": v, "n": f"step {v}"} for v in range(1, version + 1)],
        )
        conn.execute(
            text(
                "INSERT INTO api_tokens (name, token_hash, prefix, role, created_by) "
                "VALUES ('ops', 'hash-1', 'nmt_ab12', 'operator', 'admin')"
            )
        )
        conn.execute(text("SELECT nextval('librenms_push_seq')"))


def head(db) -> str | None:
    with db.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_an_existing_database_is_adopted_with_its_data(scratch):
    db, url = scratch
    current_app_database(db, url)
    assert migrate.migrate(make_url(url)) == "adopt"
    with db.connect() as conn:
        token = conn.execute(text("SELECT name, role FROM api_tokens")).one()
        assert tuple(token) == ("ops", "operator")
        # Sequences go on where they were.
        assert conn.execute(text("SELECT nextval('librenms_push_seq')")).scalar() == 2
        # The migration record stays, so the current app still starts on it.
        assert conn.execute(text("SELECT max(version) FROM schema_migrations")).scalar() == 33
        # Upgraded past the baseline.
        assert conn.execute(text("SELECT to_regclass('items') IS NOT NULL")).scalar()
    assert head(db) != migrate.BASELINE
    # Started again: an ordinary upgrade, nothing adopted twice.
    assert migrate.migrate(make_url(url)) == "upgrade"


def test_an_empty_database_is_created(scratch):
    db, url = scratch
    assert migrate.migrate(make_url(url)) == "upgrade"
    with db.connect() as conn:
        assert json.loads(json.dumps(conn.run_sync(snapshot))) == SNAPSHOT


@pytest.mark.parametrize(
    ("version", "message"),
    [
        (32, "Start the current app (nautobot_maps) once on it"),
        (34, "newer than 33"),
    ],
)
def test_another_version_is_refused(scratch, version, message):
    db, url = scratch
    current_app_database(db, url, version=version)
    with pytest.raises(migrate.MigrationRefused, match=re.escape(message)):
        migrate.migrate(make_url(url))
    with db.connect() as conn:
        assert not conn.execute(text("SELECT to_regclass('alembic_version') IS NOT NULL")).scalar()


def test_tables_without_a_migration_record_are_refused(scratch):
    db, url = scratch
    current_app_database(db, url)
    with db.begin() as conn:
        conn.execute(text("DROP TABLE schema_migrations"))
    with pytest.raises(migrate.MigrationRefused, match="no schema_migrations record"):
        migrate.migrate(make_url(url))


def migration_locks(conn) -> int:
    """Holders of the migration lock on this database (pg_locks lists every database's;
    a bigint key is its high and low 32 bits, objsubid 1)."""
    key = migrate.MIGRATION_LOCK_KEY & 0xFFFFFFFFFFFFFFFF
    return conn.execute(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objsubid = 1 "
            "AND database = (SELECT oid FROM pg_database WHERE datname = current_database()) "
            "AND classid = :high AND objid = :low"
        ),
        {"high": key >> 32, "low": key & 0xFFFFFFFF},
    ).scalar()


def test_the_lock_is_released(scratch):
    db, url = scratch
    with db.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": migrate.MIGRATION_LOCK_KEY})
        with db.connect() as conn:
            assert migration_locks(conn) == 1, "the query sees the lock while it is held"
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": migrate.MIGRATION_LOCK_KEY})
    migrate.migrate(make_url(url))
    with db.connect() as conn:
        assert migration_locks(conn) == 0


def test_an_adopted_database_a_newer_current_app_migrated_is_refused(scratch):
    """Adopted, then migrated to 34 by a newer release of the current app."""
    db, url = scratch
    current_app_database(db, url)
    migrate.migrate(make_url(url))
    with db.begin() as conn:
        conn.execute(text("INSERT INTO schema_migrations (version, name) VALUES (34, 'newer')"))
    with pytest.raises(migrate.MigrationRefused, match="newer than 33"):
        migrate.migrate(make_url(url))


def test_the_current_app_is_still_at_version_33():
    """A new step in nautobot_maps/db.py MIGRATIONS must come here too (as an
    Alembic revision, and CURRENT_APP_VERSION), or adopting would refuse it."""
    db_py = pathlib.Path(__file__).resolve().parents[2] / "nautobot_maps" / "db.py"
    if not db_py.is_file():
        pytest.skip("the current app is gone")
    steps = re.findall(r"^    \((\d+), \"", db_py.read_text("utf-8"), re.MULTILINE)
    assert int(steps[-1]) == migrate.CURRENT_APP_VERSION


def test_the_example_stacks_database_is_upgraded_with_its_items(scratch):
    """A database the example stack (#573) made: only items, at revision 0ddedf85d6f0."""
    db, url = scratch
    with db.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE items (id SERIAL PRIMARY KEY, name VARCHAR(200) NOT NULL UNIQUE, "
                "description TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
        )
        conn.execute(text("INSERT INTO items (name) VALUES ('kept')"))
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('0ddedf85d6f0')"))
    assert migrate.migrate(make_url(url)) == "from-example"
    with db.connect() as conn:
        assert conn.execute(text("SELECT name FROM items")).scalar() == "kept"
        assert conn.execute(text("SELECT to_regclass('alert_instances') IS NOT NULL")).scalar()
    assert head(db) == migrate.ITEMS_REVISION
    assert migrate.migrate(make_url(url)) == "upgrade"


def test_a_failed_example_upgrade_leaves_it_as_it_was(scratch, monkeypatch):
    """Atomic (#601 review): a failure halfway leaves the example database as it was, so
    the next start upgrades it again."""
    db, url = scratch
    with db.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE items (id SERIAL PRIMARY KEY, name VARCHAR(200) NOT NULL UNIQUE, "
                "description TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
        )
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('0ddedf85d6f0')"))
    real_stamp = command.stamp

    def failing_stamp(config, revision, *args, **kwargs):
        if revision == migrate.ITEMS_REVISION:
            raise RuntimeError("interrupted")
        return real_stamp(config, revision, *args, **kwargs)

    monkeypatch.setattr(migrate.command, "stamp", failing_stamp)
    with pytest.raises(RuntimeError, match="interrupted"):
        migrate.migrate(make_url(url))
    assert head(db) == "0ddedf85d6f0"
    with db.connect() as conn:
        assert not conn.execute(text("SELECT to_regclass('alert_instances') IS NOT NULL")).scalar()
    monkeypatch.setattr(migrate.command, "stamp", real_stamp)
    assert migrate.migrate(make_url(url)) == "from-example"
    assert head(db) == migrate.ITEMS_REVISION
