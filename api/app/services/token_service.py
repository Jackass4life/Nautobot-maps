"""Named API tokens for scripts, LibreNMS and MCP clients (#297, #592).

A token is ``nmt_`` and 43 random characters, sent as ``Authorization: Bearer
<token>``. It is shown once, when it is created; the database keeps its SHA-256
(enough for a long random secret), its first characters to tell tokens apart,
a name, a role and an optional expiry. Revoking one takes effect on the next
request. The current app's nautobot_maps/tokens.py.
"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import token_repository
from app.services import status_link_service
from app.utils import timeutil

PREFIX = "nmt_"
SHOWN_CHARACTERS = 12  # "nmt_" and 8 more, in lists
# The roles an API token may have (#691: noc and customer too), besides ``status``.
ROLES = ("viewer", "operator", "admin", "noc", "customer")
# A tenant's status token (#291): opens only its tenant's status page.
STATUS_ROLE = "status"
MAX_TENANT_LENGTH = 200
MAX_NAME_LENGTH = 100
MAX_EXPIRY_DAYS = 3650
# last_used_at is written at most this often, not on every request.
LAST_USED_INTERVAL = timedelta(minutes=5)
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
# A status token's notification addresses (#707): a plain check, the mail server
# has the last word. One @, a dot inside the domain, none of these characters.
EMAIL_FORBIDDEN = frozenset(' \t\r\n,;<>"()[]\\')
MAX_NOTIFY_EMAILS = 10
MAX_EMAIL_LENGTH = 254


class TokenError(ValueError):
    """A request that can't become a token; ``message`` says why (safe to show)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def looks_like_email(address: str) -> bool:
    """An address such as name@example.com, checked without a regular expression
    (CodeQL on #708: no input can make it slow)."""
    local, at, domain = address.partition("@")
    return bool(
        at
        and local
        and "@" not in domain
        and ".." not in domain
        and "." in domain.strip(".")
        and not domain.startswith(".")
        and not domain.endswith(".")
        and not EMAIL_FORBIDDEN.intersection(address)
    )


TAKEN = (
    "A token with this name already exists (revoked and expired ones keep their name "
    "until purged; see token list --all)"
)
# How an identity made for a key starts its description (#719): a purge removes it
# with its last key.
FOR_A_KEY = "API token"


def normal_name(name: str) -> str:
    """A key's name as an identity's (#719): lowercase, spaces as dashes."""
    return "-".join(name.strip().lower().split())


def notify_emails(value: object) -> str:
    """A status token's notification addresses (#707), checked: "a@x.dk, b@y.dk", or
    "" for none. Comma-separated (semicolons too), duplicates dropped."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TokenError("notify_email must be text: addresses separated by commas")
    found: list[str] = []
    for part in re.split(r"[,;]", value):
        address = part.strip()
        if not address:
            continue
        if len(address) > MAX_EMAIL_LENGTH or not looks_like_email(address):
            raise TokenError(f"notify_email: {address[:60]!r} is not an email address")
        if address.lower() not in (known.lower() for known in found):
            found.append(address)
    if len(found) > MAX_NOTIFY_EMAILS:
        raise TokenError(f"notify_email: at most {MAX_NOTIFY_EMAILS} addresses")
    return ", ".join(found)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_token(value: object) -> bool:
    """Whether *value* is shaped like one of our tokens (other Bearer values are not ours)."""
    return isinstance(value, str) and value.startswith(PREFIX) and len(value) > len(PREFIX)


def state(token: dict, at: datetime) -> str:
    if token.get("revoked_at"):
        return "revoked"
    expires = timeutil.parse_iso_datetime(token["expires_at"]) if token.get("expires_at") else None
    if expires is not None and expires <= at:
        return "expired"
    return "active"


def _with_state(token: dict, at: datetime) -> dict:
    return {**token, "state": state(token, at)}


async def create(session: AsyncSession, body: dict, created_by: str) -> dict:
    """Validate *body* (``name``, ``role``, optional ``expires_in_days``; with role
    ``status``, the ``tenant`` it may see and optional ``notify_email``, #707) and
    store a new token. The result has
    ``token``, the secret: the only time it is available."""
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise TokenError("name is required")
    name = name.strip()
    if len(name) > MAX_NAME_LENGTH or not NAME_PATTERN.match(name):
        raise TokenError(
            f"name: up to {MAX_NAME_LENGTH} letters, digits, spaces, dots, dashes and "
            "underscores, starting with a letter or digit"
        )
    role = body.get("role")
    for_identity = body.get("identity")
    identity_id = None
    if for_identity is not None:
        # A key of an existing identity (#719): exactly its rights.
        if role not in (None, ""):
            raise TokenError("give a role or an identity, not both")
        if not isinstance(for_identity, str) or not for_identity.strip():
            raise TokenError("identity: the name of an identity")
        identity_id = await token_repository.identity_id(session, normal_name(for_identity))
        if identity_id is None:
            raise TokenError(f"no identity {normal_name(for_identity)!r}")
        role = ""
    elif role not in (*ROLES, STATUS_ROLE):
        raise TokenError(f"role must be one of: {', '.join((*ROLES, STATUS_ROLE))}")
    tenant = body.get("tenant")
    if role == STATUS_ROLE:
        if (
            not isinstance(tenant, str)
            or not tenant.strip()
            or len(tenant.strip()) > MAX_TENANT_LENGTH
        ):
            raise TokenError(
                f"tenant is required for role status (up to {MAX_TENANT_LENGTH} characters)"
            )
        # A typo would make a working-looking link that shows nothing (#528):
        # only a tenant Nautobot has, kept as Nautobot spells it.
        found = await status_link_service.nautobot_name(session, tenant)
        if found is None:
            raise TokenError(status_link_service.unknown_tenant(tenant))
        tenant = found
    elif tenant is not None:
        raise TokenError("tenant is only for role status")
    # Set once (#707): to change the addresses, revoke the token and make a new one.
    notify_email = notify_emails(body.get("notify_email"))
    if notify_email and role != STATUS_ROLE:
        raise TokenError("notify_email is only for role status")
    days = body.get("expires_in_days")
    current = timeutil.now()
    expires_at = None
    if days is not None:
        if (
            isinstance(days, bool)
            or not isinstance(days, int)
            or days <= 0
            or days > MAX_EXPIRY_DAYS
        ):
            raise TokenError(f"expires_in_days must be a whole number from 1 to {MAX_EXPIRY_DAYS}")
        expires_at = current + timedelta(days=days)

    if await token_repository.name_taken(session, name):
        raise TokenError(TAKEN)
    if role in ROLES:
        # The old way (#719): an identity of the key's own, with that role.
        own = normal_name(name)
        if await token_repository.identity_id(session, own) is not None:
            raise TokenError(
                f"identity {own!r} exists already: make its key with identity {own!r}, "
                "or choose another name"
            )
        identity_id = await token_repository.new_identity(session, own, role, f"API token {name!r}")
    token = PREFIX + secrets.token_urlsafe(32)
    row = await token_repository.insert(
        session,
        {
            "name": name,
            "token_hash": hash_token(token),
            "prefix": token[:SHOWN_CHARACTERS],
            "role": role,
            "tenant": tenant,
            "created_by": created_by,
            "created_at": current,
            "expires_at": expires_at,
            "notify_email": notify_email,
            "identity_id": identity_id,
        },
    )
    if row is None:
        if role in ROLES and identity_id is not None:
            # The name was taken: the identity made for it goes too.
            await token_repository.delete_identity(session, identity_id)
        raise TokenError(TAKEN)
    logger.info("API token {!r} created (role {}) by {}", name, role, created_by or "unknown")
    return {**_with_state(row, current), "token": token}


async def list_tokens(session: AsyncSession, tenant: str | None = None) -> list[dict]:
    """Every token (only *tenant*'s, capitals ignored, when given), newest first,
    without secrets."""
    current = timeutil.now()
    rows = await token_repository.list_all(session, tenant.strip() if tenant else None)
    return [_with_state(token, current) for token in rows]


async def purge(
    session: AsyncSession, purged_by: str, older_than_days: object = None, dry_run: bool = False
) -> list[dict]:
    """Delete revoked and expired tokens for good (#528), never an active one; with
    *older_than_days*, only those that stopped working longer ago. Their names are
    free again. Returns them (newest first); *dry_run* deletes nothing."""
    if older_than_days is not None and (
        isinstance(older_than_days, bool)
        or not isinstance(older_than_days, int)
        or not 0 <= older_than_days <= MAX_EXPIRY_DAYS
    ):
        raise TokenError(f"older_than_days must be a whole number from 0 to {MAX_EXPIRY_DAYS}")
    current = timeutil.now()
    cutoff = current - timedelta(days=older_than_days or 0)
    rows = await token_repository.inactive(session, current, cutoff, delete=not dry_run)
    purged = sorted(
        (_with_state(token, current) for token in rows),
        key=lambda token: (token["created_at"], token["id"]),
        reverse=True,
    )
    if not dry_run:
        # Their identities made for them, with no key left, go too (#719): the name is
        # free again. People's identities are never touched.
        await token_repository.delete_keyless_identities(session, FOR_A_KEY)
        for token in purged:
            logger.info("API token {!r} purged by {}", token["name"], purged_by or "unknown")
    return purged


async def get(session: AsyncSession, token_id: int) -> dict | None:
    """One token by id (never its secret), or None."""
    found = await token_repository.get(session, token_id)
    return None if found is None else _with_state(found, timeutil.now())


async def revoke(session: AsyncSession, token_id: int, revoked_by: str) -> dict | None:
    """Revoke a token now. None when it doesn't exist; revoking twice changes nothing."""
    current = timeutil.now()
    row = await token_repository.revoke(session, token_id, current, revoked_by)
    if row is None:
        row = await token_repository.get(session, token_id)
        if row is None:
            return None
    else:
        logger.info("API token {!r} revoked by {}", row["name"], revoked_by or "unknown")
    return _with_state(row, current)


async def verify(session: AsyncSession, token: str) -> dict | None:
    """``{"name", "role", "tenant", "identity"}`` of an active token, else None (unknown,
    revoked or expired). Writes ``last_used_at`` (at most every 5 minutes) and
    commits *session*: use one of its own, not the request's."""
    if not is_token(token):
        return None
    current = timeutil.now()
    stale_before = current - LAST_USED_INTERVAL
    found = await token_repository.find_active(session, hash_token(token), current, stale_before)
    if not found:
        return None
    if found["stale"]:
        # The same condition again: of requests that all saw it stale, one writes.
        await token_repository.touch(session, found["id"], current, stale_before)
        await session.commit()
    return {
        "name": found["name"],
        "role": found["role"],
        "tenant": found.get("tenant") or "",
        # The identity whose rights the key has (#719); None for a status token.
        "identity": found.get("identity"),
    }
