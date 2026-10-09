"""Named API tokens for scripts, LibreNMS and MCP clients (#297).

A token is ``nmt_`` and 43 random characters, sent as ``Authorization:
Bearer <token>``.  It is shown once, when it is created; the database keeps
its SHA-256 (enough for a long random secret), its first characters to tell
tokens apart, a name, a role and an optional expiry.  Revoking one takes
effect on the next request.
"""

import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta

from nautobot_maps import db, timeutil

logger = logging.getLogger(__name__)

PREFIX = "nmt_"
SHOWN_CHARACTERS = 12  # "nmt_" and 8 more, in lists
ROLES = ("viewer", "operator", "admin")
MAX_NAME_LENGTH = 100
MAX_EXPIRY_DAYS = 3650
# last_used_at is written at most this often, not on every request.
LAST_USED_INTERVAL = timedelta(minutes=5)
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
COLUMNS = "id, name, prefix, role, created_by, created_at, expires_at, last_used_at, revoked_at, revoked_by"


class TokenError(ValueError):
    """A request that can't become a token; ``message`` says why (written here, safe to show)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def now() -> datetime:
    return timeutil.parse_iso_datetime(timeutil.iso_utc_now())


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_token(value: str) -> bool:
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


def create(conn, body: dict, created_by: str) -> dict:
    """Validate *body* (``name``, ``role``, optional ``expires_in_days``) and
    store a new token.  The result has ``token``, the secret: the only time
    it is available."""
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise TokenError("name is required")
    name = name.strip()
    if len(name) > MAX_NAME_LENGTH or not NAME_PATTERN.match(name):
        raise TokenError(
            f"name: up to {MAX_NAME_LENGTH} letters, digits, spaces, dots, dashes and underscores, "
            "starting with a letter or digit"
        )
    role = body.get("role")
    if role not in ROLES:
        raise TokenError(f"role must be one of: {', '.join(ROLES)}")
    days = body.get("expires_in_days")
    current = now()
    expires_at = None
    if days is not None:
        if isinstance(days, bool) or not isinstance(days, int) or days <= 0 or days > MAX_EXPIRY_DAYS:
            raise TokenError(f"expires_in_days must be a whole number from 1 to {MAX_EXPIRY_DAYS}")
        expires_at = current + timedelta(days=days)

    token = PREFIX + secrets.token_urlsafe(32)
    with db.transaction(conn):
        # The unique name is the check, so two creates at once can't both pass it.
        row = conn.execute(
            "INSERT INTO api_tokens (name, token_hash, prefix, role, created_by, created_at, expires_at) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (name) DO NOTHING RETURNING {COLUMNS}",
            (name, hash_token(token), token[:SHOWN_CHARACTERS], role, created_by, current, expires_at),
        ).fetchone()
    if row is None:
        raise TokenError("A token with this name already exists (revoked ones keep their name)")
    logger.info("API token %r created (role %s) by %s", name, role, created_by or "unknown")
    return {**_with_state(db.row_to_dict(row), current), "token": token}


def list_tokens(conn) -> list[dict]:
    """Every token, newest first, without secrets."""
    current = now()
    rows = conn.execute(f"SELECT {COLUMNS} FROM api_tokens ORDER BY created_at DESC, id DESC").fetchall()
    return [_with_state(token, current) for token in map(db.row_to_dict, rows)]


def revoke(conn, token_id: int, revoked_by: str) -> dict | None:
    """Revoke a token now.  None when it doesn't exist; revoking one twice changes nothing."""
    current = now()
    with db.transaction(conn):
        row = conn.execute(
            f"UPDATE api_tokens SET revoked_at = %s, revoked_by = %s "
            f"WHERE id = %s AND revoked_at IS NULL RETURNING {COLUMNS}",
            (current, revoked_by, token_id),
        ).fetchone()
        if row is None:
            row = conn.execute(f"SELECT {COLUMNS} FROM api_tokens WHERE id = %s", (token_id,)).fetchone()
            if row is None:
                return None
        else:
            logger.info("API token %r revoked by %s", db.row_to_dict(row)["name"], revoked_by or "unknown")
    return _with_state(db.row_to_dict(row), current)


def verify(token: str) -> dict | None:
    """``{"name", "role"}`` of an active token, else None (unknown, revoked or expired)."""
    if not is_token(token):
        return None
    conn = db.get_conn()
    if conn is None:
        return None
    current = now()
    try:
        row = conn.execute(
            "SELECT id, name, role, (last_used_at IS NULL OR last_used_at < %s) AS stale FROM api_tokens "
            "WHERE token_hash = %s AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > %s)",
            (current - LAST_USED_INTERVAL, hash_token(token), current),
        ).fetchone()
        found = db.row_to_dict(row)
        if not found:
            return None
        if found["stale"]:
            # The same condition again: of requests that all saw it stale, one writes.
            with db.transaction(conn):
                conn.execute(
                    "UPDATE api_tokens SET last_used_at = %s "
                    "WHERE id = %s AND (last_used_at IS NULL OR last_used_at < %s)",
                    (current, found["id"], current - LAST_USED_INTERVAL),
                )
        return {"name": found["name"], "role": found["role"]}
    finally:
        conn.close()
