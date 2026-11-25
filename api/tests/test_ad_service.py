"""This repo's AD service (#717, on ldap3) against ldap3's fake directory, which checks
a bind's password as AD does. The login around it: test_ad_login.py (#774); a
deployment with its own ad_service drops this file."""

import pytest
from ldap3 import MOCK_SYNC, Connection, Server
from ldap3.core.exceptions import LDAPBindError, LDAPSocketOpenError

from app.core.settings import settings
from app.services import ad_service

BASE = "DC=nmaps,DC=test"
SERVICE = (f"CN=svc,{BASE}", "Svc-Pass-1!")
USERS = {
    "jdoe": ("Jane-Pass-1!", "Jane Doe"),
    "bob": ("Bob-Pass-1!", "Bob Builder"),
}


@pytest.fixture
def directory(monkeypatch):
    """A fake AD: the service account, jdoe and bob; ``state["down"]`` breaks it."""
    fake = Server("fake-ad")
    state = {"down": False, "binds": []}
    setup = Connection(fake, client_strategy=MOCK_SYNC)
    setup.strategy.add_entry(SERVICE[0], {"cn": "svc", "userPassword": SERVICE[1]})
    for name, (password, display) in USERS.items():
        setup.strategy.add_entry(
            f"CN={display},CN=Users,{BASE}",
            {
                "sAMAccountName": name.upper() if name == "jdoe" else name,
                "cn": display,
                "displayName": display,
                "userPassword": password,
                "objectClass": "user",
            },
        )

    def connect(target, user, password, settings):
        if state["down"]:
            raise LDAPSocketOpenError("down")
        state["binds"].append(user)
        conn = Connection(fake, user, password, client_strategy=MOCK_SYNC)
        if not conn.bind():
            raise LDAPBindError("invalidCredentials")
        return conn

    monkeypatch.setattr(ad_service, "connect", connect)
    return state


@pytest.fixture
def ad(override_settings, directory):
    override_settings(
        LDAP_URL="ldaps://fake-ad",
        LDAP_BASE_DN=BASE,
        LDAP_BIND_DN=SERVICE[0],
        LDAP_BIND_PASSWORD=SERVICE[1],
    )
    return lambda username, password: ad_service.authenticate(username, password, settings)


def test_the_name_as_people_type_it(ad):
    for name in ("jdoe", "JDoe", "NMAPS\\jdoe", "jdoe@nmaps.test"):
        found = ad(name, "Jane-Pass-1!")
        assert found is not None, name
        assert found.unique_name == "jdoe" and found.common_name == "Jane Doe"
        assert found.dn == f"CN=Jane Doe,CN=Users,{BASE}"


def test_the_password_decides(ad):
    assert ad("jdoe", "nope") is None
    assert ad("nobody", "Jane-Pass-1!") is None
    assert ad("bob", "Bob-Pass-1!").unique_name == "bob"


def test_no_anonymous_bind_with_an_empty_password(ad, directory):
    assert ad("jdoe", "") is None
    assert directory["binds"] == []


def test_a_filter_in_the_name_is_a_name(ad):
    assert ad("*", "Jane-Pass-1!") is None
    assert ad("j*)(|(cn=*", "Jane-Pass-1!") is None


def test_the_directory_down_or_the_service_account_refused(ad, directory, override_settings):
    directory["down"] = True
    with pytest.raises(ad_service.DirectoryUnavailable):
        ad("jdoe", "Jane-Pass-1!")
    directory["down"] = False
    override_settings(LDAP_BIND_PASSWORD="wrong")
    with pytest.raises(ad_service.DirectoryUnavailable):
        ad("jdoe", "Jane-Pass-1!")
