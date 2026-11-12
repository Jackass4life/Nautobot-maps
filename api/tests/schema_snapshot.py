"""Today's schema as data (#590): what a test compares Alembic's result with.

    uv run python -m tests.schema_snapshot postgresql://.../<db> > tests/schema_v33.json

writes it from a database the current app (nautobot_maps/db.py) migrated to
version 33; tests/schema_v33.json is that snapshot, the reference once the
current app's migrations are gone.
"""

import json
import sys

from sqlalchemy import Connection, text

# The new stack's own tables, not the current app's schema.
OWN_TABLES = ("alembic_version", "items")


def snapshot(conn: Connection) -> dict:
    own = list(OWN_TABLES)

    def rows(sql: str) -> list[list]:
        return [list(row) for row in conn.execute(text(sql), {"own": own})]

    return {
        "columns": rows(
            """
            SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
                   pg_get_expr(d.adbin, d.adrelid)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE n.nspname = current_schema() AND c.relkind = 'r' AND a.attnum > 0
              AND NOT a.attisdropped AND c.relname <> ALL(:own)
            ORDER BY c.relname, a.attname
            """
        ),
        "constraints": rows(
            """
            SELECT c.relname, k.conname, k.contype::text, pg_get_constraintdef(k.oid)
            FROM pg_constraint k
            JOIN pg_class c ON c.oid = k.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND c.relname <> ALL(:own)
            ORDER BY c.relname, k.conname
            """
        ),
        "indexes": rows(
            """
            SELECT tablename, indexname, indexdef FROM pg_indexes
            WHERE schemaname = current_schema() AND tablename <> ALL(:own)
            ORDER BY tablename, indexname
            """
        ),
        "sequences": rows(
            """
            SELECT s.sequencename, s.data_type::text, s.start_value, s.increment_by, s.cycle,
                   (SELECT c.relname || '.' || a.attname FROM pg_depend d
                    JOIN pg_class c ON c.oid = d.refobjid
                    JOIN pg_attribute a ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid
                    WHERE d.objid = (
                        quote_ident(s.schemaname) || '.' || quote_ident(s.sequencename)
                    )::regclass
                      AND d.deptype IN ('a', 'i'))
            FROM pg_sequences s
            WHERE s.schemaname = current_schema()
              AND split_part(s.sequencename, '_', 1) <> ALL(:own)
            ORDER BY s.sequencename
            """
        ),
    }


async def _main(url: str) -> dict:
    from sqlalchemy import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.db import session

    async_url, args = session.async_url(make_url(url), 5, 0)
    engine = create_async_engine(async_url, poolclass=NullPool, connect_args=args)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(snapshot)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    import asyncio

    print(json.dumps(asyncio.run(_main(sys.argv[1])), indent=1))
