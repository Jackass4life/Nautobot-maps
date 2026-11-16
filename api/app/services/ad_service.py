"""Sign-in with Active Directory (#717; the example's services/ad_service.py, on
ldap3): find the user with the read-only service account, then bind as them with
their password. Off until LDAP_URL is set. Blocking (ldap3 is synchronous): callers
run it in a thread.

Never logged or kept: a password. An empty one is refused before any bind, as an LDAP
simple bind with an empty password is an anonymous bind that "succeeds"."""

import ssl
from dataclasses import dataclass

from ldap3 import SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPBindError, LDAPException
from ldap3.utils.conv import escape_filter_chars
from loguru import logger

from app.core.settings import Settings

MAX_NAME_LENGTH = 256
COMMON_NAME_ATTRIBUTES = ("displayName", "cn")


class DirectoryUnavailable(Exception):
    """The directory can't be asked (down, TLS refused, the service account refused):
    not the user's fault, a 503."""


@dataclass(frozen=True)
class AdUser:
    unique_name: str
    common_name: str
    dn: str


def enabled(settings: Settings) -> bool:
    return bool(settings.ldap_url)


def server(settings: Settings) -> Server:
    """The domain controller, its certificate checked against EXTRA_CA_PEM (#739; else
    the system's CAs) and its name."""
    tls = Tls(
        validate=ssl.CERT_REQUIRED,
        version=ssl.PROTOCOL_TLS_CLIENT,
        ca_certs_data=settings.extra_ca_pem or None,
    )
    secure = settings.ldap_url.lower().startswith("ldaps://")
    return Server(
        settings.ldap_url,
        use_ssl=secure,
        tls=tls if secure else None,
        get_info=None,
        connect_timeout=settings.ldap_timeout_seconds,
    )


def connect(target: Server, user: str, password: str, settings: Settings) -> Connection:
    """A bound connection (tests replace it with ldap3's fake directory)."""
    return Connection(
        target,
        user,
        password,
        auto_bind=True,
        receive_timeout=settings.ldap_timeout_seconds,
        raise_exceptions=False,
    )


def _plain_name(username: str) -> str | None:
    name = (username or "").strip()
    if not name or len(name) > MAX_NAME_LENGTH or any(ord(char) < 32 for char in name):
        return None
    # DOMAIN\\name and name@domain: the name part.
    name = name.rsplit("\\", 1)[-1]
    return name.split("@", 1)[0] or None


def find(username: str, settings: Settings) -> AdUser | None:
    """*username* in the directory, with the service account: None when not there."""
    name = _plain_name(username)
    if name is None:
        return None
    target = server(settings)
    try:
        lookup = connect(target, settings.ldap_bind_dn, settings.ldap_bind_password, settings)
    except LDAPBindError:
        logger.error("AD sign-in: the service account (LDAP_BIND_DN) was refused")
        raise DirectoryUnavailable("service account refused") from None
    except LDAPException as exc:
        logger.error("AD sign-in: the directory can't be reached: {}", type(exc).__name__)
        raise DirectoryUnavailable(type(exc).__name__) from None
    try:
        attribute = settings.ldap_user_attribute
        lookup.search(
            settings.ldap_base_dn,
            f"({attribute}={escape_filter_chars(name)})",
            SUBTREE,
            attributes=[attribute, *COMMON_NAME_ATTRIBUTES],
            size_limit=2,
        )
        entries = list(lookup.entries)
    except LDAPException as exc:
        logger.error("AD sign-in: the search failed: {}", type(exc).__name__)
        raise DirectoryUnavailable(type(exc).__name__) from None
    finally:
        lookup.unbind()
    if len(entries) != 1:
        if len(entries) > 1:
            logger.warning("AD sign-in: {!r} is more than one entry: refused", name)
        return None
    entry = entries[0]
    unique = str(entry[attribute].value or name)
    common = next(
        (
            str(entry[key].value)
            for key in COMMON_NAME_ATTRIBUTES
            if key in entry and entry[key].value
        ),
        "",
    )
    return AdUser(unique_name=unique.lower(), common_name=common, dn=entry.entry_dn)


def authenticate(username: str, password: str, settings: Settings) -> AdUser | None:
    """The user, when *password* is theirs; None for an unknown user or a wrong
    password alike (the caller says the same for both)."""
    if not password:
        return None
    found = find(username, settings)
    if found is None:
        return None
    try:
        connect(server(settings), found.dn, password, settings).unbind()
    except LDAPBindError:
        return None
    except LDAPException as exc:
        logger.error("AD sign-in: the user's bind failed: {}", type(exc).__name__)
        raise DirectoryUnavailable(type(exc).__name__) from None
    return found
