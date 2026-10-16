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


def test_a_wall_for_some_sites(page):
    """?q= and ?tenant= (#364): the wall, and the board behind it, show those sites only."""
    page.goto(f"{BASE_URL}/alerts?view=wall&q=Etage")
    page.wait_for_selector(".site-row")
    rows = page.locator(".site-row .site-name").all_inner_texts()
    assert rows and all("Etage" in name for name in rows), rows
    page.locator("#wall-updated", has_text="Updated").wait_for()
    assert page.locator("#wall-updated").inner_text().startswith("“Etage” · Updated")
    page.locator(".wall-exit").click()
    page.wait_for_selector(".site-row")
    assert page.url.endswith("/alerts?q=Etage")
    assert page.locator("#board-scope").inner_text() == "Only “Etage” · Show all sites"
    assert all("Etage" in name for name in page.locator(".site-row .site-name").all_inner_texts())
    assert page.locator(".app-nav-link", has_text="Wall view").get_attribute("href") == "/alerts?view=wall&q=Etage"
    # A link that matches no site is a warning, never the green all-clear.
    page.goto(f"{BASE_URL}/alerts?view=wall&tenant=nobody%20at%20all")
    page.locator(".scope-no-match", has_text="No site matches nobody at all.").wait_for()
    assert page.locator(".all-clear").count() == 0


def test_the_map_and_wall_link_for_some_sites(page):
    """The map with ?tenant= (#367), and Copy wall link on a filtered board (#366)."""
    page.goto(f"{BASE_URL}/?tenant=nordic%20net")
    page.locator("#map-scope", has_text="Only Nordic Net").wait_for(timeout=20000)
    count = page.locator("#location-count").inner_text()
    assert int(count.split()[0]) < 9, count
    assert page.locator(".app-nav-link", has_text="Alert Board").get_attribute("href") == "/alerts?tenant=nordic+net"
    page.goto(f"{BASE_URL}/?q=nowhere-at-all")
    page.locator("#map-scope.scope-no-match", has_text="No site matches").wait_for(timeout=20000)

    page.goto(f"{BASE_URL}/alerts")
    page.wait_for_selector(".site-row")
    assert page.locator("#copy-wall-link").is_hidden()
    page.evaluate(
        "() => { window.__copied = []; navigator.clipboard.writeText = async (text) => { window.__copied.push(text); }; }"
    )
    page.locator("#filter-site").fill("London")
    page.locator("#copy-wall-link").click()
    page.locator("#copy-wall-link", has_text="Copied").wait_for()
    assert page.evaluate("window.__copied") == [f"{BASE_URL}/alerts?view=wall&q=London"]


def test_the_map_shows_markers(page):
    page.goto(f"{BASE_URL}/")
    page.locator(".leaflet-marker-icon").first.wait_for(timeout=20000)
    assert page.locator(".leaflet-marker-icon").count() > 0


def test_the_availability_report(page):
    page.goto(f"{BASE_URL}/availability")
    page.locator("table").first.wait_for()
    assert page.locator("table tbody tr").count() > 0
    # The three downloads, each with the report header (#354).
    links = page.locator(".availability-note a").evaluate_all("as => as.map(a => a.getAttribute('href'))")
    assert [link.split("view=")[1] for link in links] == ["sites&header=1", "tenants&header=1", "incidents&header=1"]
    with urllib.request.urlopen(f"{BASE_URL}{links[2]}", timeout=30) as response:
        text = response.read().decode("utf-8-sig")
    assert text.startswith("report,Nautobot Maps availability") and "\r\n\r\nsite,site_path,tenants,device," in text


def test_the_availability_page_answers_first(page):
    """Cards, then the worst first; sort and search (#342)."""
    page.goto(f"{BASE_URL}/availability")
    assert page.locator(".avail-card").count() == 4
    sites = page.locator('[data-avail-table="sites"]')
    assert sites.locator(".avail-filters").is_visible()
    shown = [
        float(v)
        for v in sites.locator("tbody tr:visible").evaluate_all("rows => rows.map(r => r.dataset.availability)")
    ]
    assert shown == sorted(shown), "worst first"
    sites.locator("th[data-sort=name] button").click()
    assert sites.locator("th[data-sort=name]").get_attribute("aria-sort") == "ascending"
    name = sites.locator("tbody tr:visible").first.get_attribute("data-name")
    sites.locator(".avail-search").fill(name)
    assert sites.locator("tbody tr:visible").count() >= 1
    assert all(
        name.lower() in n.lower()
        for n in sites.locator("tbody tr:visible").evaluate_all(
            "rows => rows.map(r => r.dataset.name + ' ' + r.dataset.tenants)"
        )
    )


@pytest.fixture
def operator_page(page):
    """*page* as an operator: maintenance changes need the role (#343). CI makes
    the token (``token create e2e --role operator``) before the tests run."""
    token = os.getenv("E2E_OPERATOR_TOKEN", "")
    assert token.startswith("nmt_"), "set E2E_OPERATOR_TOKEN to an operator token (see the e2e job)"
    page.context.set_extra_http_headers({"Authorization": f"Bearer {token}"})
    return page


def test_planning_maintenance_for_several_sites(operator_page, request):
    """Plan for two sites from the Maintenance page, see one batch, cancel it (#343)."""
    page = operator_page
    reason = f"e2e batch {request.node.callspec.id} {int(time.time())}"  # new on every run
    sites = api("/api/maintenance/sites")["sites"]
    assert len(sites) >= 2
    page.goto(f"{BASE_URL}/maintenance")
    page.locator("#maint-plan-open").click()
    for site in sites[:2]:
        page.locator("#maint-site-search").fill(site["name"])
        page.locator(".maint-match", has_text=site["name"]).first.click()
    assert page.locator("#maint-chosen .maint-chip").count() == 2
    page.locator('input[name="maint-when"][value="planned"]').check()
    assert page.locator(".when-calendar .cal-day").count() > 27
    page.locator(".when-calendar .cal-day:not([disabled])").nth(1).click()
    page.locator(".when-calendar .cal-day:not([disabled])").nth(1).click()
    page.locator("#maint-plan-reason").fill(reason)
    page.locator(".maint-save-btn").click()
    batch = page.locator(".maint-batch", has_text=reason)
    batch.wait_for()
    assert "2 sites" in batch.inner_text()
    cancel = batch.locator(".maint-end")
    cancel.click()
    assert "Sure?" in cancel.inner_text()
    cancel.click()
    # Cancelled: out of Planned, into the folded Ended group.
    page.locator('[data-group="upcoming"] .maint-batch', has_text=reason).wait_for(state="detached")
    # Folded, so its text content, not the visible text.
    assert "CANCELLED" in page.locator('[data-group="past"] .maint-batch', has_text=reason).first.text_content()


def test_a_sites_maintenance_history(page):
    """From the board's ⋯ menu to that site's maintenance history, and its export (#353)."""
    page.goto(f"{BASE_URL}/alerts")
    page.wait_for_selector(".row-menu-btn")
    row = page.locator(".site-row").first
    name = row.locator(".site-name").inner_text()
    row.locator(".row-menu-btn").click()
    row.locator(".maint-history-link").click()
    page.wait_for_url("**/maintenance?view=history&site_id=*")
    page.locator('[data-group="history"]').wait_for()
    assert page.locator("#maint-history-bar").is_visible() and not page.locator("#maint-plan-open").is_visible()
    assert "Site:" in page.locator("#maint-site-filter").inner_text()
    assert "site_id=" in page.locator("#maint-history-csv").get_attribute("href")
    # The period: Last 7 days, kept in the address.
    page.locator("#period-button").click()
    page.locator(".period-preset", has_text="Last 7 days").click()
    page.wait_for_url("**from=*")
    # Every site: the site filter goes.
    page.locator("#maint-site-filter .maint-chip-remove").click()
    page.wait_for_function("!location.search.includes('site_id')")
    assert name


def test_the_availability_period_calendar(page):
    """Days are clicked, not typed (#341): quick choices, two clicks, Escape."""
    page.goto(f"{BASE_URL}/availability")
    button = page.locator("#period-button")
    assert button.is_visible() and not page.locator("#availability-from").is_visible()
    button.click()
    assert page.locator("#period-popover").is_visible()
    page.locator(".period-preset", has_text="Last 7 days").click()
    page.wait_for_url("**from=*")
    assert "to=" in page.url and "month=" not in page.url
    assert page.locator(".availability-note").is_visible(), "a report, not an error"

    # Two clicks in the month shown: the first and the last enabled day.
    button = page.locator("#period-button")
    button.click()
    days = page.locator(".cal-day:not([disabled])")
    first, last = days.first.get_attribute("data-day"), days.last.get_attribute("data-day")
    days.last.click()
    assert page.locator("#period-popover").is_visible(), "one click starts a range"
    days.first.click()
    page.wait_for_url(f"**from={first}&to={last}*")
    assert "–" in page.locator("#period-button").inner_text() or first == last

    # Keyboard: open, Escape closes and gives focus back.
    page.locator("#period-button").focus()
    page.keyboard.press("Enter")
    assert page.locator("#period-popover").is_visible()
    # The month arrows keep the focus, so Escape still works after them.
    page.locator('.cal-nav[data-step="-1"]').focus()
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.dataset.step") == "-1"
    page.keyboard.press("Escape")
    assert not page.locator("#period-popover").is_visible()
    assert page.evaluate("document.activeElement.id") == "period-button"

    # Back to a month: its first to last day, shown and reported as the month.
    page.locator("#period-button").click()
    page.locator(".period-preset", has_text="This month").click()
    page.wait_for_url("**from=*-01&to=*")
    assert " " in page.locator("#period-button").inner_text() and "–" not in page.locator("#period-button").inner_text()
    href = page.locator(".availability-note a").first.get_attribute("href")
    assert api(href.replace("/api/availability.csv", "/api/availability"))["month"], href


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


def test_the_map_at_scale(page):
    """1,200 sites, 25 in alarm, 3 in maintenance (#334): every one of those is its
    own pin at the first zoom, the rest are a few quiet clusters, and a site
    inside a cluster opens from a link."""
    sites = [
        {
            "id": f"s{i}",
            "name": f"Site {i:04d}",
            "latitude": 40 + (i % 40) * 0.5,
            "longitude": -5 + (i // 40) * 0.8,
            "status": "Active",
            "location_type": "Site",
        }
        for i in range(1200)
    ]
    levels = {f"s{i}": {"level": "ok", "down": 0, "monitored": 8} for i in range(1200)}
    for n in range(25):
        levels[f"s{n * 40}"] = {
            "level": ("critical", "medium", "low")[n % 3],
            "reason": "x",
            "down": 2,
            "monitored": 8,
            "since": f"2026-10-12T08:{n:02d}:00Z",
        }
    for n in range(3):
        levels[f"s{n * 40 + 7}"] = {"level": "maintenance", "down": 0, "monitored": 8}
    page.route("**/api/locations", lambda route: route.fulfill(json={"locations": sites}))
    page.route("**/api/location-alerts", lambda route: route.fulfill(json={"levels": levels}))
    page.route(
        "**/api/locations/*/detail*", lambda route: route.fulfill(json={"devices": [], "asns": [], "circuits": []})
    )
    page.route("https://*.tile.openstreetmap.org/**", lambda route: route.abort())

    started = time.monotonic()
    page.goto(f"{BASE_URL}/")
    page.locator("#location-count", has_text="1200 locations").wait_for(timeout=20000)
    alarm_pins = page.locator(".leaflet-marker-icon:has(svg)")
    # The 28 pins come once the levels are in; the clusters are the rest.
    for _ in range(100):
        if alarm_pins.count() == 28:
            break
        page.wait_for_timeout(100)
    loaded = time.monotonic() - started
    clusters = page.locator(".leaflet-marker-icon:has(.quiet-cluster)")
    assert alarm_pins.count() == 28, alarm_pins.count()
    # The map opens fitted to the sites (#362): this even grid of 1,200 makes
    # about 80 clusters, some 15 sites each, rather than a dot per site.
    assert 0 < clusters.count() < 100, f"{clusters.count()} clusters at the first zoom"
    assert loaded < 10, f"{loaded:.1f} s to show 1,200 sites"
    # No cluster wears an alarm colour: the alarms are the pins.
    for colour in ("#e74c3c", "#ff8c00", "#eab308"):
        assert (
            page.locator(
                f".leaflet-marker-icon:has(.quiet-cluster[style*='{colour}'], .quiet-dot[style*='{colour}'])"
            ).count()
            == 0
        ), colour

    # The sidebar lists them: worst first, then newest; ten and "+15 more" (#334).
    assert page.locator("#alarm-count").inner_text() == "(25)"
    assert page.locator(".alarm-item").count() == 10
    assert page.locator("#alarm-more").inner_text() == "+15 more"
    assert page.locator(".alarm-item .alarm-name").first.inner_text() == "Site 0960"  # critical, 08:24
    page.locator(".alarm-item").first.click()
    page.locator("#inspector-title", has_text="Site 0960").wait_for()
    page.keyboard.press("Escape")

    # During a search a row for a site outside it goes back to the whole map and opens it (#337).
    nearby = [{**sites[1], "distance_km": 0.0}]
    page.route(
        "**/api/search*",
        lambda route: route.fulfill(json={"search_lat": 40, "search_lon": -5, "count": 1, "locations": nearby}),
    )
    page.fill("#search-input", "40,-5")
    page.press("#search-input", "Enter")
    page.locator("#search-results-header", has_text="1 location within 5 km").wait_for()
    page.locator(".alarm-item").first.click()
    page.locator("#inspector-title", has_text="Site 0960").wait_for()
    assert page.locator("#search-results").get_attribute("class") == "hidden"
    page.keyboard.press("Escape")

    # Alarms only: the 25 pins and nothing else.
    page.locator("#alarms-only").click()
    assert page.locator(".leaflet-marker-icon").count() == 25
    assert "25 locations in alarm" in page.locator("#location-count").inner_text()
    page.locator("#clear-filters").click()
    assert page.locator(".leaflet-marker-icon:has(.quiet-cluster)").count() > 0

    # A quiet site inside a cluster opens from a link: the map zooms to it.
    page.goto(f"{BASE_URL}/?location_id=s613")
    page.locator("#inspector-title", has_text="Site 0613").wait_for(timeout=20000)
