"""End-to-end tests in Chromium (#294): the real app, the mock Nautobot and PostgreSQL.

Run against a running app: ``E2E_BASE_URL=http://127.0.0.1:5055 python -m pytest e2e/``
(CI starts the mock, PostgreSQL and the app first; see the e2e job).
"""

import csv
import io
import json
import os
import time
import urllib.parse
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
    # Activity for the same sites (#369).
    # (the panel may be collapsed at this width: its title, not its visibility)
    page.locator("#feed-title", has_text="Activity · “Etage”").wait_for(state="attached")
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


def test_the_map_lists_sites_with_missing_coordinates(page):
    """#368: the demo's Danish campus and floor have devices but no coordinates."""
    page.goto(f"{BASE_URL}/")
    page.locator("#coordinate-note", has_text="aren't on the map").wait_for(timeout=20000)
    page.locator("#coordinate-toggle").click()
    names = page.locator(".coordinate-site").all_inner_texts()
    assert any("Etage 2" in name for name in names), names
    assert page.locator(".coordinate-csv").get_attribute("href") == "/api/locations/coordinate-check.csv"


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
            "physical_address": "Main Street 3, Copenhagen",
        }
        for i in range(1200)
    ]
    sites[-1]["name"] = "Copenhagen DC"
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
    page.locator("#location-count", has_text="1,200 sites").wait_for(timeout=20000)
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
    for level in ("critical", "medium", "low"):
        assert (
            page.locator(
                f".leaflet-marker-icon:has(.quiet-cluster.level-bg-{level}, .quiet-dot.level-bg-{level})"
            ).count()
            == 0
        ), level

    # The sidebar lists them: worst first, then newest; eight and "Show all 25" (#334, #392).
    assert page.locator("#alarm-count").inner_text() == "25 in alarm"
    assert page.locator("#alarm-breakdown").inner_text() == "9 critical · 8 medium · 8 low"
    assert page.locator(".alarm-item").count() == 8
    assert page.locator("#alarm-more").inner_text() == "Show all 25"
    assert page.locator(".alarm-item .alarm-name").first.inner_text() == "Site 0960"  # critical, 08:24
    page.locator(".alarm-item").first.click()
    page.locator("#inspector-title", has_text="Site 0960").wait_for()
    page.keyboard.press("Escape")

    # During a search a row for a site outside it goes back to the whole map and opens it (#337).
    nearby = [{**sites[1], "distance_km": 0.0}]
    searches = []
    page.on("request", lambda request: searches.append(request.url) if "/api/search?" in request.url else None)
    page.route(
        "**/api/search*",
        lambda route: route.fulfill(json={"search_lat": 40, "search_lon": -5, "count": 1, "locations": nearby}),
    )
    page.fill("#search-input", "40,-5")
    page.press("#search-input", "Enter")
    page.locator("#search-results-header", has_text="1 location within 5 km").wait_for()
    assert len(searches) == 1
    # A city also in a site's name, and an address-only match: Enter searches
    # nearby, not the first suggested site (#411).
    for count, query in enumerate(("Copenhagen", "Main Street"), start=2):
        page.fill("#search-input", query)
        buttons = page.locator("#site-matches button")
        assert "site-match-map" in buttons.first.get_attribute("class")
        assert page.locator("#site-matches [data-location-id]").count() == 8
        assert len(searches) == count - 1, "typing stays local"
        # Address matches remain reachable from the first (map) row.
        page.press("#search-input", "ArrowDown")
        assert buttons.first.evaluate("el => el === document.activeElement")
        page.keyboard.press("ArrowDown")
        assert buttons.nth(1).evaluate("el => el === document.activeElement")
        page.focus("#search-input")
        with page.expect_response("**/api/search?*"):
            page.press("#search-input", "Enter")
        assert len(searches) == count
        page.locator("#search-results-header", has_text="1 location within 5 km").wait_for()
        assert page.locator("#search-results").is_visible()
        assert page.locator(".result-distance").inner_text() == "0 km"
        assert page.locator(".search-point-marker").count() == 1
        assert page.locator(".leaflet-overlay-pane canvas").is_visible()
        assert page.evaluate(
            """async () => {
                const { state } = await import("/static/js/map/state.js");
                return state.markerLayer.getLayers().filter(layer => layer instanceof L.Circle)
                    .map(circle => circle.getRadius());
            }"""
        ) == [5000]
        assert page.locator("#location-inspector").is_hidden()
    page.locator(".alarm-item").first.click()
    page.locator("#inspector-title", has_text="Site 0960").wait_for()
    assert page.locator("#search-results").get_attribute("class") == "hidden"
    page.keyboard.press("Escape")

    # Alarms only: the 25 pins and nothing else.
    page.locator("#alarms-only").click()
    assert page.locator(".leaflet-marker-icon").count() == 25
    assert page.locator("#location-count").inner_text() == "25 sites in alarm"
    page.locator("#filters-toggle").click()
    page.locator("#clear-filters").click()
    assert page.locator(".leaflet-marker-icon:has(.quiet-cluster)").count() > 0

    # A quiet site inside a cluster opens from a link: the map zooms to it.
    page.goto(f"{BASE_URL}/?location_id=s613")
    page.locator("#inspector-title", has_text="Site 0613").wait_for(timeout=20000)


def test_a_click_on_a_tenant_info_keeps_its_description_open(page):
    """#390: the tenant's (i) in the site panel stays open after a click."""
    page.goto(f"{BASE_URL}/?location_id=loc-lon")
    page.locator("#inspector-title", has_text="London HQ").wait_for(timeout=20000)
    tip = page.locator(".inspector-row .info-tip").first
    tip.click()
    page.mouse.move(1, 1)  # the pointer leaves the (i)
    box = page.locator(".info-tip-box")
    assert box.is_visible()
    assert "Retail bank" in box.inner_text()
    # On top, not behind the site panel: what is under its middle is the box.
    rect = box.bounding_box()
    middle = (rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
    on_top = page.evaluate("([x, y]) => document.elementFromPoint(x, y)?.className || ''", list(middle))
    assert "info-tip-box" in on_top, on_top
    page.mouse.click(*middle)  # a click in it keeps it, so its text can be selected
    assert box.is_visible()
    page.keyboard.press("Escape")  # closes the tip, not the panel
    assert not box.is_visible()
    assert page.locator("#location-inspector:not(.hidden)").count() == 1


def test_the_site_panel_shows_the_address_and_no_country(page):
    """#386: from addresses the Country row showed a town; it is gone."""
    page.goto(f"{BASE_URL}/?location_id=loc-cph")
    page.locator("#inspector-title", has_text="Copenhagen DC").wait_for(timeout=20000)
    labels = page.locator(".inspector-row-label").all_inner_texts()
    assert "Address" in labels
    assert "Country" not in labels


def test_the_calm_panel_search_filters_legend_and_menu(page):
    """#392: one search (sites as you type, then the map), filters folded with
    chips, the legend on the map, and the pages in one menu."""
    page.goto(f"{BASE_URL}/")
    page.locator("#alarm-count", has_text="in alarm").wait_for(timeout=20000)
    # Nothing to scroll in the first screen.
    assert page.evaluate("document.querySelector('#sidebar').scrollHeight <= innerHeight")

    # The sites as you type; a click opens one.
    page.fill("#search-input", "london h")
    page.locator(".site-match", has_text="London HQ").click()
    page.locator("#inspector-title", has_text="London HQ").wait_for()
    assert page.locator("#site-matches").is_hidden()
    page.keyboard.press("Escape")
    # Enter opens a full site-name match.
    page.fill("#search-input", "copenhagen dc")
    page.locator(".site-match[data-location-id]", has_text="Copenhagen DC").wait_for()
    page.press("#search-input", "Enter")
    page.locator("#inspector-title", has_text="Copenhagen DC").wait_for()

    # The menu opens over the site panel, every link reachable; Escape closes the menu, not the panel.
    page.locator(".app-menu summary").click()
    wall = page.locator(".app-menu .app-nav-link", has_text="Wall view")
    box = wall.bounding_box()
    middle = [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2]
    assert page.evaluate("([x, y]) => document.elementFromPoint(x, y)?.textContent", middle) == "Wall view"
    page.keyboard.press("Escape")
    assert page.locator(".app-menu .app-nav").is_hidden()
    assert page.locator("#location-inspector:not(.hidden)").count() == 1
    page.keyboard.press("Escape")
    # A city makes the map search the first row.
    page.fill("#search-input", "Copenhagen")
    page.locator(".site-match-map", has_text="Search the map for “Copenhagen”").wait_for()

    # Filters are folded; one in use shows as a chip that removes it.
    page.fill("#search-input", "")
    assert page.locator("#filter-section").is_hidden()
    page.locator("#filters-toggle").click()
    page.get_by_role("combobox", name="Location type", exact=True).click()
    page.get_by_role("combobox", name="Search Location type", exact=True).fill("Data Center")
    page.get_by_role("option", name="Data Center", exact=True).click()
    page.keyboard.press("Escape")
    assert page.locator("#filters-toggle").inner_text() == "Filters · 1"
    page.locator(".filter-chip", has_text="Data Center").click()
    assert page.locator("#filters-toggle").inner_text() == "Filters"

    # The legend is on the map, behind its button.
    assert page.locator("#legend").is_hidden()
    page.locator("#legend-toggle").click()
    page.locator("#legend", has_text="Critical").wait_for()

    # Every page is in the menu, which closes on a click outside it.
    page.locator(".app-menu summary").click()
    links = page.locator(".app-menu .app-nav-link").all_inner_texts()
    assert links == ["Map", "Alert Board", "Availability", "Maintenance", "Circuits", "API", "Wall view"], links
    page.mouse.click(700, 300)
    assert page.locator(".app-menu .app-nav").is_hidden()


@pytest.mark.parametrize("path", ["/", "/alerts"])
def test_find_and_pick_two_tenants(page, path):
    """The real control searches locally, keeps both picks, and scopes staff links."""
    endpoint = "/api/locations" if path == "/" else "/api/alerts"
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(f"{BASE_URL}{path}")
    if path == "/":
        page.locator("#loading-overlay").wait_for(state="hidden", timeout=30000)
        page.locator("#filters-toggle").click()
    else:
        page.wait_for_selector(".site-row")
        page.locator('[data-severity-filter="all"]').click()
    control = page.get_by_role("combobox", name="Tenant", exact=True)
    control.click()
    search = page.get_by_role("combobox", name="Search Tenant", exact=True)
    before = sum(endpoint in url for url in requests)
    search.fill("acm")
    assert page.get_by_role("option").all_text_contents() == ["Acme Corp"]
    search.press("Enter")
    search.fill("nord")
    search.press("Enter")
    search.press("Escape")
    assert control.inner_text() == "Acme Corp +1"
    assert control.get_attribute("aria-expanded") == "false"
    assert sum(endpoint in url for url in requests) == before, "typing and picks make no requests"

    payload = api(endpoint)
    sites = payload["locations" if path == "/" else "alerts"]
    expected = [
        site for site in sites if {"Acme Corp", "Nordic Net"}.intersection(site.get("tenants") or [site.get("tenant")])
    ]
    if path == "/":
        assert page.locator("#location-count").inner_text() == f"{len(expected):,} sites"
        assert page.locator(".filter-chip").count() == 2
        page.locator(".filter-chip", has_text="Acme Corp").click()
        assert control.inner_text() == "Nordic Net"
        assert page.locator(".filter-chip").count() == 1
        # Put the second pick back before checking the outgoing links.
        control.click()
        search.fill("acm")
        search.press("Enter")
        search.press("Escape")
    else:
        names = page.locator(".site-row .site-name").all_inner_texts()
        assert sorted(names) == sorted(site["name"] for site in expected)
    page.locator(".app-menu summary").click()
    href = page.locator(".app-nav-link", has_text="Alert Board").get_attribute("href")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    assert set(params["tenant"]) == {"Acme Corp", "Nordic Net"}


def test_the_availability_page_explains_itself(page):
    """A click on an (i) says what the page or a figure means, for someone new to it."""
    page.goto(f"{BASE_URL}/availability")
    page.wait_for_selector(".avail-card")
    assert page.locator(".help-tip").count() == 9
    page.locator("h1 .help-tip").click()
    page.mouse.move(1, 1)
    box = page.locator(".info-tip-box")
    assert "How much of the time each site and tenant was up" in box.inner_text()
    page.keyboard.press("Escape")
    assert box.is_hidden()


def test_the_calm_site_panel(page):
    """#394: what is wrong first (one status line, the down devices, core first),
    the rest folded with counts; a device opens in place."""
    page.goto(f"{BASE_URL}/?location_id=loc-lon")
    page.locator("#inspector-title", has_text="London HQ").wait_for(timeout=20000)
    page.locator(".site-status-line", has_text="Medium · 2 of 7 down").wait_for()
    # Two sites at one address: the tabs, and the header says so.
    assert "2 locations here" in page.locator("#inspector-subtitle").inner_text()
    assert page.locator(".site-status-reason").inner_text() == "2/7 devices offline (29%)"
    # No pill and banner saying the level twice, no "Location inspector" label.
    assert page.locator(".inspector-kicker, .alert-banner, .inspector-pill").count() == 0
    assert page.locator("#inspector-down-title").inner_text() == "Down · 2"
    down = page.locator(".device-rows[aria-label='Down devices'] .device-row-name").all_inner_texts()
    assert down == ["lon-acc-sw01", "lon-acc-sw02"], down
    # A device opens in place, and closes again.
    first = page.locator(".device-rows[aria-label='Down devices'] .device-row").first
    first.click()
    page.locator(".device-rows[aria-label='Down devices'] .device-details", has_text="Serial").wait_for()
    assert first.get_attribute("aria-expanded") == "true"
    page.locator(".device-rows[aria-label='Down devices'] .device-row").first.click()
    assert page.locator(".device-rows[aria-label='Down devices'] .device-details").count() == 0
    # Folded, with counts; a fold with nothing in it isn't there.
    folds = page.locator(".panel-fold-title").all_inner_texts()
    assert folds[0] == "All devices · 8" and "Details" not in folds, folds
    # The site's basic facts under its name, from the start (#398).
    info = page.locator(".site-info .inspector-row-label").all_inner_texts()
    assert info[:2] == ["Address", "Tenant"], info
    assert "1 Canada Square" in page.locator(".site-info").inner_text()
    assert page.locator(".panel-fold[open]").count() == 0
    # A fold stays open while the panel is drawn again (a device opened).
    page.locator('[data-fold="devices"] > summary').click()
    page.locator(".device-rows[aria-label='Down devices'] .device-row").first.click()
    assert page.locator('[data-fold="devices"][open]').count() == 1

    # A healthy site: one green line, its few devices listed.
    page.goto(f"{BASE_URL}/?location_id=loc-ams")
    page.locator(".site-status-line", has_text="OK · 3 devices").wait_for(timeout=20000)
    assert page.locator('[data-fold="devices"][open] .device-row').count() == 3
    assert page.locator("#inspector-down-title").count() == 0
    assert page.evaluate(
        "(() => { const p = document.querySelector('.inspector-shell'); return p.scrollHeight <= p.clientHeight; })()"
    )


def test_a_stack_is_one_device(page):
    """#400: a virtual chassis whose members have no name is one row, "Stack of 4"."""
    page.goto(f"{BASE_URL}/?location_id=loc-par")
    page.locator("#inspector-title", has_text="Paris PoP").wait_for(timeout=20000)
    fold = page.locator('[data-fold="devices"]')
    assert fold.locator(".panel-fold-title").inner_text() == "All devices · 5 (3 in stacks)"
    # Open by default: a healthy site of ten devices or fewer.
    assert fold.evaluate("el => el.open")
    names = fold.locator(".device-row-name").all_inner_texts()
    assert names == ["par-rt01", "par-stack01"], names
    assert "Unknown" not in fold.inner_text()
    stack = fold.locator(".device-row", has_text="par-stack01")
    assert "Stack of 4" in stack.inner_text()
    stack.click()
    members = fold.locator(".stack-member").all_inner_texts()
    assert [m.split(" · ")[0] for m in members] == ["2. par-stack01:2", "3. par-stack01:3", "4. par-stack01:4"], members


def test_the_map_panel_folds_away(page):
    """#420: « folds the panel and the map takes the room; » brings it back
    with the alarm count; remembered after a reload; `[` toggles it."""
    page.goto(f"{BASE_URL}/")
    page.locator("#alarm-count", has_text="in alarm").wait_for(timeout=20000)
    alarms = page.locator("#alarm-count").inner_text().split()[0]
    width = page.locator("#map").bounding_box()["width"]

    page.get_by_role("button", name="Hide the panel").click()
    toggle = page.locator("#panel-toggle")
    page.wait_for_function("() => document.querySelector('#map').getBoundingClientRect().left === 0")
    assert page.locator("#map").bounding_box()["width"] > width + 300
    assert toggle.inner_text() == f"» {alarms} in alarm"
    assert page.evaluate("document.querySelector('#sidebar').inert")
    # Nothing in the folded panel can be clicked or reached.
    page.wait_for_function("() => document.querySelector('#sidebar').getBoundingClientRect().right <= 0")

    page.reload()
    page.locator("#panel-toggle", has_text="in alarm").wait_for(timeout=20000)
    assert page.evaluate("document.body.classList.contains('panel-hidden')")
    page.keyboard.press("[")
    page.wait_for_function("() => document.querySelector('#sidebar').getBoundingClientRect().left === 0")
    assert toggle.inner_text() == "«"
    # Typing [ in the search doesn't fold it.
    page.fill("#search-input", "[")
    assert not page.evaluate("document.body.classList.contains('panel-hidden')")


def test_the_map_panel_folds_on_a_phone(browser):
    context = browser.new_context(viewport={"width": 390, "height": 800})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{BASE_URL}/")
    page.locator("#alarm-count", has_text="in alarm").wait_for(timeout=20000)
    page.get_by_role("button", name="Hide the panel").click()
    page.wait_for_function("() => document.querySelector('#map').getBoundingClientRect().bottom === innerHeight")
    page.wait_for_function("() => document.querySelector('#sidebar').getBoundingClientRect().top >= innerHeight")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("#panel-toggle").click()
    page.wait_for_function("() => document.querySelector('#sidebar').getBoundingClientRect().bottom <= innerHeight + 1")
    context.close()
    assert errors == [], errors
