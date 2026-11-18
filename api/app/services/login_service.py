"""The app's own sign-in (#717; the example's services/auth.py): an AD user with an
identity gets a signed JWT, as the ``Authorization: Bearer`` value and as an HttpOnly
cookie. A request with one is that identity: its roles are read live (the identity
cache, 30 s), not taken from the token, so a removed role ends within 30 s, not when
the token expires. Failed sign-ins are throttled per name and address."""

import time
import uuid
from datetime import timedelta

import jwt

from app.core.settings import Settings
from app.services import identity_service
from app.utils import timeutil

COOKIE = "nm_session"
ALGORITHM = "HS256"
# Failed sign-ins per (name, address): after this many, a wait that doubles from 30 s.
FREE_FAILURES = 5
FIRST_WAIT_SECONDS = 30.0
MAX_WAIT_SECONDS = 900.0
# Forget a (name, address) after this long without a failure.
FAILURE_MEMORY_SECONDS = 3600.0

_failures: dict[tuple[str, str], tuple[int, float, float]] = {}


def issue(found: identity_service.Identity, settings: Settings) -> tuple[str, dict]:
    """A signed token for *found*, and its claims (the example's Jwt: iss, aud, jti,
    sub, exp, nbf, iat, scopes, common_name). The scopes are for the reader; the API
    reads the identity's own every request."""
    from app.auth import permissions

    now = timeutil.now()
    scopes: set[str] = set(found.scopes)
    for role in found.roles:
        scopes |= {str(scope) for scope in permissions.role_scopes(role)}
    claims = {
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "jti": uuid.uuid4().hex,
        "sub": found.unique_name,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.JWT_LIFETIME_SECONDS)).timestamp()),
        "common_name": found.common_name or None,
        "scopes": sorted(scopes),
    }
    return jwt.encode(claims, settings.JWT_SIGNING_SECRET, algorithm=ALGORITHM), claims


def verify(token: str, settings: Settings) -> dict | None:
    """The claims of a token this app signed and that is still valid, else None."""
    if not token or not settings.JWT_SIGNING_SECRET:
        return None
    try:
        claims = jwt.decode(
            token,
            settings.JWT_SIGNING_SECRET,
            algorithms=[ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            options={"require": ["exp", "iat", "nbf", "sub", "iss", "aud"]},
            leeway=30,
        )
    except jwt.PyJWTError:
        return None
    return claims if isinstance(claims.get("sub"), str) and claims["sub"] else None


def _key(username: str, address: str) -> tuple[str, str]:
    return (identity_service.normal_name(username), address or "")


def wait_seconds(username: str, address: str) -> int:
    """How long this name from this address must wait before trying again (0: now)."""
    found = _failures.get(_key(username, address))
    if found is None:
        return 0
    _, until, _ = found
    return max(0, int(until - time.monotonic() + 0.999))


def failed(username: str, address: str) -> None:
    now = time.monotonic()
    for key, (_, _, last) in list(_failures.items()):
        if now - last > FAILURE_MEMORY_SECONDS:
            del _failures[key]
    key = _key(username, address)
    count = _failures.get(key, (0, 0.0, 0.0))[0] + 1
    over = count - FREE_FAILURES + 1
    wait = 0.0 if over <= 0 else min(MAX_WAIT_SECONDS, FIRST_WAIT_SECONDS * 2 ** (over - 1))
    _failures[key] = (count, now + wait, now)


def succeeded(username: str, address: str) -> None:
    _failures.pop(_key(username, address), None)


def forget_failures() -> None:
    _failures.clear()
