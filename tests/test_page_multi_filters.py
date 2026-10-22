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


def test_availability_multi_tenants_at_scale_and_clear():
    run_js("""
import { rowFrom, viewRows } from "@js/availability/table.js";
const rows = Array.from({ length: 1200 }, (_, i) => rowFrom({
  dataset: { name: `Site ${i}`, availability: String(i % 100),
    downtime: String(1200 - i), tenants: JSON.stringify([`Tenant ${i % 6}`, "Shared"]) },
}));
const view = viewRows(rows, { tenant: ["Tenant 1", "Tenant 3"], limit: 25 });
check(view.matched === 400 && view.shown.length === 25 && view.rest.length === 375, "OR picks, limited rows");
check(viewRows(rows, { tenant: ["Tenant 1", "Shared"] }).matched === 1200, "secondary tenant, no duplicates");
check(viewRows(rows, { tenant: ["Tenant 1", "Tenant 3"], query: "site 11" }).shown
  .every((row) => row.name.includes("11") && (row.tenants.includes("Tenant 1") || row.tenants.includes("Tenant 3"))),
  "query AND tenants");
check(viewRows(rows, { tenant: [] }).matched === 1200, "clear restores all rows");
check(viewRows(rows, { tenant: ["Tenant"] }).matched === 0, "exact tenant match");
""")


def test_maintenance_multi_tenants_at_scale_and_clear():
    run_js("""
import { batchesOf, batchMatches, groupsHtml } from "@js/maintenance/list.js";
const batches = batchesOf(Array.from({ length: 1200 }, (_, i) => ({
  id: i, batch_id: `batch-${i}`, site_id: `site-${i}`, site_name: `Site ${i}`,
  state: "upcoming", tenants: [`Tenant ${i % 6}`, "Shared"], reason: i % 2 ? "Core" : "Edge",
  starts_at: "2026-10-22T10:00:00Z", ends_at: "2026-10-22T11:00:00Z",
})));
const shown = batches.filter((batch) => batchMatches(batch, { tenant: ["Tenant 1", "Tenant 3"] }));
check(shown.length === 400, "either tenant");
check(batches.filter((batch) => batchMatches(batch, { tenant: ["Tenant 1", "Shared"] })).length === 1200,
  "secondary tenant, no duplicate batches");
check(shown.every((batch) => batchMatches(batch, { words: ["core"], tenant: ["Tenant 1", "Tenant 3"] })),
  "search AND tenants");
check(batches.filter((batch) => batchMatches(batch, { tenant: [], words: [] })).length === 1200, "clear");
check(!batchMatches(shown[0], { tenant: ["Tenant"], words: [] }), "exact tenant match");
const html = groupsHtml(shown, { limits: {}, expanded: new Set(), descriptions: {} });
check((html.match(/class="maint-batch"/g) || []).length === 25 && html.includes("375 more"), "limited filtered groups");
""")
