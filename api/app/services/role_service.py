"""Roles from the database (#713; the example's services/roles.py): each role and its
scopes, read into ``permissions.ROLES``, which every check uses, so a request needs no
query. Read when the app starts and again when the copy is older than
``CACHE_SECONDS``, so every worker sees a change within that time. A database that
can't be read keeps the last copy; one without roles gives the built-in defaults.

The changes (#721, the admin pages and ``role ...``) run in the caller's transaction;
``admin_kept`` refuses one that would leave no way to administer the app."""

import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger

from app.auth import permissions
from app.auth import scopes as scope_rules
from app.db import aconnection
from app.services import identity_service as identities

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


# --- Changes (#721): the admin pages' and the CLI's -----------------------------------

ROLE_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
MAX_ROLE_NAME = 64
# A token's role, not one of these: its access is the status page's own.
RESERVED = frozenset({"status"})


class RoleError(identities.IdentityError):
    """A role change that can't be made (an IdentityError, so one handler takes both)."""


async def list_all(conn: aconnection.Connection) -> list[dict]:
    """Every role with its scopes and how many identities have it, built-in first."""
    rows = (
        await conn.execute(
            "SELECT r.id, r.name, r.label, r.landing, r.description, r.built_in, "
            "(SELECT count(*) FROM identities_to_roles ir WHERE ir.role_id = r.id) AS identities "
            "FROM roles r ORDER BY r.built_in DESC, r.name"
        )
    ).fetchall()
    held = (
        await conn.execute(
            "SELECT role_id, scope FROM permissions WHERE role_id IS NOT NULL ORDER BY scope"
        )
    ).fetchall()
    found = []
    for row in rows:
        found.append(
            {
                "name": row["name"],
                "label": row["label"] or row["name"],
                "landing": row["landing"] or "/",
                "description": row["description"] or "",
                "built_in": bool(row["built_in"]),
                "scopes": [each["scope"] for each in held if each["role_id"] == row["id"]],
                "identities": int(row["identities"]),
            }
        )
    rank = {role: index for index, role in enumerate(identities.ROLE_PRIORITY)}
    return sorted(found, key=lambda role: (rank.get(role["name"], len(rank)), role["name"]))


async def read_one(conn: aconnection.Connection, name: str) -> dict:
    name = (name or "").strip().lower()
    for role in await list_all(conn):
        if role["name"] == name:
            return role
    raise RoleError(f"no role {name!r}")


async def _role(conn: aconnection.Connection, name: str) -> dict:
    row = (
        await conn.execute(
            "SELECT id, built_in FROM roles WHERE name = %s", ((name or "").strip().lower(),)
        )
    ).fetchone()
    if row is None:
        raise RoleError(f"no role {(name or '').strip().lower()!r}")
    return row


def _landing(landing: str) -> str:
    landing = (landing or "/").strip()
    # A page of this app: a path, never another site's (//host) or a scheme.
    if not landing.startswith("/") or landing.startswith("//") or any(c.isspace() for c in landing):
        raise RoleError(f"landing {landing!r}: a page of this app, e.g. / or /noc")
    return landing


def _text(value: str, what: str) -> str:
    value = (value or "").strip()
    if len(value) > identities.MAX_NAME_LENGTH:
        raise RoleError(f"{what}: up to {identities.MAX_NAME_LENGTH} characters")
    return value


async def create(
    conn: aconnection.Connection,
    name: str,
    label: str = "",
    description: str = "",
    landing: str = "/",
    scopes: list[str] = (),  # type: ignore[assignment]
) -> dict:
    name = (name or "").strip().lower()
    if not ROLE_NAME.match(name) or len(name) > MAX_ROLE_NAME:
        raise RoleError(f"name: a-z first, then a-z, 0-9, _ or -, up to {MAX_ROLE_NAME} characters")
    if name in RESERVED:
        raise RoleError(f"{name!r} is a token's role, not one to make")
    # session:basic always (the top bar, the help pages: what every signed-in role
    # reads); it can be taken away after.
    checked = sorted(
        {identities.check_scope(scope) for scope in scopes}
        | {str(permissions.Permission.SESSION_BASIC)}
    )
    row = (
        await conn.execute(
            "INSERT INTO roles (name, label, landing, description, built_in) "
            "VALUES (%s, %s, %s, %s, false) ON CONFLICT (name) DO NOTHING RETURNING id",
            (name, _text(label, "label"), _landing(landing), _text(description, "description")),
        )
    ).fetchone()
    if row is None:
        raise RoleError(f"role {name!r} exists already")
    for scope in checked:
        await conn.execute(
            "INSERT INTO permissions (scope, role_id) VALUES (%s, %s)", (scope, row["id"])
        )
    logger.info("Role {!r} created with scopes {}", name, ", ".join(checked) or "none")
    return await read_one(conn, name)


async def update(
    conn: aconnection.Connection,
    name: str,
    label: str | None = None,
    description: str | None = None,
    landing: str | None = None,
) -> None:
    """The fields given change; the others stay."""
    role = await _role(conn, name)
    await conn.execute(
        "UPDATE roles SET label = COALESCE(%s, label), description = COALESCE(%s, description), "
        "landing = COALESCE(%s, landing), updated_at = now() WHERE id = %s",
        (
            None if label is None else _text(label, "label"),
            None if description is None else _text(description, "description"),
            None if landing is None else _landing(landing),
            role["id"],
        ),
    )


async def delete(conn: aconnection.Connection, name: str) -> int:
    """The role goes, and the identities that had it lose it: how many had it."""
    role = await _role(conn, name)
    if role["built_in"]:
        raise RoleError(f"{name.strip().lower()!r} is built in: edit its scopes instead")
    had = (
        await conn.execute(
            "SELECT count(*) AS n FROM identities_to_roles WHERE role_id = %s", (role["id"],)
        )
    ).fetchone()["n"]
    await conn.execute("DELETE FROM roles WHERE id = %s", (role["id"],))
    logger.info("Role {!r} deleted ({} identities had it)", name.strip().lower(), had)
    return int(had)


async def add_scope(conn: aconnection.Connection, name: str, scope: str) -> None:
    role = await _role(conn, name)
    scope = identities.check_scope(scope)
    # The unique constraint doesn't hold with identity_id NULL (NULLs differ).
    await conn.execute(
        "INSERT INTO permissions (scope, role_id) SELECT %s, %s WHERE NOT EXISTS "
        "(SELECT 1 FROM permissions WHERE scope = %s AND role_id = %s)",
        (scope, role["id"], scope, role["id"]),
    )
    await conn.execute("UPDATE roles SET updated_at = now() WHERE id = %s", (role["id"],))


async def remove_scope(conn: aconnection.Connection, name: str, scope: str) -> None:
    role = await _role(conn, name)
    await conn.execute(
        "DELETE FROM permissions WHERE role_id = %s AND scope = %s",
        (role["id"], identities.check_scope(scope)),
    )
    await conn.execute("UPDATE roles SET updated_at = now() WHERE id = %s", (role["id"],))


# --- Never lock the admins out --------------------------------------------------------


async def admin_holders(conn: aconnection.Connection) -> tuple[int, int]:
    """How many roles, and how many identities, hold the admin permission."""
    roles = await load(conn)
    admin_roles = {
        name
        for name, role in roles.items()
        if scope_rules.is_scope_covered(permissions.Permission.ADMIN, role.scopes)
    }
    rows = (
        await conn.execute(
            "SELECT ir.identity_id AS id, r.name AS role, NULL AS scope "
            "FROM identities_to_roles ir JOIN roles r ON r.id = ir.role_id UNION ALL "
            "SELECT identity_id, NULL, scope FROM permissions WHERE identity_id IS NOT NULL"
        )
    ).fetchall()
    held: dict[int, set[str]] = {}
    for row in rows:
        scopes = held.setdefault(row["id"], set())
        if row["role"] in admin_roles:
            scopes.add(str(permissions.Permission.ADMIN))
        elif row["scope"]:
            scopes.add(row["scope"])
    admins = sum(
        1
        for scopes in held.values()
        if scope_rules.is_scope_covered(permissions.Permission.ADMIN, scopes)
    )
    return len(admin_roles), admins


@asynccontextmanager
async def admin_kept(conn: aconnection.Connection) -> AsyncIterator[None]:
    """Around a change in the caller's transaction: refused (RoleError, which rolls it
    back) when afterwards no role holds the admin permission, or when identities held
    it before and none does after. Before anyone has an identity, the groups give admin
    (AUTH_GROUP_ROLES), so no identity needs it then."""
    _, people_before = await admin_holders(conn)
    yield
    roles_after, people_after = await admin_holders(conn)
    if not roles_after:
        raise RoleError("refused: no role would hold the admin permission any more")
    if people_before and not people_after:
        raise RoleError(
            "refused: nobody would be an admin any more (make someone else an admin first)"
        )


def changed() -> None:
    """After a committed change: this worker reads roles and identities again on its
    next request (the others within 30 s)."""
    forget()
    identities.forget()
