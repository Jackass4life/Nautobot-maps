"""End-to-end tests in Chromium (#294): the real app, the mock Nautobot and PostgreSQL.

Run against a running app: ``E2E_BASE_URL=http://127.0.0.1:5055 python -m pytest e2e/``
(CI starts the mock, PostgreSQL and the app first; see the e2e job).
"""

import csv
import io
import json
import os
import time
import urllib.request

import pytest

BASE_URL = os.getenv("E2E_BASE_URL", "").rstrip("/")
if not BASE_URL:
    pytest.skip("E2E_BASE_URL not set: start the app and point the e2e tests at it", allow_module_level=True)
sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        chromium = p.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture(params=["light", "dark"])
def page(browser, request):
    """A page in both themes; any uncaught JS error fails the test."""
    context = browser.new_context(viewport={"width": 1400, "height": 900}, color_scheme=request.param)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    context.close()
    assert errors == [], errors


def api(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=30) as response:
        return json.load(response)


def site_row(page, name: str):
    return page.locator(".site-row", has=page.locator(".site-name", has_text=name)).first


def test_the_board_lists_the_alarms(page):
    page.goto(f"{BASE_URL}/alerts")
    page.wait_for_selector(".site-row")
    board = api("/api/alerts")
    alarms = [row["name"] for row in board["alerts"] if row["alert_level"] in ("critical", "medium", "low")]
    assert alarms, "the mock has sites in alarm"
    for name in alarms:
        assert site_row(page, name).is_visible(), name


def test_adding_a_case_shows_it_on_the_row(page, request):
    page.goto(f"{BASE_URL}/alerts")
    page.wait_for_selector(".case-open-btn")
    row = page.locator(".site-row", has=page.locator(".case-open-btn")).first
    site_name = row.locator(".site-name").inner_text()
    case = f"E2E-{request.node.callspec.id.upper()}-{int(time.time())}"  # new on every run
    row.locator(".case-open-btn").click()
    page.wait_for_selector("#case-input")
    # A device that already has a case starts unticked: tick one either way.
    page.locator(".case-device-checkbox").first.check()
    page.fill("#case-input", case)
    page.click(".case-save-btn")
    page.locator(".site-row", has_text=site_name).locator(f"text={case}").first.wait_for(timeout=15000)
    cases = [c for row in api("/api/alerts")["alerts"] for d in row["down_devices"] for c in d["case_numbers"]]
    assert case in cases


def test_history_exports_a_csv(page):
    page.goto(f"{BASE_URL}/alerts")
    page.wait_for_selector(".row-menu-btn")
    row = page.locator(".site-row", has=page.locator(".case-open-btn")).first
    row.locator(".row-menu-btn").click()
    row.locator(".history-btn").click()
    link = page.locator(".history-export-link").first
    link.wait_for()
    with page.expect_download() as download:
        link.click()
    path = download.value.path()
    with open(path, encoding="utf-8-sig") as handle:
        header = next(csv.reader(io.StringIO(handle.read())))
    assert "site" in ",".join(header)


def test_the_wall_view_has_no_controls(page):
    page.goto(f"{BASE_URL}/alerts?view=wall")
    page.wait_for_selector(".site-row")
    assert page.locator(".case-open-btn:visible").count() == 0
    assert page.locator(".row-menu-btn:visible").count() == 0
    assert page.locator("#wall-sound").is_visible()


def test_the_map_shows_markers(page):
    page.goto(f"{BASE_URL}/")
    page.locator(".leaflet-marker-icon").first.wait_for(timeout=20000)
    assert page.locator(".leaflet-marker-icon").count() > 0


def test_the_availability_report(page):
    page.goto(f"{BASE_URL}/availability")
    page.locator("table").first.wait_for()
    assert page.locator("table tbody tr").count() > 0


def test_a_tenant_status_page(page):
    tenant = next(t for row in api("/api/alerts")["alerts"] for t in row.get("tenants") or [])
    page.goto(f"{BASE_URL}/status/{urllib.request.quote(tenant)}")
    headline = page.locator("#status-headline")
    for _ in range(100):
        if headline.inner_text() != "Loading…":
            break
        page.wait_for_timeout(100)
    assert "operational" in headline.inner_text().lower() or "affected" in headline.inner_text().lower()
    assert page.locator(".status-site").count() > 0
