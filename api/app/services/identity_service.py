"""Identities (#715; the example's services/identities.py): a person the app knows by
the user name a sign-in gives (the proxy's ``X-Forwarded-User`` today), with roles and
scopes of their own. A sign-in is looked up here, at most every ``CACHE_SECONDS`` per
name, so a request needs no query; a change reaches every worker within that time."""

import re
import time
from dataclasses import dataclass, field

from loguru import logger

from app.auth import permissions
from app.db import aconnection

CACHE_SECONDS = 30.0
# A role's rank for the label and start page of someone with several: these first,
# then any other role by name.
ROLE_PRIORITY = ("admin", "operator", "noc", "customer", "viewer")
SCOPE_PATTERN = re.compile(r"^[a-z0-9_*-]+(:[a-z0-9_*-]+)*$")
MAX_NAME_LENGTH = 255


class IdentityError(ValueError):
    """A change that can't be made; ``message`` says why (safe to show)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Identity:
    unique_name: str
    common_name: str = ""
    description: str = ""
    roles: tuple[str, ...] = ()
    scopes: frozenset[str] = field(default_factory=frozenset)


_cache: dict[str, tuple[float, Identity | None]] = {}
_unknown_logged: set[str] = set()


def normal_name(name: str) -> str:
    return (name or "").strip().lower()


def by_priority(roles: list[str]) -> tuple[str, ...]:
    """*roles* with the highest first: ROLE_PRIORITY's order, then by name."""
    rank = {role: index for index, role in enumerate(ROLE_PRIORITY)}
    return tuple(sorted(set(roles), key=lambda role: (rank.get(role, len(rank)), role)))


async def read(conn: aconnection.Connection, unique_name: str) -> Identity | None:
    """One identity with its roles and own scopes, or None."""
    row = (
        await conn.execute(
            "SELECT id, unique_name, common_name, description FROM identities "
            "WHERE unique_name = %s",
            (normal_name(unique_name),),
        )
    ).fetchone()
    if row is None:
        return None
    roles = (
        await conn.execute(
            "SELECT r.name FROM identities_to_roles ir JOIN roles r ON r.id = ir.role_id "
            "WHERE ir.identity_id = %s",
            (row["id"],),
        )
    ).fetchall()
    scopes = (
        await conn.execute("SELECT scope FROM permissions WHERE identity_id = %s", (row["id"],))
    ).fetchall()
    return Identity(
        unique_name=row["unique_name"],
        common_name=row["common_name"] or "",
        description=row["description"] or "",
        roles=by_priority([role["name"] for role in roles]),
        scopes=frozenset(scope["scope"] for scope in scopes),
    )


async def lookup(unique_name: str) -> Identity | None:
    """*unique_name*'s identity as last read (at most CACHE_SECONDS ago), or None. A
    database that can't be read: None, logged (the caller decides what that means)."""
    name = normal_name(unique_name)
    if not name:
        return None
    now = time.monotonic()
    cached = _cache.get(name)
    if cached is not None and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        async with aconnection.connect() as conn:
            found = await read(conn, name)
    except Exception as exc:  # noqa: BLE001 - a failed read: no identity this time
        logger.warning("Identity {!r} could not be read: {}", name, type(exc).__name__)
        return cached[1] if cached is not None else None
    _cache[name] = (now, found)
    return found


def note_unknown(unique_name: str, fallback: str) -> None:
    """Once per name and process: someone signed in without an identity (#715), so
    the admin sees who still needs one."""
    name = normal_name(unique_name)
    if name and name not in _unknown_logged:
        _unknown_logged.add(name)
        logger.info("Signed in without an identity: {!r} ({})", name, fallback)


def forget() -> None:
    """Every name is read again (after a change, and in tests)."""
    _cache.clear()
    _unknown_logged.clear()


# --- Changes (the CLI's and the admin pages', #721) ----------------------------------


def check_scope(scope: str) -> str:
    scope = (scope or "").strip().lower()
    if not SCOPE_PATTERN.match(scope) or len(scope) > MAX_NAME_LENGTH:
        raise IdentityError(
            f"scope {scope!r}: parts of a-z, 0-9, _ - or *, separated by ':' (e.g. alerts:ack)"
        )
    return scope


async def _identity_id(conn: aconnection.Connection, unique_name: str) -> int:
    row = (
        await conn.execute(
            "SELECT id FROM identities WHERE unique_name = %s", (normal_name(unique_name),)
        )
    ).fetchone()
    if row is None:
        raise IdentityError(f"no identity {normal_name(unique_name)!r}")
    return row["id"]


async def _role_id(conn: aconnection.Connection, role: str) -> int:
    row = (
        await conn.execute("SELECT id FROM roles WHERE name = %s", (role.strip().lower(),))
    ).fetchone()
    if row is None:
        names = ", ".join(sorted(permissions.ROLES))
        raise IdentityError(f"no role {role!r} (the roles: {names})")
    return row["id"]


async def create(
    conn: aconnection.Connection,
    unique_name: str,
    roles: list[str] = (),  # type: ignore[assignment]
    scopes: list[str] = (),  # type: ignore[assignment]
    common_name: str = "",
    description: str = "",
) -> Identity:
    name = normal_name(unique_name)
    if not name or len(name) > MAX_NAME_LENGTH or any(char.isspace() for char in name):
        raise IdentityError(
            f"name: the user name a sign-in gives, up to {MAX_NAME_LENGTH} characters, no spaces"
        )
    checked = [check_scope(scope) for scope in scopes]
    role_ids = [await _role_id(conn, role) for role in roles]
    row = (
        await conn.execute(
            "INSERT INTO identities (unique_name, common_name, description) VALUES (%s, %s, %s) "
            "ON CONFLICT (unique_name) DO NOTHING RETURNING id",
            (name, common_name.strip() or None, description.strip() or None),
        )
    ).fetchone()
    if row is None:
        raise IdentityError(f"identity {name!r} exists already")
    for role_id in sorted(set(role_ids)):
        await conn.execute(
            "INSERT INTO identities_to_roles (identity_id, role_id) VALUES (%s, %s)",
            (row["id"], role_id),
        )
    for scope in sorted(set(checked)):
        await conn.execute(
            "INSERT INTO permissions (scope, identity_id) VALUES (%s, %s)", (scope, row["id"])
        )
    logger.info("Identity {!r} created with roles {}", name, ", ".join(roles) or "none")
    return await read(conn, name)  # type: ignore[return-value]


async def list_all(conn: aconnection.Connection) -> list[Identity]:
    rows = (
        await conn.execute("SELECT unique_name FROM identities ORDER BY unique_name")
    ).fetchall()
    return [found for row in rows if (found := await read(conn, row["unique_name"]))]


async def key_counts(conn: aconnection.Connection) -> dict[str, int]:
    """Each identity's working keys (not revoked, not expired), by name (#721)."""
    rows = (
        await conn.execute(
            "SELECT i.unique_name, count(*) AS n FROM api_tokens t "
            "JOIN identities i ON i.id = t.identity_id WHERE t.revoked_at IS NULL "
            "AND (t.expires_at IS NULL OR t.expires_at > now()) GROUP BY i.unique_name"
        )
    ).fetchall()
    return {row["unique_name"]: int(row["n"]) for row in rows}


async def delete(conn: aconnection.Connection, unique_name: str) -> None:
    await conn.execute(
        "DELETE FROM identities WHERE id = %s", (await _identity_id(conn, unique_name),)
    )
    logger.info("Identity {!r} deleted", normal_name(unique_name))


async def add_role(conn: aconnection.Connection, unique_name: str, role: str) -> None:
    await conn.execute(
        "INSERT INTO identities_to_roles (identity_id, role_id) VALUES (%s, %s) "
        "ON CONFLICT DO NOTHING",
        (await _identity_id(conn, unique_name), await _role_id(conn, role)),
    )


async def remove_role(conn: aconnection.Connection, unique_name: str, role: str) -> None:
    await conn.execute(
        "DELETE FROM identities_to_roles WHERE identity_id = %s AND role_id = %s",
        (await _identity_id(conn, unique_name), await _role_id(conn, role)),
    )


async def add_scope(conn: aconnection.Connection, unique_name: str, scope: str) -> None:
    # The unique constraint doesn't hold with role_id NULL (NULLs differ): checked here.
    identity_id = await _identity_id(conn, unique_name)
    scope = check_scope(scope)
    await conn.execute(
        "INSERT INTO permissions (scope, identity_id) SELECT %s, %s WHERE NOT EXISTS "
        "(SELECT 1 FROM permissions WHERE scope = %s AND identity_id = %s)",
        (scope, identity_id, scope, identity_id),
    )


async def remove_scope(conn: aconnection.Connection, unique_name: str, scope: str) -> None:
    await conn.execute(
        "DELETE FROM permissions WHERE identity_id = %s AND scope = %s",
        (await _identity_id(conn, unique_name), check_scope(scope)),
    )
