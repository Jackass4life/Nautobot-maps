"""API tokens in ``api_tokens`` (#592): the current app's SQL (nautobot_maps/tokens.py).

Each function takes an `AsyncSession` (#643): the request's, or one of the caller's own.
"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils import timeutil

COLUMNS = (
    "id, name, prefix, role, tenant, created_by, created_at, expires_at, last_used_at, "
    "revoked_at, revoked_by, notify_email, notify_after_minutes, "
    # The identity a key belongs to (#719), by name.
    "(SELECT unique_name FROM identities WHERE id = api_tokens.identity_id) AS identity"
)
# When it stopped working: revoked or expired, whichever came first.
INACTIVE = (
    "(revoked_at IS NOT NULL OR expires_at <= :now) AND LEAST(revoked_at, expires_at) <= :cutoff"
)


async def _rows(db: AsyncSession, sql: str, params: dict) -> list[dict]:
    result = await db.execute(text(sql), params)
    return [dict(row) for row in result.mappings()] if result.returns_rows else []


def _first(rows: list[dict]) -> dict | None:
    return timeutil.row(rows[0]) if rows else None


async def insert(db: AsyncSession, values: dict) -> dict | None:
    """The new token's row; None when the name is taken (the unique name is the check)."""
    return _first(
        await _rows(
            db,
            "INSERT INTO api_tokens (name, token_hash, prefix, role, tenant, created_by, "
            "created_at, expires_at, notify_email, notify_after_minutes, identity_id) "
            "VALUES (:name, :token_hash, :prefix, :role, :tenant, :created_by, :created_at, "
            ":expires_at, :notify_email, :notify_after_minutes, :identity_id) "
            "ON CONFLICT (name) DO NOTHING "
            f"RETURNING {COLUMNS}",
            values,
        )
    )


async def list_all(db: AsyncSession, tenant: str | None = None) -> list[dict]:
    where, params = "", {}
    if tenant:
        where, params = "WHERE lower(tenant) = lower(:tenant)", {"tenant": tenant}
    rows = await _rows(
        db, f"SELECT {COLUMNS} FROM api_tokens {where} ORDER BY created_at DESC, id DESC", params
    )
    return [timeutil.row(row) for row in rows]


async def inactive(db: AsyncSession, now: datetime, cutoff: datetime, delete: bool) -> list[dict]:
    """Revoked or expired before *cutoff*; deleted when *delete*."""
    if delete:
        sql = f"DELETE FROM api_tokens WHERE {INACTIVE} RETURNING {COLUMNS}"
    else:
        sql = f"SELECT {COLUMNS} FROM api_tokens WHERE {INACTIVE}"
    return [timeutil.row(row) for row in await _rows(db, sql, {"now": now, "cutoff": cutoff})]


async def revoke(db: AsyncSession, token_id: int, now: datetime, revoked_by: str) -> dict | None:
    """The row, revoked now; None when it doesn't exist or was revoked already."""
    return _first(
        await _rows(
            db,
            "UPDATE api_tokens SET revoked_at = :now, revoked_by = :by "
            f"WHERE id = :id AND revoked_at IS NULL RETURNING {COLUMNS}",
            {"now": now, "by": revoked_by, "id": token_id},
        )
    )


async def get(db: AsyncSession, token_id: int) -> dict | None:
    return _first(
        await _rows(db, f"SELECT {COLUMNS} FROM api_tokens WHERE id = :id", {"id": token_id})
    )


async def find_active(
    db: AsyncSession, token_hash: str, now: datetime, stale_before: datetime
) -> dict:
    """An active token by its hash, with ``stale``: last used before *stale_before*."""
    return (
        _first(
            await _rows(
                db,
                "SELECT id, name, role, tenant, "
                "(SELECT unique_name FROM identities WHERE id = api_tokens.identity_id) "
                "AS identity, (last_used_at IS NULL OR last_used_at < :stale) AS stale "
                "FROM api_tokens WHERE token_hash = :hash AND revoked_at IS NULL "
                "AND (expires_at IS NULL OR expires_at > :now)",
                {"stale": stale_before, "hash": token_hash, "now": now},
            )
        )
        or {}
    )


async def touch(db: AsyncSession, token_id: int, now: datetime, stale_before: datetime) -> None:
    """``last_used_at = now``, unless another request wrote it meanwhile (the same condition)."""
    await _rows(
        db,
        "UPDATE api_tokens SET last_used_at = :now "
        "WHERE id = :id AND (last_used_at IS NULL OR last_used_at < :stale)",
        {"now": now, "id": token_id, "stale": stale_before},
    )


async def identity_id(db: AsyncSession, unique_name: str) -> int | None:
    rows = await _rows(db, "SELECT id FROM identities WHERE unique_name = :n", {"n": unique_name})
    return rows[0]["id"] if rows else None


async def new_identity(db: AsyncSession, unique_name: str, role: str, description: str) -> int:
    """An identity for a key made the old way (``--role``, #719), with that role."""
    rows = await _rows(
        db,
        "INSERT INTO identities (unique_name, description) VALUES (:n, :d) RETURNING id",
        {"n": unique_name, "d": description},
    )
    await _rows(
        db,
        "INSERT INTO identities_to_roles (identity_id, role_id) "
        "SELECT :i, id FROM roles WHERE name = :r",
        {"i": rows[0]["id"], "r": role},
    )
    return rows[0]["id"]


async def delete_identity(db: AsyncSession, identity_id: int) -> None:
    await _rows(db, "DELETE FROM identities WHERE id = :i", {"i": identity_id})


async def name_taken(db: AsyncSession, name: str) -> bool:
    return bool(await _rows(db, "SELECT 1 FROM api_tokens WHERE name = :n", {"n": name}))


async def delete_keyless_identities(db: AsyncSession, description_start: str) -> None:
    """Identities made for keys (their description starts so) with no key left."""
    await _rows(
        db,
        "DELETE FROM identities i WHERE i.description LIKE :start "
        "AND NOT EXISTS (SELECT 1 FROM api_tokens t WHERE t.identity_id = i.id)",
        {"start": description_start + "%"},
    )
