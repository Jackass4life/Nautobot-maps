// Which sites the board shows, in which order (#294).
import { formatLocationAddress, hasActiveAlarm, severityWeight, siteCaseState, siteTenants } from "./rows.js";
import { DEFAULT_SEVERITY_FILTER, DEFAULT_SORT, SORT_KEY, config, els, state } from "./state.js";

function populateSelect(selectEl, values, label) {
  selectEl.innerHTML = `<option value="">${label}</option>`;
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    selectEl.appendChild(option);
  });
}

export function populateFilters(alerts) {
  populateSelect(
    els.filterStatus,
    [...new Set(alerts.map((item) => item.status).filter(Boolean))].sort(),
    "All statuses"
  );
  populateSelect(
    els.filterType,
    [...new Set(alerts.map((item) => item.location_type).filter(Boolean))].sort(),
    "All location types"
  );
  populateSelect(
    els.filterTenant,
    [...new Set(alerts.flatMap(siteTenants))].sort(),
    "All tenants"
  );
}

// Whether a site passes the severity tiles: Alarms, one level, or all.
export function matchesSeverityFilter(item, filter = state.severityFilter) {
  if (filter === "all") return true;
  if (filter === "alarms") return hasActiveAlarm(item);
  return (item.alert_level || "no_data") === filter;
}

export function setSeverityFilter(value) {
  const buttons = els.severityFilterButtons || [];
  state.severityFilter = buttons.some((button) => button.dataset.severityFilter === value)
    ? value
    : DEFAULT_SEVERITY_FILTER;
  buttons.forEach((button) => {
    button.setAttribute("aria-pressed", button.dataset.severityFilter === state.severityFilter ? "true" : "false");
  });
}

export function compareText(left, right) {
  return (left || "").localeCompare(right || "");
}

// Milliseconds of the site's newest open alert start, or null without one.
export function latestDownTime(item) {
  const parsed = Date.parse(item.latest_down_at || "");
  return Number.isNaN(parsed) ? null : parsed;
}

export function compareSites(a, b, sort) {
  if (sort === "site") return compareText(a.name, b.name);
  if (sort === "newest") {
    // Newest down first (#228); sites with nothing down follow, by severity.
    const left = latestDownTime(a);
    const right = latestDownTime(b);
    if (left !== right) {
      if (left === null) return 1;
      if (right === null) return -1;
      return right - left;
    }
  }
  return severityWeight(a.alert_level) - severityWeight(b.alert_level)
    // Within a level, more important sites first (#293).
    || (a.tier_rank ?? 99) - (b.tier_rank ?? 99)
    || (b.down_device_count || 0) - (a.down_device_count || 0)
    || compareText(a.name, b.name);
}

export function restoreSort() {
  let stored = null;
  try {
    stored = localStorage.getItem(SORT_KEY);
  } catch (_err) {
    // Storage unavailable: keep the default.
  }
  els.sortBy.value = Array.from(els.sortBy.options).some((option) => option.value === stored) ? stored : DEFAULT_SORT;
}

export function rememberSort(value) {
  try {
    if (value === DEFAULT_SORT) localStorage.removeItem(SORT_KEY);
    else localStorage.setItem(SORT_KEY, value);
  } catch (_err) {
    // noop
  }
}

export function isSiteExpanded(item) {
  const siteId = String(item.id || "");
  return state.allSitesExpanded ? !state.expandedSiteIds.has(siteId) : state.expandedSiteIds.has(siteId);
}

// Wall view order: nobody on it, someone on it, then planned maintenance (#283).
export function caseSortRank(item) {
  if (item.alert_level === "maintenance") return 2;
  return siteCaseState(item).state === "handled" ? 1 : 0;
}

// The sites that pass *criteria*, sorted: {site (search text), status, type,
// tenant, sort, severityFilter, wallView}.
export function filterSites(sites, criteria) {
  const siteNeedle = (criteria.site || "").trim().toLowerCase();
  const { status, type, tenant, sort, wallView } = criteria;
  const filtered = sites.filter((item) => {
    const searchableText = [
      item.name,
      item.ancestor_path || item.parent,
      formatLocationAddress(item),
      item.country,
    ].join(" ").toLowerCase();
    // The wall view also lists sites in maintenance, at the bottom (#283).
    if (!matchesSeverityFilter(item, criteria.severityFilter) && !(wallView && item.alert_level === "maintenance")) return false;
    if (siteNeedle && !searchableText.includes(siteNeedle)) return false;
    if (status && item.status !== status) return false;
    if (type && item.location_type !== type) return false;
    if (tenant && !siteTenants(item).includes(tenant)) return false;
    return true;
  });

  // The wall view puts the sites nobody is on first (#271).
  filtered.sort((a, b) => (wallView ? caseSortRank(a) - caseSortRank(b) : 0) || compareSites(a, b, sort));

  return filtered;
}

// The board's sites with the filters on the page.
export function getFilteredAlerts() {
  return filterSites(state.allAlerts, {
    site: els.filterSite.value,
    status: els.filterStatus.value,
    type: els.filterType.value,
    tenant: els.filterTenant.value,
    sort: els.sortBy.value,
    severityFilter: state.severityFilter,
    wallView: config.wallView,
  });
}

// "More filters (2)": the hidden filters say when they are in use.
export function moreFiltersInUse() {
  const sorted = els.sortBy.value !== DEFAULT_SORT;
  return [els.filterStatus?.value, els.filterType?.value, els.toggleNonOperational?.checked, sorted].filter(Boolean).length;
}

// Reset is shown only when there is something to reset (#311).
export function filtersInUse() {
  return Boolean(
    els.filterSite.value.trim()
      || els.filterTenant.value
      || moreFiltersInUse()
      || els.sortBy.value !== DEFAULT_SORT
      || state.severityFilter !== DEFAULT_SEVERITY_FILTER
  );
}

export function renderMoreFiltersLabel() {
  if (!els.moreFiltersToggle) return;
  const inUse = moreFiltersInUse();
  els.moreFiltersToggle.textContent = inUse ? `More filters (${inUse})` : "More filters";
}
