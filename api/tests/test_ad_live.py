"""Sign-in against a real Active Directory (#717, #782), through the company's sit_core:
skipped without AD_LIVE_HOST (the domain controller's host name) or without sit_core
installed. The accounts are development/samba_ad.sh's (jdoe, bob); another AD: set
AD_LIVE_USER/AD_LIVE_PASSWORD to an account of its own. tests/test_ad_login.py
covers the rest with a pretend AD."""

import importlib.util
import os

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.core.settings import settings
from app.services import ad_service, identity_service, login_service, role_service
from tests import db as adb

LIVE_HOST = os.environ.get("AD_LIVE_HOST", "")
USER = os.environ.get("AD_LIVE_USER", "jdoe")
PASSWORD = os.environ.get("AD_LIVE_PASSWORD", "Jane-Pass-2026!x")
pytestmark = [
    pytest.mark.skipif(not LIVE_HOST, reason="AD_LIVE_HOST not set"),
    pytest.mark.skipif(importlib.util.find_spec("sit_core") is None, reason="no sit_core"),
]


@pytest.fixture
def live(override_settings, pg_database):
    adb.sql("TRUNCATE roles, permissions, identities, identities_to_roles CASCADE")
    adb.call(role_service.seed_defaults)
    override_settings(
        AUTH_MODE="header",
        AUTH_REQUIRE_VIEWER="true",
        AD_HOST=LIVE_HOST,
        JWT_SIGNING_SECRET="live-test-secret-" + "x" * 32,
    )
    login_service.forget_failures()
    return settings


def test_the_password_decides(live):
    found = ad_service.authenticate(USER, PASSWORD, live)
    assert found is not None and found.unique_name == USER.lower()
    assert ad_service.authenticate(USER, PASSWORD + "-wrong", live) is None
    assert ad_service.authenticate(USER, "", live) is None
    assert ad_service.authenticate("nobody-at-all", PASSWORD, live) is None
    assert ad_service.authenticate("*", PASSWORD, live) is None


def test_sign_in_through_the_api(live):
    with TestClient(app, base_url="https://maps.test") as client:
        sent = {"username": USER, "password": PASSWORD}
        assert client.post("/api/v1/auth/login", json=sent).status_code == 200
        assert client.get("/api/v1/me").json()["role"] == "viewer", "the first sign-in"
        wrong = {"username": USER, "password": "nope"}
        assert client.post("/api/v1/auth/login", json=wrong).status_code == 401
    assert adb.call(identity_service.read, USER).roles == ("viewer",)
