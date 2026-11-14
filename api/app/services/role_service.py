"""Roles from the database (#713; the example's services/roles.py): each role and its
scopes, read into ``permissions.ROLES``, which every check uses, so a request needs no
query. Read when the app starts and again when the copy is older than
``CACHE_SECONDS``, so every worker sees a change within that time. A database that
can't be read keeps the last copy; one without roles gives the built-in defaults."""

import time

from loguru import logger

from app.auth import permissions
from app.db import aconnection

CACHE_SECONDS = 30.0
_read_at: float | None = None


async def load(conn: aconnection.Connection) -> dict[str, permissions.RoleDef]:
    """Every role in the database with its scopes, by name."""
    rows = (
        await conn.execute(
            "SELECT r.name, r.label, r.landing, r.description, p.scope FROM roles r "
            "LEFT JOIN permissions p ON p.role_id = r.id ORDER BY r.id, p.scope"
        )
    ).fetchall()
    found: dict[str, tuple[dict, set[str]]] = {}
    for row in rows:
        _, held = found.setdefault(row["name"], (dict(row), set()))
        if row["scope"]:
            held.add(row["scope"])
    return {
        name: permissions.RoleDef(
            row["label"] or name, row["landing"] or "/", frozenset(held), row["description"]
        )
        for name, (row, held) in found.items()
    }


async def refresh(force: bool = False) -> None:
    """Read the roles again when the copy is older than CACHE_SECONDS (or *force*)."""
    global _read_at
    now = time.monotonic()
    if not force and _read_at is not None and now - _read_at < CACHE_SECONDS:
        return
    # Before the read: requests meanwhile don't start another.
    _read_at = now
    try:
        async with aconnection.connect() as conn:
            found = await load(conn)
    except Exception as exc:  # noqa: BLE001 - any failure: the last roles stand
        logger.warning("Roles could not be read, the last ones kept: {}", type(exc).__name__)
        return
    if not found:
        logger.warning("No roles in the database: the built-in ones are used")
        found = dict(permissions.DEFAULT_ROLES)
    permissions.ROLES.clear()
    permissions.ROLES.update(found)


def forget() -> None:
    """The next ``refresh`` reads the database (after a change, and in tests)."""
    global _read_at
    _read_at = None


async def seed_defaults(conn: aconnection.Connection) -> int:
    """The built-in roles, when the table has none (tests empty it): how many."""
    if (await conn.execute("SELECT 1 FROM roles LIMIT 1")).fetchone():
        return 0
    for name, role in permissions.DEFAULT_ROLES.items():
        row = (
            await conn.execute(
                "INSERT INTO roles (name, label, landing, description, built_in) "
                "VALUES (%s, %s, %s, %s, true) RETURNING id",
                (name, role.label, role.landing, role.description),
            )
        ).fetchone()
        for scope in sorted(role.scopes):
            await conn.execute(
                "INSERT INTO permissions (scope, role_id) VALUES (%s, %s)", (str(scope), row["id"])
            )
    return len(permissions.DEFAULT_ROLES)
