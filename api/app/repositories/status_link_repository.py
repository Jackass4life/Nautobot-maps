"""Tenant status page links in ``status_links`` (#592), and Nautobot's tenant
names from the inventory snapshot: the current app's SQL (statuslinks.py)."""

from sqlalchemy import text
from sqlalchemy.orm import Session


def read(session: Session, keys: list[str]) -> dict[str, str]:
    rows = session.execute(
        text("SELECT tenant_key, link_id FROM status_links WHERE tenant_key = ANY(:keys)"),
        {"keys": keys},
    )
    return {row.tenant_key: row.link_id for row in rows}


def insert_missing(session: Session, keys: list[str], ids: list[str]) -> None:
    """Links for these tenants; one another request made meanwhile is kept."""
    session.execute(
        text(
            "INSERT INTO status_links (tenant_key, link_id) "
            "SELECT key, id FROM unnest(CAST(:keys AS text[]), CAST(:ids AS text[])) AS t(key, id) "
            "ON CONFLICT (tenant_key) DO NOTHING"
        ),
        {"keys": keys, "ids": ids},
    )


def tenant_for(session: Session, link_id: str) -> str | None:
    return session.execute(
        text("SELECT tenant_key FROM status_links WHERE link_id = :id"), {"id": link_id}
    ).scalar()


def known(session: Session, key: str, tenant: str) -> bool:
    row = session.execute(
        text(
            "SELECT 1 FROM status_links WHERE tenant_key = :key "
            "UNION ALL SELECT 1 FROM nautobot_tenant_cache "
            "WHERE lower(name) = lower(:tenant) LIMIT 1"
        ),
        {"key": key, "tenant": tenant},
    ).first()
    return row is not None


def replace(session: Session, key: str, link_id: str) -> None:
    session.execute(
        text(
            "INSERT INTO status_links (tenant_key, link_id) VALUES (:key, :id) "
            "ON CONFLICT (tenant_key) DO UPDATE SET link_id = EXCLUDED.link_id, created_at = now()"
        ),
        {"key": key, "id": link_id},
    )


def nautobot_tenant_name(session: Session, tenant: str) -> str | None:
    """The tenant as Nautobot spells it (the last sync; capitals don't matter)."""
    return session.execute(
        text(
            "SELECT name FROM nautobot_tenant_cache WHERE lower(name) = lower(:tenant) "
            "ORDER BY name LIMIT 1"
        ),
        {"tenant": tenant},
    ).scalar()
