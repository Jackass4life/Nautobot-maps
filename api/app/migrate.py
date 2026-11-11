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

import asyncio
from pathlib import Path

from alembic.config import Config
from loguru import logger
from sqlalchemy import URL, Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.core.settings import get_settings
from app.db import session
from app.logs import config as logs

API_ROOT = Path(__file__).resolve().parent.parent
# The revision that is the current app's schema version 33.
BASELINE = "1423339b6a00"
CURRENT_APP_VERSION = 33
# nautobot_maps/db.py MIGRATION_LOCK_KEY: the same lock as the current app's migrations.
MIGRATION_LOCK_KEY = 674864467105151045
# The example stack's only revision (#573), before the baseline existed: such a database
# holds just the items table.
EXAMPLE_REVISION = "0ddedf85d6f0"
ITEMS_REVISION = "b5b9fde7cf8b"
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


def _current_app_version(conn: Connection) -> int | None:
    """The current app's schema version (its ``schema_migrations``), None without one."""
    if not _exists(conn, "schema_migrations"):
        return None
    version = conn.execute(text("SELECT COALESCE(max(version), 0) FROM schema_migrations"))
    return int(version.scalar() or 0)


def plan(conn: Connection) -> str:
    """What to do with this database: "upgrade" or "adopt"; refuses the rest."""
    version = _current_app_version(conn)
    # Also after adoption: the current app can still start on the database, and a newer
    # release of it may have migrated it since (#590 review).
    if version is not None and version > CURRENT_APP_VERSION:
        raise MigrationRefused(
            f"The database is at the current app's schema version {version}, newer than "
            f"{CURRENT_APP_VERSION}, which this release takes over. Was it migrated by a newer "
            "release of the current app? Run that release, or restore a backup."
        )
    if _exists(conn, "alembic_version"):
        revisions = {
            row[0] for row in conn.execute(text("SELECT version_num FROM alembic_version"))
        }
        if revisions == {EXAMPLE_REVISION}:
            return "from-example"
        return "upgrade"
    if version is not None:
        if version == CURRENT_APP_VERSION:
            return "adopt"
        raise MigrationRefused(
            f"The database is at the current app's schema version {version}; this release "
            f"takes over version {CURRENT_APP_VERSION}. Start the current app (nautobot_maps) "
            "once on it to migrate it, then start this one."
        )
    if _exists(conn, CURRENT_APP_TABLE):
        raise MigrationRefused(
            "The database has the current app's tables but no schema_migrations record (from "
            "before versioned migrations). Start the current app (nautobot_maps) once on it to "
            "migrate it, then start this one."
        )
    return "upgrade"


def _command(conn: Connection, config: Config, name: str, revision: str) -> None:
    """Alembic's *name* (upgrade, stamp) on *conn*: alembic/env.py runs it there."""
    config.attributes["connection"] = conn
    try:
        getattr(command, name)(config, revision)
    finally:
        config.attributes.pop("connection", None)


async def _migrate(engine: AsyncEngine, url: str) -> str:
    config = alembic_config(url)
    async with engine.connect() as lock:
        await lock.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
        try:
            action = await lock.run_sync(plan)
            await lock.commit()
            if action == "adopt":
                logger.info(
                    "Adopting the current app's database (schema version {}) as revision {}",
                    CURRENT_APP_VERSION,
                    BASELINE,
                )
                async with engine.begin() as conn:
                    await conn.run_sync(_command, config, "stamp", BASELINE)
            if action == "from-example":
                # The example stack's database (#573): its items kept, the schema built
                # under it. One transaction (#601 review): the revision's removal, the
                # baseline and the stamp all happen, or none does, so a failed start can
                # simply be retried.
                logger.info("Upgrading the example stack's database ({})", EXAMPLE_REVISION)
                async with engine.begin() as conn:
                    await conn.execute(text("DELETE FROM alembic_version"))
                    await conn.run_sync(_command, config, "upgrade", BASELINE)
                    await conn.run_sync(_command, config, "stamp", ITEMS_REVISION)
            async with engine.begin() as conn:
                await conn.run_sync(_command, config, "upgrade", "head")
            return action
        finally:
            await lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
            await lock.commit()


def engine_for(url: URL) -> AsyncEngine:
    """An asyncpg engine for migrations (#643): a connection per use, the app's connect
    timeout and no statement timeout (a migration may take longer)."""
    async_url, args = session.async_url(url, get_settings().db_connect_timeout_seconds, 0)
    return create_async_engine(async_url, poolclass=NullPool, connect_args=args)


def migrate(url: URL) -> str:
    """Adopt (when needed) and upgrade to head; returns what was done."""

    async def run() -> str:
        engine = engine_for(url)
        try:
            return await _migrate(engine, url.render_as_string(hide_password=False))
        finally:
            await engine.dispose()

    return asyncio.run(run())


def main() -> None:
    if not logs.configured():
        logs.configure()
    try:
        migrate(get_settings().sqlalchemy_url())
    except MigrationRefused as refused:
        raise SystemExit(f"Not migrating: {refused}") from None


if __name__ == "__main__":
    main()
