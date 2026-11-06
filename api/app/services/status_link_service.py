"""Each tenant's status page link (#500, #592): ``/status/<id>``, a random id,
not the tenant's name, so another customer's page can't be found by guessing.
Made the first time staff need the link and kept; replacing one makes the old
link answer 404. The name is kept case-folded, as the status page matches
tenants (#291). The current app's nautobot_maps/statuslinks.py.
"""

import re
import secrets

from sqlalchemy.orm import Session

from app.repositories import status_link_repository

# 24 URL-safe characters, 144 random bits.
ID_BYTES = 18
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{24}$")


def _key(tenant: str) -> str:
    return tenant.strip().casefold()


def new_id() -> str:
    return secrets.token_urlsafe(ID_BYTES)


def ensure(session: Session, tenants: list[str]) -> dict[str, str]:
    """Each tenant's link id, made for those without one: one read, and a write
    only when some tenant is new."""
    keys = sorted({_key(tenant) for tenant in tenants if tenant and tenant.strip()})
    if not keys:
        return {}
    ids = status_link_repository.read(session, keys)
    missing = [key for key in keys if key not in ids]
    if missing:
        status_link_repository.insert_missing(session, missing, [new_id() for _ in missing])
        # Another request may have made some meanwhile: theirs are kept.
        ids |= status_link_repository.read(session, missing)
    return {tenant: ids[_key(tenant)] for tenant in tenants if tenant and _key(tenant) in ids}


def tenant_for(session: Session, link_id: str) -> str | None:
    """The (case-folded) tenant a link id opens; None for anything else."""
    return status_link_repository.tenant_for(session, link_id)


def known(session: Session, tenant: str) -> bool:
    """Whether *tenant* is one Nautobot has (the last sync) or has a link (#528)."""
    return status_link_repository.known(session, _key(tenant), tenant.strip())


def replace(session: Session, tenant: str) -> str:
    """A new link id for *tenant*; the old one stops working at once."""
    link_id = new_id()
    status_link_repository.replace(session, _key(tenant), link_id)
    return link_id


def unknown_tenant(tenant: str) -> str:
    """The refusal for a tenant Nautobot doesn't have (#528), the same everywhere."""
    return f"no tenant {tenant.strip()!r} in Nautobot (as of the last sync)"


def nautobot_name(session: Session, tenant: str) -> str | None:
    """*tenant* as Nautobot spells it, or None when Nautobot has no such tenant (#528)."""
    return status_link_repository.nautobot_tenant_name(session, tenant.strip())
