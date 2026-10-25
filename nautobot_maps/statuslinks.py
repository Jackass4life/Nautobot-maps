"""Each tenant's status page link (#500): ``/status/<id>``, a random id the
app makes, not the tenant's name, so another customer's page can't be found
by guessing a name.  The id is made the first time staff need the link (the
Availability page, a status token) and kept; ``python -m nautobot_maps
status-link TENANT --new`` replaces one that leaked, and the old link then
answers 404.

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
    """Each tenant's link id, made for those without one: one read, and a
    write only when some tenant is new."""
    keys = sorted({_key(tenant) for tenant in tenants if tenant and tenant.strip()})
    if not keys:
        return {}
    ids = _read(conn, keys)
    missing = [key for key in keys if key not in ids]
    if missing:
        conn.execute(
            """
            INSERT INTO status_links (tenant_key, link_id)
            SELECT key, id FROM unnest(%s::text[], %s::text[]) AS t(key, id)
            ON CONFLICT (tenant_key) DO NOTHING
            """,
            (missing, [new_id() for _ in missing]),
        )
        # Another request may have made some meanwhile: theirs are kept.
        ids |= _read(conn, missing)
    return {tenant: ids[_key(tenant)] for tenant in tenants if tenant and _key(tenant) in ids}


def _read(conn, keys: list[str]) -> dict[str, str]:
    rows = conn.execute("SELECT tenant_key, link_id FROM status_links WHERE tenant_key = ANY(%s)", (keys,)).fetchall()
    return {row["tenant_key"]: row["link_id"] for row in rows}


def tenant_for(conn, link_id: str) -> str | None:
    """The (case-folded) tenant a link id opens; None for anything else, a
    tenant's name included."""
    row = conn.execute("SELECT tenant_key FROM status_links WHERE link_id = %s", (link_id,)).fetchone()
    return row["tenant_key"] if row else None


def known(conn, tenant: str) -> bool:
    """Whether *tenant* is one Nautobot has (the last sync) or has a link: so a
    typo doesn't make a link, or leave a leaked one working."""
    key = _key(tenant)
    row = conn.execute(
        "SELECT 1 FROM status_links WHERE tenant_key = %s "
        "UNION ALL SELECT 1 FROM nautobot_tenant_cache WHERE lower(name) = lower(%s) LIMIT 1",
        (key, tenant.strip()),
    ).fetchone()
    return row is not None


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
