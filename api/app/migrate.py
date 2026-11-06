"""Bring the database to the newest schema, adopting the current app's (#590).

    python -m app.migrate        (the container's entrypoint runs it)

- A database the current app migrated (nautobot_maps/db.py, `schema_migrations`
  at version 33) is stamped at the Alembic baseline, which is that schema, so its
  data is kept as is; then upgraded.
- An empty database, or one Alembic manages already: upgraded.
- Anything else is refused with what to do: the current app's schema below 33
  (upgrade with the current app first), above 33 (newer than this release), or
  its tables without a migration record.

Holds the current app's migration lock throughout, so neither app migrates the
database while the other does.
"""

import logging
from pathlib import Path

from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text

from alembic import command
from app.utils.config import get_settings

logger = logging.getLogger(__name__)

API_ROOT = Path(__file__).resolve().parent.parent
# The revision that is the current app's schema version 33.
BASELINE = "1423339b6a00"
CURRENT_APP_VERSION = 33
# nautobot_maps/db.py MIGRATION_LOCK_KEY: the same lock as the current app's migrations.
MIGRATION_LOCK_KEY = 674864467105151045
# A table of the current app's schema since its first version.
CURRENT_APP_TABLE = "alert_instances"


class MigrationRefused(RuntimeError):
    """The database can't be brought to this release's schema automatically."""


def alembic_config(url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    # ConfigParser interpolation: "%" (percent-encoded credentials) must be "%%".
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _exists(conn: Connection, table: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}).scalar())


def plan(conn: Connection) -> str:
    """What to do with this database: "upgrade" or "adopt"; refuses the rest."""
    if _exists(conn, "alembic_version"):
        return "upgrade"
    if _exists(conn, "schema_migrations"):
        version = conn.execute(text("SELECT COALESCE(max(version), 0) FROM schema_migrations"))
        version = int(version.scalar() or 0)
        if version == CURRENT_APP_VERSION:
            return "adopt"
        if version < CURRENT_APP_VERSION:
            raise MigrationRefused(
                f"The database is at the current app's schema version {version}; this release "
                f"takes over version {CURRENT_APP_VERSION}. Start the current app (nautobot_maps) "
                "once on it to migrate it, then start this one."
            )
        raise MigrationRefused(
            f"The database is at the current app's schema version {version}, newer than "
            f"{CURRENT_APP_VERSION}, which this release takes over. Was it migrated by a newer "
            "release of the current app? Run that release, or restore a backup."
        )
    if _exists(conn, CURRENT_APP_TABLE):
        raise MigrationRefused(
            "The database has the current app's tables but no schema_migrations record (from "
            "before versioned migrations). Start the current app (nautobot_maps) once on it to "
            "migrate it, then start this one."
        )
    return "upgrade"


def migrate(engine: Engine, url: str) -> str:
    """Adopt (when needed) and upgrade to head; returns what was done."""
    config = alembic_config(url)
    with engine.connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
        try:
            action = plan(lock)
            lock.commit()
            if action == "adopt":
                logger.info(
                    "Adopting the current app's database (schema version %s) as revision %s",
                    CURRENT_APP_VERSION,
                    BASELINE,
                )
                command.stamp(config, BASELINE)
            command.upgrade(config, "head")
            return action
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
            lock.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    url = get_settings().sqlalchemy_url()
    engine = create_engine(url)
    try:
        migrate(engine, url.render_as_string(hide_password=False))
    except MigrationRefused as refused:
        raise SystemExit(f"Not migrating: {refused}") from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
