"""Sign-in with AD in Chromium (#717, #723): the app's own page against a real domain
controller (development/samba_ad.sh; CI's ad-live job starts one and the app).

    E2E_AD_BASE_URL=http://127.0.0.1:5057 python -m pytest e2e/test_ad_sign_in.py

The app runs with LDAP_URL set to that domain controller and AUTH_REQUIRE_VIEWER=true;
jdoe has an identity with the noc role, bob has none (the job makes them).
"""

import os
import re

import pytest

BASE_URL = os.getenv("E2E_AD_BASE_URL", "").rstrip("/")
if not BASE_URL:
    pytest.skip("E2E_AD_BASE_URL not set: the ad-live job runs these", allow_module_level=True)
sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
expect = pytest.importorskip("playwright.sync_api").expect

# development/samba_ad.sh's test accounts: test-only passwords, nowhere else.
JANE = ("jdoe", "Jane-Pass-2026!x")
BOB = ("bob", "Bob-Pass-2026!x")


@pytest.fixture
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        yield page
        browser.close()
        assert errors == [], errors


def sign_in(page, username, password):
    page.locator("#auth-username input, input#auth-username").first.fill(username)
    page.locator("#auth-password input, input#auth-password").first.fill(password)
    page.locator("#auth-submit").click()


def test_signing_in_and_out(page):
    # Not signed in: the page asked for goes to /auth, and back after.
    page.goto(f"{BASE_URL}/alerts")
    page.wait_for_url(re.compile(r"/auth\?redirect="))
    sign_in(page, JANE[0], "wrong")
    expect(page.locator("#auth-error")).to_have_text("Wrong user name or password")
    sign_in(page, *JANE)
    # NOC's workspace: /alerts is its board, /noc/board.
    page.wait_for_url(re.compile(r"/noc/board"))
    cookie = next(c for c in page.context.cookies() if c["name"] == "nm_session")
    assert cookie["httpOnly"] and cookie["secure"] and cookie["sameSite"] == "Strict"
    me = page.evaluate("fetch('/api/v1/me').then((r) => r.json())")
    assert me["username"] == "jdoe" and me["access"]["roles"] == ["noc"]
    # The top bar's Sign out: the cookie goes, and the pages ask again.
    page.get_by_role("button", name="Pages", exact=True).click()
    page.locator(".app-nav-sign-out").click()
    page.wait_for_url(re.compile(r"/auth\?signed_out=1"))
    expect(page.locator("#auth-signed-out")).to_be_visible()
    assert not [c for c in page.context.cookies() if c["name"] == "nm_session"]
    page.goto(f"{BASE_URL}/alerts")
    page.wait_for_url(re.compile(r"/auth\?redirect="))


def test_without_an_identity_no_access(page):
    page.goto(f"{BASE_URL}/auth")
    sign_in(page, *BOB)
    expect(page.locator("#auth-error")).to_contain_text("No access")
    assert not [c for c in page.context.cookies() if c["name"] == "nm_session"]
