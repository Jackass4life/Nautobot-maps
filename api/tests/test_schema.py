"""Today's schema, and taking over a database the current app migrated (#590)."""

import json
import pathlib
import re

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, make_url, text

from alembic import command
from app import migrate
from app.models import Base
from app.session import engine
from tests.conftest import TEST_DATABASE_URL
from tests.schema_snapshot import snapshot

SNAPSHOT = json.loads((pathlib.Path(__file__).parent / "schema_v33.json").read_text("utf-8"))
SCRATCH = make_url(TEST_DATABASE_URL).database + "_scratch"


def test_alembic_builds_the_current_apps_schema():
    """Column by column, constraint by constraint, index by index (and sequences)."""
    with engine.connect() as conn:
        assert json.loads(json.dumps(snapshot(conn))) == SNAPSHOT


def test_the_models_match_the_migrations():
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []


@pytest.fixture
def scratch():
    """An empty database of its own: (engine, url), dropped afterwards."""
    admin = create_engine(make_url(TEST_DATABASE_URL).set(database="postgres"))
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{SCRATCH}"'))
    url = make_url(TEST_DATABASE_URL).set(database=SCRATCH).render_as_string(hide_password=False)
    db = create_engine(url)
    yield db, url
    db.dispose()
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
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
    assert migrate.migrate(db, url) == "adopt"
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
    assert migrate.migrate(db, url) == "upgrade"


def test_an_empty_database_is_created(scratch):
    db, url = scratch
    assert migrate.migrate(db, url) == "upgrade"
    with db.connect() as conn:
        assert json.loads(json.dumps(snapshot(conn))) == SNAPSHOT


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
        migrate.migrate(db, url)
    with db.connect() as conn:
        assert not conn.execute(text("SELECT to_regclass('alembic_version') IS NOT NULL")).scalar()


def test_tables_without_a_migration_record_are_refused(scratch):
    db, url = scratch
    current_app_database(db, url)
    with db.begin() as conn:
        conn.execute(text("DROP TABLE schema_migrations"))
    with pytest.raises(migrate.MigrationRefused, match="no schema_migrations record"):
        migrate.migrate(db, url)


def test_the_lock_is_released(scratch):
    db, url = scratch
    migrate.migrate(db, url)
    with db.connect() as conn:
        held = conn.execute(
            text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")
        ).scalar()
        assert held == 0


def test_the_current_app_is_still_at_version_33():
    """A new step in nautobot_maps/db.py MIGRATIONS must come here too (as an
    Alembic revision, and CURRENT_APP_VERSION), or adopting would refuse it."""
    db_py = pathlib.Path(__file__).resolve().parents[2] / "nautobot_maps" / "db.py"
    if not db_py.is_file():
        pytest.skip("the current app is gone")
    steps = re.findall(r"^    \((\d+), \"", db_py.read_text("utf-8"), re.MULTILINE)
    assert int(steps[-1]) == migrate.CURRENT_APP_VERSION
