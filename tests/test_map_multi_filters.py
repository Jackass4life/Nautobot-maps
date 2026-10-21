"""Multi-value map filters and the staff links they produce (#412)."""

from tests import test_integration
from tests.js_modules import run_js


def test_map_multi_filters_at_scale():
    run_js(
        """
import { filterLocations } from "@js/map/filters.js";
const sites = Array.from({length: 1200}, (_, i) => ({
  id: i, status: ["Active", "Planned", "Retired"][i % 3],
  location_type: ["Office", "DC", "Store"][i % 3],
  parent: `Parent ${i % 300}`, tenant_group: `Group ${i % 3}`,
  tenant: `Tenant ${i % 500}`, tenants: [`Tenant ${i % 500}`, `Partner ${i % 7}`],
}));
const fields = ["status", "location_type", "parent", "tenant_group", "tenant"];
for (const field of fields) {
  const picks = [sites[0][field], sites[1][field]];
  const filtered = filterLocations(sites, {[field]: picks});
  check(filtered.length > 0 && filtered.length < sites.length, field);
  check(filtered.every((site) => picks.includes(site[field])), `any selected ${field}`);
  check(filtered.some((site) => site[field] === picks[0]), `first ${field}`);
  check(filtered.some((site) => site[field] === picks[1]), `second ${field}`);
}
const criteria = {status: ["Active", "Planned"], location_type: ["Office", "Store"],
  parent: ["Parent 0", "Parent 3"], tenant_group: ["Group 0", "Group 1"]};
const filtered = filterLocations(sites, criteria);
check(filtered.length === 8, "AND across filters");
check(filterLocations(sites, {tenant: ["Partner 0", "Partner 1"]}).length === 344,
  "relationship-linked tenants match too");
check(filterLocations(sites, {status: [], tenant: []}).length === 1200, "empty is all");
"""
    )


def test_map_chips_remove_one_value_and_clear_all():
    run_js(
        test_integration.MAP_FAKES
        + test_integration.TestMapAlarmListInTheBrowser.SETUP
        + """
import { activeFilters, renderFilterChips } from "@js/map/filters.js";
import { selectedValues, setSelectedValues } from "@js/lib/multi-select.js";
els.filterTenant.options = [
  { value: "", selected: false },
  { value: "Odd", selected: true },
  { value: "Even", selected: true },
];
Object.defineProperty(els.filterTenant, "selectedOptions", {
  get() { return this.options.filter((option) => option.selected); },
});
els.filterChips = element("chips", { replaceChildren(...chips) { this.children = chips; } });
els.filterCount = element("count");
renderFilterChips();
check(activeFilters().length === 2, "one chip for each pick");
check(els.filterChips.children.map((chip) => chip.dataset.value).join() === "Odd,Even",
  "chip identifies the value, not just the filter");
setSelectedValues(els.filterTenant, selectedValues(els.filterTenant).filter((value) => value !== "Odd"));
check(selectedValues(els.filterTenant).join() === "Even", "removing one keeps the other");
clearFilters();
check(selectedValues(els.filterTenant).length === 0, "clear removes all");
"""
    )


def test_staff_navigation_links_keep_all_tenant_picks():
    run_js(
        """
import { readScope, updateTenantLinks } from "@js/lib/scope.js";
const links = ["/", "/alerts", "/alerts?view=wall", "/availability"].map((href) =>
  element("link", { attrs: { href } }));
document.querySelectorAll = () => links;
const scope = readScope("?tenant=Original&q=DNK");
updateTenantLinks(["Acme & Co", "Nordic Net"], scope);
for (const link of links.slice(0, 3)) {
  const url = new URL(link.attrs.href, "http://localhost");
  check(url.searchParams.getAll("tenant").join() === "Acme & Co,Nordic Net", "every pick");
  check(url.searchParams.get("q") === "DNK", "original text scope");
}
check(new URL(links[2].attrs.href, "http://localhost").searchParams.get("view") === "wall", "wall");
check(links[3].attrs.href === "/availability", "other pages unchanged");
updateTenantLinks([], scope);
check(links[0].attrs.href === "/?tenant=Original&q=DNK", "clearing restores incoming scope");
"""
    )
