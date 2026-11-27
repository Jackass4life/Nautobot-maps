"""Sign-in with Active Directory (#717, #782; the example's services/ad_service.py):
the company's AD package (its import name: AD_AUTH_PACKAGE, #793) binds as the user
with its ``auth.BasicAuthenticator`` and their password on AD_HOST. Off until AD_HOST is
set. The package isn't on PyPI: imported only when someone signs in, so the app and the
tests run without it.
Blocking (ldap3 underneath is synchronous): callers run it in a thread.

Never logged or kept: a password. An empty one is refused before any bind, as an LDAP
simple bind with an empty password is an anonymous bind that "succeeds"."""

import importlib
from dataclasses import dataclass

from ldap3.core.exceptions import LDAPBindError, LDAPException
from loguru import logger

from app.core.settings import Settings

MAX_NAME_LENGTH = 256


class DirectoryUnavailable(Exception):
    """The directory can't be asked (down, TLS refused, the AD package not installed): not
    the user's fault, a 503."""


@dataclass(frozen=True)
class AdUser:
    unique_name: str
    common_name: str
    dn: str = ""


def enabled(settings: Settings) -> bool:
    return bool(settings.AD_HOST)


def _attributes(package: str):
    """``from <package>.auth.ldap import attributes``: a name there, or the submodule
    ``<package>.auth.ldap.attributes`` (the real package's, #797)."""
    ldap = importlib.import_module(f"{package}.auth.ldap")
    if hasattr(ldap, "attributes"):
        return ldap.attributes
    return importlib.import_module(f"{package}.auth.ldap.attributes")


def authenticator(settings: Settings):
    """The example's AdService on AD_HOST (tests replace this function)."""
    package = settings.AD_AUTH_PACKAGE
    try:
        authenticator_class = importlib.import_module(f"{package}.auth").BasicAuthenticator
        attributes = _attributes(package)
    except (ImportError, AttributeError) as exc:
        # The cause too: "isn't installed" is only one of them (#797).
        logger.error(
            "AD sign-in: AD_AUTH_PACKAGE {!r} isn't installed or has no auth.BasicAuthenticator "
            "(packages/README.md): {}: {}",
            package,
            type(exc).__name__,
            exc,
        )
        raise DirectoryUnavailable("the AD package is not installed") from None

    class AdService(authenticator_class):
        UNIQUE_NAME_ATTRIBUTE = attributes.USERNAME
        COMMON_NAME_ATTRIBUTE = attributes.COMMON_NAME

        def __init__(self):
            super().__init__(
                host=settings.AD_HOST,
                attributes=[self.UNIQUE_NAME_ATTRIBUTE, self.COMMON_NAME_ATTRIBUTE],
            )

    return AdService()


def _plain_name(username: str) -> str | None:
    name = (username or "").strip()
    if not name or len(name) > MAX_NAME_LENGTH or any(ord(char) < 32 for char in name):
        return None
    # DOMAIN\\name and name@domain: the name part.
    name = name.rsplit("\\", 1)[-1]
    return name.split("@", 1)[0] or None


def _text(value) -> str:
    # An LDAP attribute may come back as a list of values.
    if isinstance(value, list | tuple):
        value = value[0] if value else ""
    return str(value or "")


def authenticate(username: str, password: str, settings: Settings) -> AdUser | None:
    """The user, when *password* is theirs; None for an unknown user or a wrong
    password alike (the caller says the same for both)."""
    name = _plain_name(username)
    if name is None or not password:
        return None
    service = authenticator(settings)
    try:
        found = service.login(username=name, password=password)
    except LDAPBindError:
        return None
    except LDAPException as exc:
        logger.error("AD sign-in: the directory request failed: {}", type(exc).__name__)
        raise DirectoryUnavailable(type(exc).__name__) from None
    if not found:
        return None
    unique = _text(found.get(service.UNIQUE_NAME_ATTRIBUTE)) or name
    common = _text(found.get(service.COMMON_NAME_ATTRIBUTE))
    dn = _text(found.get("dn") or found.get("distinguishedName"))
    return AdUser(unique_name=unique.lower(), common_name=common, dn=dn)
