"""API tokens in ``api_tokens`` (#592): the current app's SQL (nautobot_maps/tokens.py)."""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.utils import timeutil

COLUMNS = (
    "id, name, prefix, role, tenant, created_by, created_at, expires_at, last_used_at, "
    "revoked_at, revoked_by"
)
# When it stopped working: revoked or expired, whichever came first.
INACTIVE = (
    "(revoked_at IS NOT NULL OR expires_at <= :now) AND LEAST(revoked_at, expires_at) <= :cutoff"
)


def insert(session: Session, values: dict) -> dict | None:
    """The new token's row; None when the name is taken (the unique name is the check)."""
    row = (
        session.execute(
            text(
                "INSERT INTO api_tokens (name, token_hash, prefix, role, tenant, created_by, "
                "created_at, expires_at) VALUES (:name, :token_hash, :prefix, :role, :tenant, "
                ":created_by, :created_at, :expires_at) ON CONFLICT (name) DO NOTHING "
                f"RETURNING {COLUMNS}"
            ),
            values,
        )
        .mappings()
        .first()
    )
    return timeutil.row(row) if row else None


def list_all(session: Session, tenant: str | None = None) -> list[dict]:
    where, params = (
        ("WHERE lower(tenant) = lower(:tenant)", {"tenant": tenant}) if tenant else ("", {})
    )
    rows = session.execute(
        text(f"SELECT {COLUMNS} FROM api_tokens {where} ORDER BY created_at DESC, id DESC"), params
    ).mappings()
    return [timeutil.row(row) for row in rows]


def inactive(session: Session, now: datetime, cutoff: datetime, delete: bool) -> list[dict]:
    """Revoked or expired before *cutoff*; deleted when *delete*."""
    if delete:
        sql = f"DELETE FROM api_tokens WHERE {INACTIVE} RETURNING {COLUMNS}"
    else:
        sql = f"SELECT {COLUMNS} FROM api_tokens WHERE {INACTIVE}"
    rows = session.execute(text(sql), {"now": now, "cutoff": cutoff}).mappings()
    return [timeutil.row(row) for row in rows]


def revoke(session: Session, token_id: int, now: datetime, revoked_by: str) -> dict | None:
    """The row, revoked now; None when it doesn't exist or was revoked already."""
    row = (
        session.execute(
            text(
                "UPDATE api_tokens SET revoked_at = :now, revoked_by = :by "
                f"WHERE id = :id AND revoked_at IS NULL RETURNING {COLUMNS}"
            ),
            {"now": now, "by": revoked_by, "id": token_id},
        )
        .mappings()
        .first()
    )
    return timeutil.row(row) if row else None


def get(session: Session, token_id: int) -> dict | None:
    row = (
        session.execute(text(f"SELECT {COLUMNS} FROM api_tokens WHERE id = :id"), {"id": token_id})
        .mappings()
        .first()
    )
    return timeutil.row(row) if row else None


def find_active(session: Session, token_hash: str, now: datetime, stale_before: datetime) -> dict:
    """An active token by its hash, with ``stale``: last used before *stale_before*."""
    row = (
        session.execute(
            text(
                "SELECT id, name, role, tenant, "
                "(last_used_at IS NULL OR last_used_at < :stale) AS stale "
                "FROM api_tokens WHERE token_hash = :hash AND revoked_at IS NULL "
                "AND (expires_at IS NULL OR expires_at > :now)"
            ),
            {"stale": stale_before, "hash": token_hash, "now": now},
        )
        .mappings()
        .first()
    )
    return timeutil.row(row)


def touch(session: Session, token_id: int, now: datetime, stale_before: datetime) -> None:
    """``last_used_at = now``, unless another request wrote it meanwhile (the same condition)."""
    session.execute(
        text(
            "UPDATE api_tokens SET last_used_at = :now "
            "WHERE id = :id AND (last_used_at IS NULL OR last_used_at < :stale)"
        ),
        {"now": now, "id": token_id, "stale": stale_before},
    )
