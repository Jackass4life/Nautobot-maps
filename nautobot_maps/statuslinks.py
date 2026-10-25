"""Each tenant's status page link (#500): ``/status/<id>``, a random id the
app makes, not the tenant's name, so another customer's page can't be found
by guessing a name.  The id is made the first time staff need the link (the
Availability page) and kept; ``python -m nautobot_maps status-link TENANT
--new`` replaces one that leaked, and the old link then answers 404.

The name is kept case-folded, as the status page matches tenants (#291).
"""

import re
import secrets

# 24 URL-safe characters, 144 random bits.
ID_BYTES = 18
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{24}$")


def _key(tenant: str) -> str:
    return tenant.strip().casefold()


def new_id() -> str:
    return secrets.token_urlsafe(ID_BYTES)


def ensure(conn, tenants: list[str]) -> dict[str, str]:
    """Each tenant's link id, made for those without one: two queries for
    any number of tenants."""
    keys = sorted({_key(tenant) for tenant in tenants if tenant and tenant.strip()})
    if not keys:
        return {}
    conn.execute(
        """
        INSERT INTO status_links (tenant_key, link_id)
        SELECT key, id FROM unnest(%s::text[], %s::text[]) AS t(key, id)
        ON CONFLICT (tenant_key) DO NOTHING
        """,
        (keys, [new_id() for _ in keys]),
    )
    rows = conn.execute("SELECT tenant_key, link_id FROM status_links WHERE tenant_key = ANY(%s)", (keys,)).fetchall()
    ids = {row["tenant_key"]: row["link_id"] for row in rows}
    return {tenant: ids[_key(tenant)] for tenant in tenants if tenant and _key(tenant) in ids}


def tenant_for(conn, link_id: str) -> str | None:
    """The (case-folded) tenant a link id opens; None for anything else, a
    tenant's name included."""
    if not ID_PATTERN.match(link_id or ""):
        return None
    row = conn.execute("SELECT tenant_key FROM status_links WHERE link_id = %s", (link_id,)).fetchone()
    return row["tenant_key"] if row else None


def replace(conn, tenant: str) -> str:
    """A new link id for *tenant*; the old one stops working at once."""
    link_id = new_id()
    conn.execute(
        """
        INSERT INTO status_links (tenant_key, link_id) VALUES (%s, %s)
        ON CONFLICT (tenant_key) DO UPDATE SET link_id = EXCLUDED.link_id, created_at = now()
        """,
        (_key(tenant), link_id),
    )
    return link_id
