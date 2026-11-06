"""API tokens in ``api_tokens`` (#592): the current app's SQL (nautobot_maps/tokens.py).

Each function takes a SQLAlchemy session or connection, or app/db.py's psycopg
connection (the routes ported from the current app use those, #604).
"""

import re
from datetime import datetime

from sqlalchemy import text

from app.utils import timeutil

COLUMNS = (
    "id, name, prefix, role, tenant, created_by, created_at, expires_at, last_used_at, "
    "revoked_at, revoked_by"
)
# When it stopped working: revoked or expired, whichever came first.
INACTIVE = (
    "(revoked_at IS NOT NULL OR expires_at <= :now) AND LEAST(revoked_at, expires_at) <= :cutoff"
)
_NAMED = re.compile(r"(?<!:):(\w+)")


def _rows(conn, sql: str, params: dict) -> list[dict]:  # type: ignore[no-untyped-def]
    if hasattr(conn, "exec_driver_sql") or hasattr(conn, "get_bind"):
        result = conn.execute(text(sql), params)
        return [dict(row) for row in result.mappings()] if result.returns_rows else []
    cursor = conn.execute(_NAMED.sub(r"%(\1)s", sql), params)
    return list(cursor.fetchall()) if cursor.description else []


def _first(rows: list[dict]) -> dict | None:
    return timeutil.row(rows[0]) if rows else None


def insert(conn, values: dict) -> dict | None:  # type: ignore[no-untyped-def]
    """The new token's row; None when the name is taken (the unique name is the check)."""
    return _first(
        _rows(
            conn,
            "INSERT INTO api_tokens (name, token_hash, prefix, role, tenant, created_by, "
            "created_at, expires_at) VALUES (:name, :token_hash, :prefix, :role, :tenant, "
            ":created_by, :created_at, :expires_at) ON CONFLICT (name) DO NOTHING "
            f"RETURNING {COLUMNS}",
            values,
        )
    )


def list_all(conn, tenant: str | None = None) -> list[dict]:  # type: ignore[no-untyped-def]
    where, params = "", {}
    if tenant:
        where, params = "WHERE lower(tenant) = lower(:tenant)", {"tenant": tenant}
    rows = _rows(
        conn, f"SELECT {COLUMNS} FROM api_tokens {where} ORDER BY created_at DESC, id DESC", params
    )
    return [timeutil.row(row) for row in rows]


def inactive(conn, now: datetime, cutoff: datetime, delete: bool) -> list[dict]:  # type: ignore[no-untyped-def]
    """Revoked or expired before *cutoff*; deleted when *delete*."""
    if delete:
        sql = f"DELETE FROM api_tokens WHERE {INACTIVE} RETURNING {COLUMNS}"
    else:
        sql = f"SELECT {COLUMNS} FROM api_tokens WHERE {INACTIVE}"
    return [timeutil.row(row) for row in _rows(conn, sql, {"now": now, "cutoff": cutoff})]


def revoke(conn, token_id: int, now: datetime, revoked_by: str) -> dict | None:  # type: ignore[no-untyped-def]
    """The row, revoked now; None when it doesn't exist or was revoked already."""
    return _first(
        _rows(
            conn,
            "UPDATE api_tokens SET revoked_at = :now, revoked_by = :by "
            f"WHERE id = :id AND revoked_at IS NULL RETURNING {COLUMNS}",
            {"now": now, "by": revoked_by, "id": token_id},
        )
    )


def get(conn, token_id: int) -> dict | None:  # type: ignore[no-untyped-def]
    return _first(_rows(conn, f"SELECT {COLUMNS} FROM api_tokens WHERE id = :id", {"id": token_id}))


def find_active(conn, token_hash: str, now: datetime, stale_before: datetime) -> dict:  # type: ignore[no-untyped-def]
    """An active token by its hash, with ``stale``: last used before *stale_before*."""
    return (
        _first(
            _rows(
                conn,
                "SELECT id, name, role, tenant, "
                "(last_used_at IS NULL OR last_used_at < :stale) AS stale "
                "FROM api_tokens WHERE token_hash = :hash AND revoked_at IS NULL "
                "AND (expires_at IS NULL OR expires_at > :now)",
                {"stale": stale_before, "hash": token_hash, "now": now},
            )
        )
        or {}
    )


def touch(conn, token_id: int, now: datetime, stale_before: datetime) -> None:  # type: ignore[no-untyped-def]
    """``last_used_at = now``, unless another request wrote it meanwhile (the same condition)."""
    _rows(
        conn,
        "UPDATE api_tokens SET last_used_at = :now "
        "WHERE id = :id AND (last_used_at IS NULL OR last_used_at < :stale)",
        {"now": now, "id": token_id, "stale": stale_before},
    )
