"""Board, Availability and Maintenance use OR picks and AND filter fields."""

from tests.js_modules import run_js

SELECT = """
function select(values = []) {
  const node = element("select", {
    appendChild(option) { this.options.push(option); return option; },
  });
  Object.defineProperty(node, "selectedOptions", { get() { return this.options.filter((o) => o.selected); } });
  Object.defineProperty(node, "textContent", { set() { this.options = []; } });
  node.options = values.map((value) => ({ value, selected: false }));
  return node;
}
"""


def test_board_multi_filters_at_scale_and_refresh():
    run_js(
        """
import { filterSites, getFilteredAlerts, populateFilters, currentWallLink, filtersInUse,
  moreFiltersInUse, filtersLeftOffTheWall } from "@js/alerts/filters.js";
import { selectedValues, setSelectedValues } from "@js/lib/multi-select.js";
import { inScope, readScope } from "@js/lib/scope.js";
import { config, els, state, DEFAULT_SEVERITY_FILTER, DEFAULT_SORT } from "@js/alerts/state.js";
"""
        + SELECT
        + """
const sites = Array.from({ length: 1200 }, (_, i) => ({
  id: String(i), name: `Site ${i}`, country: i % 2 ? "NOR" : "DNK",
  status: ["Active", "Planned", "Retired"][i % 3],
  location_type: ["Office", "Datacenter", "Warehouse", "Exchange"][i % 4],
  tenants: [`Tenant ${i % 5}`, "Shared"], alert_level: "critical",
}));
const criteria = {
  site: "DNK", status: ["Active", "Planned"], type: ["Office", "Warehouse"],
  tenant: ["Tenant 1", "Tenant 2"], severityFilter: "all", sort: "site",
};
const expected = sites.filter((s) => s.country === "DNK"
  && ["Active", "Planned"].includes(s.status)
  && ["Office", "Warehouse"].includes(s.location_type)
  && s.tenants.some((t) => ["Tenant 1", "Tenant 2"].includes(t)));
check(expected.length > 100, "scale fixture");
check(filterSites(sites, criteria).length === expected.length, "OR within, AND across fields");
check(filterSites(sites, { ...criteria, status: [], type: [], tenant: [], site: "" }).length === 1200, "empty is all");
check(filterSites(sites, { ...criteria, tenant: ["Shared", "Tenant 1"] }).length
  === sites.filter((s) => s.country === "DNK" && s.status !== "Retired").length, "multi-tenant OR without duplicates");
Object.assign(els, {
  filterStatus: select(), filterType: select(), filterTenant: select(),
  filterSite: element("search", { value: "DNK" }), sortBy: element("sort", { value: DEFAULT_SORT }),
  toggleNonOperational: element("toggle"),
});
state.allAlerts = sites;
state.severityFilter = "all";
config.wallView = false;
populateFilters(sites);
setSelectedValues(els.filterStatus, criteria.status);
setSelectedValues(els.filterType, criteria.type);
setSelectedValues(els.filterTenant, criteria.tenant);
check(getFilteredAlerts().length === expected.length, "page reads every selected option");
check(moreFiltersInUse() === 2 && filtersInUse(), "filter indicators count fields");
check(filtersLeftOffTheWall().join() === "Status,Type", "wall omissions");
populateFilters(sites.map((s) => ({ ...s })));
check(getFilteredAlerts().length === expected.length, "all picks survive refresh");
populateFilters(sites.filter((s) => s.status !== "Planned" && !s.tenants.includes("Tenant 2")));
check(selectedValues(els.filterStatus).join() === "Active", "invalid status removed");
check(selectedValues(els.filterTenant).join() === "Tenant 1", "invalid tenant removed");

config.scope = readScope("?tenant=Shared&q=DNK&q=Site");
setSelectedValues(els.filterTenant, ["Shared", "Tenant 1"]);
els.filterSite.value = "  10  ";
const link = new URL(currentWallLink(), "https://example.test");
check(link.searchParams.getAll("tenant").sort().join() === "Shared,Tenant 1", "wall contains every tenant pick");
check(link.searchParams.getAll("q").join() === "DNK,Site,10", "incoming text scope stays AND");
check(inScope(sites[0], config.scope) && !inScope(sites[1], config.scope), "incoming scope remains unchanged");
for (const input of [els.filterStatus, els.filterType, els.filterTenant]) setSelectedValues(input, []);
els.filterSite.value = "";
state.severityFilter = DEFAULT_SEVERITY_FILTER;
check(!filtersInUse() && moreFiltersInUse() === 0, "reset clears every pick");
check(new URL(currentWallLink(), "https://example.test").searchParams.getAll("tenant").join() === "Shared",
  "reset preserves incoming scope");
"""
    )
