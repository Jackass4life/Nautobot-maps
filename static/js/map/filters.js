// The sidebar's filters: which locations the map shows.
import { isAlarm, renderAlarmList } from "./alarms.js";
import { openLocationFromQuery } from "./inspector.js";
import { renderMarkers } from "./markers.js";
import { matchesValues, selectedValues, setFilterOptions, setSelectedValues } from "../lib/multi-select.js";
import { SCOPE_NO_MATCH_HINT, scopeLabel, siteTenants, updateTenantLinks } from "../lib/scope.js";
import { config, els, getLocationById, locationAlerts, state } from "./state.js";

const ALARMS_ONLY_KEY = "nautobot-maps-map-alarms-only";

const FILTERS = [
  ["filterStatus", "status", "All statuses", "Status"],
  ["filterType", "location_type", "All types", "Type"],
  ["filterParent", "parent", "All parents", "Parent"],
  ["filterTenant", "tenant", "All tenants", "Tenant"],
  ["filterTenantGroup", "tenant_group", "All tenant groups", "Tenant group"],
];

// "Only Acme Corp · Show all sites" under the count; a link that matches no
// site (a typo, a renamed tenant) says so instead of an empty map (#367).
// *siteCount*: the sites before the scope.
export function showScope(siteCount) {
  const { mapScope } = els;
  if (!config.scope || !mapScope) return;
  const label = scopeLabel(config.scope, state.allLocations);
  const noMatch = siteCount > 0 && !state.allLocations.length;
  mapScope.classList.toggle("scope-no-match", noMatch);
  // Built whole each time, so it can be shown again either way.
  const strong = document.createElement("strong");
  strong.textContent = label;
  const all = document.createElement("a");
  all.href = "/";
  all.textContent = "Show all sites";
  mapScope.replaceChildren(
    ...(noMatch ? ["No site matches ", strong, `. ${SCOPE_NO_MATCH_HINT} `] : ["Only ", strong, " · "]),
    all,
  );
  mapScope.hidden = false;
}

// A link to a site (?location_id=) the scope leaves out: say why it doesn't open.
export function linkedSiteOutOfScope(everySite) {
  const locId = state.initialLocationId;
  return Boolean(locId) && !getLocationById(locId) && everySite.some((loc) => loc.id === locId);
}

export function populateFilters(locations) {
  for (const [name, field, label] of FILTERS) {
    const values = [...new Set(locations.flatMap((loc) => field === "tenant" ? siteTenants(loc) : [loc[field]]).filter(Boolean))].sort();
    setFilterOptions(els[name], values, label);
  }
  // The status buttons only for statuses some site has, and only with a choice.
  const statuses = new Set(locations.map((loc) => loc.status).filter(Boolean));
  for (const button of els.quickStatusButtons || []) {
    const status = button.dataset.quickStatus || "";
    button.hidden = statuses.size < 2 || (status !== "" && !statuses.has(status));
  }
}

// The filters in use, as [element name, what it is, value] (#392).
export function activeFilters() {
  return FILTERS.flatMap(([name, , , what]) => selectedValues(els[name]).map((value) => [name, what, value]));
}

// "Filters · 2" and a chip per filter in use, each removable, so it is clear
// why sites are missing while the filters are folded away (#392).
export function renderFilterChips() {
  const { filterChips, filterCount } = els;
  if (!filterChips) return;
  const active = activeFilters();
  filterCount.textContent = active.length ? ` · ${active.length}` : "";
  filterChips.replaceChildren(...active.map(([name, what, value]) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "filter-chip";
    chip.dataset.filter = name;
    chip.dataset.value = value;
    chip.setAttribute("aria-label", `Remove filter ${what}: ${value}`);
    chip.textContent = `${value} ×`;
    return chip;
  }));
  filterChips.hidden = !active.length;
}

// The locations matching *criteria*: {status, location_type, parent, tenant,
// tenant_group}, each an array of values (empty for any).
export function filterLocations(locations, criteria) {
  return locations.filter((loc) => FILTERS.every(([, field]) => matchesValues(
    field === "tenant" ? siteTenants(loc) : loc[field], criteria[field],
  )));
}

// "Alarms only" once the levels are in: before (or if they fail) every site is
// shown, with the page's "levels not loaded" note, not an empty map (#337).
function alarmsOnlyApplies() {
  return state.alarmsOnly && state.levelsLoaded;
}

export function updateLocationCount(count) {
  const scope = alarmsOnlyApplies() ? " in alarm" : "";
  els.locationCount.textContent = `${count.toLocaleString("en")} site${count !== 1 ? "s" : ""}${scope}`;
}

// "Alarms only" (#334): remembered in this browser, like the board's sort.
export function restoreAlarmsOnly() {
  try {
    state.alarmsOnly = localStorage.getItem(ALARMS_ONLY_KEY) === "1";
  } catch (_err) {
    state.alarmsOnly = false;
  }
}

export function setAlarmsOnly(on, { remember = true } = {}) {
  state.alarmsOnly = Boolean(on);
  if (els.alarmsOnlyBtn) {
    els.alarmsOnlyBtn.classList.toggle("active", state.alarmsOnly);
    els.alarmsOnlyBtn.setAttribute("aria-pressed", state.alarmsOnly ? "true" : "false");
  }
  if (!remember) return;
  try {
    if (state.alarmsOnly) localStorage.setItem(ALARMS_ONLY_KEY, "1");
    else localStorage.removeItem(ALARMS_ONLY_KEY);
  } catch (_err) {
    // Not remembered; works for this page.
  }
}

export function syncQuickStatusButtons() {
  const statuses = selectedValues(els.filterStatus);
  (els.quickStatusButtons || []).forEach((button) => {
    const status = button.dataset.quickStatus || "";
    const isActive = status ? statuses.includes(status) : !statuses.length;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

export function applyFilters() {
  const criteria = Object.fromEntries(FILTERS.map(([name, field]) => [field, selectedValues(els[name])]));
  const filtered = filterLocations(state.allLocations, criteria);
  state.filteredLocations = filtered;
  const drawn = alarmsOnlyApplies() ? filtered.filter((loc) => isAlarm(locationAlerts[loc.id])) : filtered;
  renderMarkers(drawn);
  updateLocationCount(drawn.length);
  openLocationFromQuery();
  syncQuickStatusButtons();
  renderFilterChips();
  updateTenantLinks(criteria.tenant, config.scope);
  renderAlarmList();
}

// *remember* false: for the moment only, so a saved Alarms only stays saved
// (a site picked from the search that the filters hid, #392).
export function clearFilters({ remember = true } = {}) {
  for (const [name] of FILTERS) setSelectedValues(els[name], []);
  setAlarmsOnly(false, { remember });
  applyFilters();
}
