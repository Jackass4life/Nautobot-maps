"""The map's calmer left panel (#392): one search, filters folded with chips,
and one top bar for every page."""

from tests import test_integration
from tests.js_modules import run_js

SITES = """
const sites = [
  { id: "c1", name: "Copenhagen Office 01", ancestor_path: "EMEA › DNK", physical_address: "Roersvej 33, 5000 Odense C" },
  { id: "c2", name: "Copenhagen DC 02", ancestor_path: "EMEA › DNK", physical_address: "" },
  { id: "o1", name: "Oslo Office", ancestor_path: "EMEA › NOR", physical_address: "Karl Johans gate 14, Oslo" },
  { id: "x<1", name: "Bank <HQ>", ancestor_path: "", physical_address: "Main Street 1, Copenhagen" },
];
"""


def test_site_search_names_first_then_path_and_address():
    run_js(
        SITES
        + """
import { matchSites, siteMatchesHtml } from "@js/map/sitesearch.js";
const names = (query) => matchSites(sites, query).matches.map((loc) => loc.name);
// Every word must match; names first, then path or address.
check(names("copenhagen").join() === "Copenhagen DC 02,Copenhagen Office 01,Bank <HQ>", names("copenhagen").join());
check(names("copenhagen office").join() === "Copenhagen Office 01", "every word");
check(names("odense").join() === "Copenhagen Office 01", "by address");
check(names("dnk").length === 2, "by path");
check(names("nowhere").length === 0, "none");
// Coordinates are the map's search, not a site name.
check(matchSites(sites, "55.6761, 12.5683").matches.length === 0, "coordinates");
check(matchSites(sites, "   ").matches.length === 0, "blank");
// Eight at most, and how many more.
const many = Array.from({ length: 30 }, (_, i) => ({ id: `s${i}`, name: `Site ${i}` }));
const { matches, more } = matchSites(many, "site");
check(matches.length === 8 && more === 22, `${matches.length} + ${more}`);

const html = siteMatchesHtml(matchSites(sites, "copenhagen"), "copenhagen", { c1: "critical" });
check(html.includes('data-location-id="c1"') && html.includes("dot-critical"), "its level's dot");
check(html.includes("dot-no-data"), "unknown level: no data");
check(html.includes("Bank &lt;HQ&gt;") && html.includes('data-location-id="x&lt;1"') && !html.includes("<HQ>"), "escaped");
check(html.includes("Search the map for “copenhagen”"), "the map search");
check(siteMatchesHtml({ matches: many.slice(0, 8), more: 22 }, "site").includes("22 more: keep typing"), "more");
check(siteMatchesHtml({ matches: [], more: 0 }, "<b>").includes("for “&lt;b&gt;”"), "query escaped");
"""
    )


def test_site_search_default_enter_target_at_scale():
    run_js(
        SITES
        + """
import { matchSites, siteMatchesHtml } from "@js/map/sitesearch.js";
sites.push({ id: "cph", name: "CPH-01", physical_address: "Main Street 2, Copenhagen" });
sites.push(...Array.from({ length: 1200 }, (_, i) => ({
  id: `s${i}`, name: `Site ${i}`, ancestor_path: "Denmark",
  physical_address: "Main Street 3, Copenhagen",
})));
// Enter uses the first button, just like ArrowDown from the input.
const firstButton = (query) => {
  const result = matchSites(sites, query);
  return siteMatchesHtml(result, query).split("</button>")[0];
};
check(firstButton("  cPh-01  ").includes('data-location-id="cph"'), "full name opens the site");
check(firstButton("Copenhagen DC 02").includes('data-location-id="c2"'), "full name first");
for (const query of ["Copenhagen", "Main Street", "Denmark", "55.6761, 12.5683", "nowhere"]) {
  check(firstButton(query).includes("site-match-map"), `${query}: Enter searches the map`);
}
const city = matchSites(sites, "Copenhagen");
check(city.matches.length === 8 && city.more === 1196, "address suggestions stay capped at scale");
const html = siteMatchesHtml(city, "Copenhagen");
check(html.indexOf("site-match-map") < html.indexOf("data-location-id"), "map before suggestions");
check(html.includes('data-location-id="c2"'), "partial names remain selectable");
const exact = siteMatchesHtml(matchSites(sites, "CPH-01"), "CPH-01");
check(exact.indexOf("data-location-id") < exact.indexOf("site-match-map"), "name before map");
"""
    )


def test_filters_folded_with_chips_and_empty_lists_hidden():
    run_js(
        test_integration.MAP_FAKES
        + test_integration.TestMapAlarmListInTheBrowser.SETUP
        + """
import { populateFilters, renderFilterChips } from "@js/map/filters.js";
els.filterChips = element("filterChips", { replaceChildren(...chips) { this.children = chips; } });
els.filterCount = element("filterCount");
const quick = ["", "Active", "Planned"].map((status) => element(`quick-${status}`, { dataset: { quickStatus: status } }));
els.quickStatusButtons = quick;
for (const loc of state.allLocations) loc.status = "Active";
populateFilters(state.allLocations);
// Two tenants: a list to choose from; one status, no parents: nothing to choose.
check(!els.filterTenant.hidden && els.filterParent.hidden, "lists with one value or none are hidden");
check(quick.every((button) => button.hidden), "one status: no status buttons");
state.allLocations[0].status = "Planned";
populateFilters(state.allLocations);
check(!quick[0].hidden && !quick[1].hidden && !quick[2].hidden, "Active and Planned: the buttons");
state.allLocations[0].status = "Retired";
populateFilters(state.allLocations);
check(!quick[1].hidden && quick[2].hidden, "no site Planned: no Planned button");

applyFilters();
check(els.filterCount.textContent === "" && els.filterChips.hidden, "no filter, no chip");
els.filterTenant.value = "Odd";
applyFilters();
check(els.filterCount.textContent === " · 1", els.filterCount.textContent);
const [chip] = els.filterChips.children;
check(chip.textContent === "Odd ×" && chip.dataset.filter === "filterTenant", chip.textContent);
check(chip.attrs["aria-label"] === "Remove filter Tenant: Odd", chip.attrs["aria-label"]);
check(els.locationCount.textContent === "20 sites", els.locationCount.textContent);

// A site picked from the search that the filters hid: every site for now,
// and a saved Alarms only stays saved (#392).
let saved = "1";
setGlobal("localStorage", { getItem: () => saved, setItem: (_k, v) => { saved = v; }, removeItem: () => { saved = null; } });
setAlarmsOnly(true, { remember: false });
clearFilters({ remember: false });
check(!state.alarmsOnly && saved === "1" && els.filterTenant.value === "", "cleared for now, still saved");
clearFilters();
check(saved === null, "Clear filters itself forgets it");
"""
    )


def test_one_top_bar_on_every_page(client):
    for path, active in (
        ("/", "/"),
        ("/alerts", "/alerts"),
        ("/availability", "/availability"),
        ("/maintenance", "/maintenance"),
        ("/circuits", "/circuits"),
        ("/docs", "/docs"),
    ):
        html = client.get(path).get_data(as_text=True)
        assert html.count('class="app-menu"') == 1, path
        assert 'id="theme-toggle"' in html and 'href="/static/css/nav.css' in html, path
        assert f'aria-current="page" href="{active}"' in html, path
        assert html.count('aria-current="page"') >= 1, path


def test_the_map_and_board_keep_a_links_scope(client):
    html = client.get("/?tenant=Acme%20Corp").get_data(as_text=True)
    assert 'href="/alerts?tenant=Acme+Corp"' in html
    assert 'href="/alerts?view=wall&amp;tenant=Acme+Corp"' in html


def test_the_theme_button_is_an_icon_with_a_label():
    run_js(
        """
import { applyTheme } from "@js/lib/theme.js";
const button = element("theme", { textContent: "keep" });
applyTheme("dark", button);
check(button.attrs["aria-label"] === "Switch to light mode" && button.title === "Light mode", button.title);
check(button.textContent === "keep", "the icon stays");
applyTheme("light", button);
check(button.attrs["aria-label"] === "Switch to dark mode" && button.title === "Dark mode", button.title);
"""
    )


PANEL_PAGE = """
import { els, state, locationAlerts, locationAlertInfo } from "@js/map/state.js";
const store = new Map();
setGlobal("localStorage", {
  getItem: (key) => store.get(key) ?? null,
  setItem: (key, value) => store.set(key, String(value)),
  removeItem: (key) => store.delete(key),
});
setGlobal("window", { requestAnimationFrame: (fn) => fn() });
const resized = [];
state.map = { invalidateSize: (options) => resized.push(options) };
document.addEventListener = () => {};
const inside = element("alarm-item");
els.sidebar = element("sidebar", { inert: false, contains: (node) => node === inside });
els.panelToggle = element("panel-toggle");
"""


def test_the_panel_folds_away_and_is_remembered():
    """#420: « hides the panel, the map is measured again keeping its centre,
    and the choice is remembered; focus inside the panel moves to the toggle."""
    run_js(
        PANEL_PAGE
        + """
import { initPanelToggle, panelToggleLabel, setPanelHidden } from "@js/map/sidebar.js";
check(panelToggleLabel(false, 23).text === "«", "open: «");
check(panelToggleLabel(true, null).text === "»", "folded, levels not in: »");
check(panelToggleLabel(true, 0).text === "»", "folded, nothing in alarm: »");
check(panelToggleLabel(true, 1234).text === "» 1,234 in alarm", panelToggleLabel(true, 1234).text);
check(panelToggleLabel(true, 23).label === "Show the panel: 23 in alarm", "its label says so");

initPanelToggle();
check(!document.body.classList.contains("panel-hidden") && !els.sidebar.inert, "open by default");
document.activeElement = inside;
setPanelHidden(true);
check(document.body.classList.contains("panel-hidden"), "folded");
check(els.sidebar.inert, "out of the tab order");
check(focused.at(-1) === "panel-toggle", "focus moves to the toggle");
check(els.panelToggle.attrs["aria-expanded"] === "false", "aria-expanded");
check(store.get("nautobot-maps-map-panel-hidden") === "1", "remembered");
check(resized.length === 2 && resized[1].animate === false && resized[1].pan !== false, "measured again, same centre");

// A new page opens it folded.
document.body.classList.remove("panel-hidden");
initPanelToggle();
check(document.body.classList.contains("panel-hidden"), "restored");
setPanelHidden(false);
check(!store.has("nautobot-maps-map-panel-hidden") && !els.sidebar.inert, "open again, forgotten");
"""
    )


def test_the_panel_opens_when_storage_is_blocked():
    run_js(
        PANEL_PAGE
        + """
import { initPanelToggle, setPanelHidden } from "@js/map/sidebar.js";
const blocked = () => { throw new Error("SecurityError"); };
setGlobal("localStorage", { getItem: blocked, setItem: blocked, removeItem: blocked });
initPanelToggle();
check(!document.body.classList.contains("panel-hidden"), "open");
setPanelHidden(true);
check(document.body.classList.contains("panel-hidden"), "still folds for this page");
"""
    )


def test_the_panel_shortcut_is_not_typing():
    run_js(
        """
import { isPanelShortcut } from "@js/map/sidebar.js";
const on = (target, extra = {}) => isPanelShortcut({ key: "[", target, ...extra });
check(on(element("map")), "[ on the page");
check(!on(element("input", { closest: (sel) => sel.includes("input") ? {} : null })), "typing in a field");
check(!on(element("map"), { ctrlKey: true }), "with a modifier");
check(!isPanelShortcut({ key: "]", target: element("map") }), "another key");
"""
    )


def test_a_folded_panel_shows_the_alarm_count_at_scale():
    """#420: folding the panel never hides that something is wrong: » shows
    the alarm list's count (40 sites in alarm of 1,200)."""
    run_js(
        PANEL_PAGE
        + """
import { renderAlarmList } from "@js/map/alarms.js";
import { setPanelHidden } from "@js/map/sidebar.js";
Object.assign(els, {
  alarmList: element("alarm-list"), alarmCount: element("alarm-count"),
  alarmMore: element("alarm-more"), alarmBreakdown: element("alarm-breakdown"),
});
state.allLocations = Array.from({ length: 1200 }, (_, i) => ({ id: `s${i}`, name: `Site ${i}` }));
state.filteredLocations = state.allLocations;
setPanelHidden(true);
renderAlarmList();
check(els.panelToggle.textContent === "»", "levels not in yet: no count");
state.levelsLoaded = true;
state.allLocations.slice(0, 40).forEach((loc, i) => {
  locationAlerts[loc.id] = i < 30 ? "critical" : "low";
  locationAlertInfo[loc.id] = { level: locationAlerts[loc.id], down: 3, monitored: 8 };
});
renderAlarmList();
check(els.panelToggle.textContent === "» 40 in alarm", els.panelToggle.textContent);
setPanelHidden(false);
check(els.panelToggle.textContent === "«", "open: the panel shows the count itself");
"""
    )
