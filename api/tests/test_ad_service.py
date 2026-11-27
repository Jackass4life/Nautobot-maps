"""The AD service (#717, #782) around the company's AD package's BasicAuthenticator,
with a pretend one, ``pretend_ad``: the package isn't on PyPI, and AD_AUTH_PACKAGE
names it (#793). The login around it: test_ad_login.py (#774)."""

import sys
import types

import pytest
from ldap3.core.exceptions import LDAPBindError, LDAPSocketOpenError

from app.core.settings import settings
from app.services import ad_service

USERS = {"jdoe": ("Jane-Pass-1!", "Jane Doe"), "bob": ("Bob-Pass-1!", "Bob Builder")}


@pytest.fixture
def ad_package(monkeypatch):
    """pretend_ad.auth with a BasicAuthenticator that knows jdoe and bob, as AD does:
    a wrong password is a refused bind. ``state["down"]`` breaks it."""
    state = {"down": False, "hosts": [], "asked": []}

    class BasicAuthenticator:
        def __init__(self, host, attributes):
            state["hosts"].append(host)
            self.attributes = attributes

        def login(self, username, password):
            state["asked"].append(username)
            if state["down"]:
                raise LDAPSocketOpenError("down")
            if username.lower() not in USERS or USERS[username.lower()][0] != password:
                raise LDAPBindError("invalidCredentials")
            # As AD has them: jdoe's name in capitals, attributes as lists.
            return {
                "sAMAccountName": [username.upper() if username == "jdoe" else username],
                "cn": USERS[username.lower()][1],
            }

    auth = types.ModuleType("pretend_ad.auth")
    auth.BasicAuthenticator = BasicAuthenticator
    # As in the real package, attributes is a submodule, not a name in auth.ldap (#797).
    attributes = types.ModuleType("pretend_ad.auth.ldap.attributes")
    attributes.USERNAME, attributes.COMMON_NAME = "sAMAccountName", "cn"
    for name, module in {
        "pretend_ad": types.ModuleType("pretend_ad"),
        "pretend_ad.auth": auth,
        "pretend_ad.auth.ldap": types.ModuleType("pretend_ad.auth.ldap"),
        "pretend_ad.auth.ldap.attributes": attributes,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return state


@pytest.fixture
def ad(override_settings, ad_package):
    override_settings(
        AD_HOST="dc1.example.test", AD_AUTH_PACKAGE="pretend_ad", JWT_SIGNING_SECRET="s" * 40
    )
    return lambda username, password: ad_service.authenticate(username, password, settings)


def test_the_name_forms_and_the_host(ad, ad_package):
    found = ad("jdoe", "Jane-Pass-1!")
    assert found == ad_service.AdUser(unique_name="jdoe", common_name="Jane Doe")
    assert ad("NMAPS\\jdoe", "Jane-Pass-1!") == found
    assert ad("jdoe@nmaps.test", "Jane-Pass-1!") == found
    assert ad_package["asked"] == ["jdoe"] * 3, "the plain name is what AD is asked"
    assert set(ad_package["hosts"]) == {"dc1.example.test"}


def test_the_password_decides(ad):
    assert ad("bob", "Bob-Pass-1!").common_name == "Bob Builder"
    assert ad("bob", "Jane-Pass-1!") is None
    assert ad("nobody", "Jane-Pass-1!") is None


def test_no_anonymous_bind(ad, ad_package):
    """An empty password would be an anonymous bind that "succeeds": never asked."""
    for name, password in (("jdoe", ""), ("", "x"), ("a\x00b", "x"), ("x" * 300, "x")):
        assert ad(name, password) is None
    assert ad_package["asked"] == []


def test_the_directory_down_or_the_package_missing(ad, ad_package, monkeypatch):
    ad_package["down"] = True
    with pytest.raises(ad_service.DirectoryUnavailable):
        ad("jdoe", "Jane-Pass-1!")
    monkeypatch.setitem(sys.modules, "pretend_ad.auth", None)
    with pytest.raises(ad_service.DirectoryUnavailable):
        ad("jdoe", "Jane-Pass-1!")


def test_the_log_says_why_the_package_cant_be_used(ad, monkeypatch, caplog):
    """ "isn't installed" is one cause of several: the log names the real one (#797)."""
    monkeypatch.setitem(sys.modules, "pretend_ad.auth.ldap.attributes", None)
    with pytest.raises(ad_service.DirectoryUnavailable):
        ad("jdoe", "Jane-Pass-1!")
    assert "ModuleNotFoundError" in caplog.text and "pretend_ad.auth.ldap.attributes" in caplog.text
