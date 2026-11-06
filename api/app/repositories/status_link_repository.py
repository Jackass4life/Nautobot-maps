"""Tenant status page links in ``status_links`` (#592), and Nautobot's tenant
names from the inventory snapshot: the current app's SQL (statuslinks.py).

Each function takes a SQLAlchemy session or connection, or app/db.py's psycopg
connection (the routes ported from the current app use those, #604).
"""

import re

from sqlalchemy import text

_NAMED = re.compile(r"(?<!:):(\w+)")


def _rows(conn, sql: str, params: dict) -> list[dict]:  # type: ignore[no-untyped-def]
    if hasattr(conn, "exec_driver_sql") or hasattr(conn, "get_bind"):
        result = conn.execute(text(sql), params)
        return [dict(row) for row in result.mappings()] if result.returns_rows else []
    cursor = conn.execute(_NAMED.sub(r"%(\1)s", sql), params)
    return list(cursor.fetchall()) if cursor.description else []


def read(conn, keys: list[str]) -> dict[str, str]:  # type: ignore[no-untyped-def]
    rows = _rows(
        conn,
        "SELECT tenant_key, link_id FROM status_links WHERE tenant_key = ANY(:keys)",
        {"keys": keys},
    )
    return {row["tenant_key"]: row["link_id"] for row in rows}


def insert_missing(conn, keys: list[str], ids: list[str]) -> None:  # type: ignore[no-untyped-def]
    """Links for these tenants; one another request made meanwhile is kept."""
    _rows(
        conn,
        "INSERT INTO status_links (tenant_key, link_id) "
        "SELECT key, id FROM unnest(CAST(:keys AS text[]), CAST(:ids AS text[])) AS t(key, id) "
        "ON CONFLICT (tenant_key) DO NOTHING",
        {"keys": keys, "ids": ids},
    )


def tenant_for(conn, link_id: str) -> str | None:  # type: ignore[no-untyped-def]
    rows = _rows(conn, "SELECT tenant_key FROM status_links WHERE link_id = :id", {"id": link_id})
    return rows[0]["tenant_key"] if rows else None


def known(conn, key: str, tenant: str) -> bool:  # type: ignore[no-untyped-def]
    rows = _rows(
        conn,
        "SELECT 1 AS found FROM status_links WHERE tenant_key = :key "
        "UNION ALL SELECT 1 FROM nautobot_tenant_cache "
        "WHERE lower(name) = lower(:tenant) LIMIT 1",
        {"key": key, "tenant": tenant},
    )
    return bool(rows)


def replace(conn, key: str, link_id: str) -> None:  # type: ignore[no-untyped-def]
    _rows(
        conn,
        "INSERT INTO status_links (tenant_key, link_id) VALUES (:key, :id) "
        "ON CONFLICT (tenant_key) DO UPDATE SET link_id = EXCLUDED.link_id, created_at = now()",
        {"key": key, "id": link_id},
    )


def nautobot_tenant_name(conn, tenant: str) -> str | None:  # type: ignore[no-untyped-def]
    """The tenant as Nautobot spells it (the last sync; capitals don't matter)."""
    rows = _rows(
        conn,
        "SELECT name FROM nautobot_tenant_cache WHERE lower(name) = lower(:tenant) "
        "ORDER BY name LIMIT 1",
        {"tenant": tenant},
    )
    return rows[0]["name"] if rows else None
