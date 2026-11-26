"""The AD service (#717, #782) around sit_core's BasicAuthenticator, with a pretend
sit_core: the company's package isn't on PyPI. The login around it: test_ad_login.py
(#774)."""

import sys
import types

import pytest
from ldap3.core.exceptions import LDAPBindError, LDAPSocketOpenError

from app.core.settings import settings
from app.services import ad_service

USERS = {"jdoe": ("Jane-Pass-1!", "Jane Doe"), "bob": ("Bob-Pass-1!", "Bob Builder")}


@pytest.fixture
def sit_core(monkeypatch):
    """sit_core.auth with a BasicAuthenticator that knows jdoe and bob, as AD does:
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

    auth = types.ModuleType("sit_core.auth")
    auth.BasicAuthenticator = BasicAuthenticator
    ldap = types.ModuleType("sit_core.auth.ldap")
    ldap.attributes = types.SimpleNamespace(USERNAME="sAMAccountName", COMMON_NAME="cn")
    for name, module in {
        "sit_core": types.ModuleType("sit_core"),
        "sit_core.auth": auth,
        "sit_core.auth.ldap": ldap,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return state


@pytest.fixture
def ad(override_settings, sit_core):
    override_settings(AD_HOST="dc1.example.test", JWT_SIGNING_SECRET="s" * 40)
    return lambda username, password: ad_service.authenticate(username, password, settings)


def test_the_name_forms_and_the_host(ad, sit_core):
    found = ad("jdoe", "Jane-Pass-1!")
    assert found == ad_service.AdUser(unique_name="jdoe", common_name="Jane Doe")
    assert ad("NMAPS\\jdoe", "Jane-Pass-1!") == found
    assert ad("jdoe@nmaps.test", "Jane-Pass-1!") == found
    assert sit_core["asked"] == ["jdoe"] * 3, "the plain name is what AD is asked"
    assert set(sit_core["hosts"]) == {"dc1.example.test"}


def test_the_password_decides(ad):
    assert ad("bob", "Bob-Pass-1!").common_name == "Bob Builder"
    assert ad("bob", "Jane-Pass-1!") is None
    assert ad("nobody", "Jane-Pass-1!") is None


def test_no_anonymous_bind(ad, sit_core):
    """An empty password would be an anonymous bind that "succeeds": never asked."""
    for name, password in (("jdoe", ""), ("", "x"), ("a\x00b", "x"), ("x" * 300, "x")):
        assert ad(name, password) is None
    assert sit_core["asked"] == []


def test_the_directory_down_or_sit_core_missing(ad, sit_core, monkeypatch):
    sit_core["down"] = True
    with pytest.raises(ad_service.DirectoryUnavailable):
        ad("jdoe", "Jane-Pass-1!")
    monkeypatch.setitem(sys.modules, "sit_core.auth", None)
    with pytest.raises(ad_service.DirectoryUnavailable):
        ad("jdoe", "Jane-Pass-1!")
