"""Who is calling (#582): identity from a trusted sign-in proxy, role from groups.

Same model as the current app (nautobot_maps/auth.py): in header mode the user
and group headers count only when the direct peer is in AUTH_TRUSTED_PROXIES
(and sends AUTH_PROXY_SECRET when one is set); anyone else could set them.
"""

import hmac
import ipaddress
import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import Request

from app.utils.config import Role, Settings
from app.v1.me.dtos import MeDto

logger = logging.getLogger(__name__)

ROLE_LEVELS: dict[str, int] = {"viewer": 1, "operator": 2, "admin": 3}
PROXY_SECRET_HEADER = "X-Auth-Proxy-Secret"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Peers already warned about, so a scanner can't flood the log.
_logged_untrusted: set[str] = set()


@dataclass(frozen=True)
class CurrentUser:
    username: str | None
    role: Role | None
    groups: list[str] = field(default_factory=list)

    def has_role(self, required: Role) -> bool:
        return self.role is not None and ROLE_LEVELS[self.role] >= ROLE_LEVELS[required]


def resolve_user(request: Request, settings: Settings) -> CurrentUser:
    if settings.auth_mode == "disabled":
        return CurrentUser(username=None, role="admin")
    default_role = settings.auth_default_role or None
    username = request.headers.get(settings.auth_header_user, "").strip()
    if not username or not _from_trusted_proxy(request, settings):
        return CurrentUser(username=None, role=None)
    groups = [
        g.strip()
        for g in request.headers.get(settings.auth_header_groups, "").split(",")
        if g.strip()
    ]
    return CurrentUser(
        username=username, role=_role_from_groups(groups, settings) or default_role, groups=groups
    )


def is_cross_site_write(request: Request, settings: Settings) -> bool:
    """A change sent by a browser from another site (CSRF), except from CORS_ORIGINS.

    Behind a sign-in proxy the session cookie comes along, so a page elsewhere
    could act as the signed-in user. Browsers say where a request comes from:
    Sec-Fetch-Site, else Origin. Scripts send neither and are not affected.
    """
    if request.method in SAFE_METHODS:
        return False
    origin = request.headers.get("origin", "").strip()
    if origin and origin in settings.cors_origins:
        return False
    fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
    if fetch_site:
        return fetch_site not in {"same-origin", "none"}
    if not origin:
        return False
    return origin == "null" or _origin_of(origin) != _origin_of(str(request.base_url))


def _origin_of(url: str) -> tuple[str, str, int | None]:
    """Scheme, host and port (default filled in): what makes two origins the same."""
    parts = urlsplit(url.lower())
    try:
        port = parts.port
    except ValueError:
        return ("invalid", url, None)
    default_port = {"http": 80, "https": 443}.get(parts.scheme)
    return (parts.scheme, parts.hostname or "", port or default_port)


def to_me_dto(user: CurrentUser, settings: Settings) -> MeDto:
    return MeDto(
        username=user.username, role=user.role, groups=user.groups, auth_mode=settings.auth_mode
    )


def _role_from_groups(groups: list[str], settings: Settings) -> Role | None:
    names = {g.lower() for g in groups}
    if names & settings.auth_admin_groups:
        return "admin"
    if names & settings.auth_operator_groups:
        return "operator"
    if names & settings.auth_viewer_groups:
        return "viewer"
    return None


def _from_trusted_proxy(request: Request, settings: Settings) -> bool:
    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    trusted = any(address in network for network in settings.auth_trusted_proxies)
    if trusted and settings.auth_proxy_secret:
        sent = request.headers.get(PROXY_SECRET_HEADER, "")
        trusted = hmac.compare_digest(sent.encode(), settings.auth_proxy_secret.encode())
    if not trusted and str(address) not in _logged_untrusted and len(_logged_untrusted) < 100:
        _logged_untrusted.add(str(address))
        logger.warning(
            "Header auth: ignoring identity headers from %s (not in AUTH_TRUSTED_PROXIES%s)",
            address,
            " or wrong/missing proxy secret" if settings.auth_proxy_secret else "",
        )
    return trusted
