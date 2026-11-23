"""Create or drop a CI database (#765): ``pg.py create|drop SERVER_URL NAME``.

SERVER_URL is ``postgresql://user:password@host:port`` without a database; the
statements run on its ``postgres`` database.  Run with api/'s environment (asyncpg).
"""

import asyncio
import re
import sys

import asyncpg


async def main(action: str, server: str, name: str) -> None:
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise SystemExit(f"Not a database name for CI: {name!r}")
    conn = await asyncpg.connect(f"{server}/postgres")
    try:
        if action == "create":
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{name}"')
        else:
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("create", "drop"):
        raise SystemExit(__doc__)
    asyncio.run(main(*sys.argv[1:]))
