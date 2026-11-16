"""Sign-in against a real Active Directory (#717): development/samba_ad.sh starts one
(Samba AD, LDAPS with its own CA); CI's ad-live job does. Without AD_LIVE_URL these
are skipped; tests/test_ad_login.py covers the rest with a fake directory."""

import os

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.core.settings import get_settings
from app.services import ad_service, identity_service, login_service, role_service
from tests import db as adb

LIVE_URL = os.environ.get("AD_LIVE_URL", "")
LIVE_CA = os.environ.get("AD_LIVE_CA_FILE", "")
pytestmark = pytest.mark.skipif(not LIVE_URL, reason="AD_LIVE_URL not set")

ACCOUNTS = {
    "LDAP_BASE_DN": "DC=nmaps,DC=test",
    "LDAP_BIND_DN": "svc-maps@nmaps.test",
    "LDAP_BIND_PASSWORD": "Svc-Pass-2026!x",
}


@pytest.fixture
def live(configure, pg_database):
    adb.sql("TRUNCATE roles, permissions, identities, identities_to_roles CASCADE")
    adb.call(role_service.seed_defaults)
    configure(
        AUTH_MODE="header",
        AUTH_REQUIRE_VIEWER="true",
        LDAP_URL=LIVE_URL,
        EXTRA_CA_PEM=open(LIVE_CA, encoding="ascii").read(),
        JWT_SIGNING_SECRET="live-test-secret-" + "x" * 32,
        **ACCOUNTS,
    )
    login_service.forget_failures()
    return get_settings()


def test_the_password_decides(live):
    found = ad_service.authenticate("jdoe", "Jane-Pass-2026!x", live)
    assert found is not None and found.unique_name == "jdoe" and found.common_name == "Jane Doe"
    assert found.dn.startswith("CN=Jane Doe,CN=Users,")
    assert ad_service.authenticate("NMAPS\\jdoe", "Jane-Pass-2026!x", live) is not None
    assert ad_service.authenticate("jdoe", "wrong", live) is None
    assert ad_service.authenticate("jdoe", "", live) is None
    assert ad_service.authenticate("nobody", "Jane-Pass-2026!x", live) is None
    assert ad_service.authenticate("*", "Jane-Pass-2026!x", live) is None


def test_the_certificate_is_checked(live, configure, tmp_path):
    """Another CA's certificate (here: the system's CAs only) is refused."""
    configure(EXTRA_CA_PEM=open("/etc/ssl/certs/ca-certificates.crt", encoding="ascii").read())
    with pytest.raises(ad_service.DirectoryUnavailable):
        ad_service.authenticate("jdoe", "Jane-Pass-2026!x", get_settings())


def test_a_wrong_service_account_is_the_setups_fault(live, configure):
    configure(LDAP_BIND_PASSWORD="not-it")
    with pytest.raises(ad_service.DirectoryUnavailable):
        ad_service.authenticate("jdoe", "Jane-Pass-2026!x", get_settings())


def test_sign_in_through_the_api(live):
    adb.call(identity_service.create, "jdoe", ["noc"])
    with TestClient(app, base_url="https://maps.test") as client:
        sent = {"username": "jdoe", "password": "Jane-Pass-2026!x"}
        assert client.post("/api/v1/auth/login", json=sent).status_code == 200
        assert client.get("/api/v1/me").json()["role"] == "noc"
        bob = {"username": "bob", "password": "Bob-Pass-2026!x"}
        assert client.post("/api/v1/auth/login", json=bob).json()["error"] == "No access"
        wrong = {"username": "jdoe", "password": "nope"}
        assert client.post("/api/v1/auth/login", json=wrong).status_code == 401
