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
