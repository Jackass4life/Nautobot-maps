"""Tenant status page links in ``status_links`` (#592), and Nautobot's tenant
names from the inventory snapshot: the current app's SQL (statuslinks.py).

Each function takes an `AsyncSession` (#643): the request's, or one of the caller's own.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def _rows(db: AsyncSession, sql: str, params: dict) -> list[dict]:
    result = await db.execute(text(sql), params)
    return [dict(row) for row in result.mappings()] if result.returns_rows else []


async def read(db: AsyncSession, keys: list[str]) -> dict[str, str]:
    rows = await _rows(
        db,
        "SELECT tenant_key, link_id FROM status_links WHERE tenant_key = ANY(:keys)",
        {"keys": keys},
    )
    return {row["tenant_key"]: row["link_id"] for row in rows}


async def insert_missing(db: AsyncSession, keys: list[str], ids: list[str]) -> None:
    """Links for these tenants; one another request made meanwhile is kept."""
    await _rows(
        db,
        "INSERT INTO status_links (tenant_key, link_id) "
        "SELECT key, id FROM unnest(CAST(:keys AS text[]), CAST(:ids AS text[])) AS t(key, id) "
        "ON CONFLICT (tenant_key) DO NOTHING",
        {"keys": keys, "ids": ids},
    )


async def tenant_for(db: AsyncSession, link_id: str) -> str | None:
    rows = await _rows(
        db, "SELECT tenant_key FROM status_links WHERE link_id = :id", {"id": link_id}
    )
    return rows[0]["tenant_key"] if rows else None


async def known(db: AsyncSession, key: str, tenant: str) -> bool:
    rows = await _rows(
        db,
        "SELECT 1 AS found FROM status_links WHERE tenant_key = :key "
        "UNION ALL SELECT 1 FROM nautobot_tenant_cache "
        "WHERE lower(name) = lower(:tenant) LIMIT 1",
        {"key": key, "tenant": tenant},
    )
    return bool(rows)


async def replace(db: AsyncSession, key: str, link_id: str) -> None:
    await _rows(
        db,
        "INSERT INTO status_links (tenant_key, link_id) VALUES (:key, :id) "
        "ON CONFLICT (tenant_key) DO UPDATE SET link_id = EXCLUDED.link_id, created_at = now()",
        {"key": key, "id": link_id},
    )


async def nautobot_tenant_name(db: AsyncSession, tenant: str) -> str | None:
    """The tenant as Nautobot spells it (the last sync; capitals don't matter)."""
    rows = await _rows(
        db,
        "SELECT name FROM nautobot_tenant_cache WHERE lower(name) = lower(:tenant) "
        "ORDER BY name LIMIT 1",
        {"tenant": tenant},
    )
    return rows[0]["name"] if rows else None


async def listing(db: AsyncSession) -> list[dict]:
    """Every link (#687): its tenant as Nautobot spells it when it still has it (else the
    case-folded name the link is kept under), its id and when it was made."""
    return await _rows(
        db,
        "SELECT l.tenant_key, l.link_id, l.created_at, "
        "(SELECT t.name FROM nautobot_tenant_cache t WHERE lower(t.name) = l.tenant_key "
        "ORDER BY t.name LIMIT 1) AS name "
        "FROM status_links l ORDER BY l.tenant_key",
        {},
    )


async def delete(db: AsyncSession, key: str) -> bool:
    """Whether there was a link for *key*; its page answers 404 from now on."""
    rows = await _rows(
        db, "DELETE FROM status_links WHERE tenant_key = :key RETURNING link_id", {"key": key}
    )
    return bool(rows)
