// The sidebar's filters: which locations the map shows.
import { isAlarm, renderAlarmList } from "./alarms.js";
import { openLocationFromQuery } from "./inspector.js";
import { renderMarkers } from "./markers.js";
import { els, locationAlerts, state } from "./state.js";

const ALARMS_ONLY_KEY = "nautobot-maps-map-alarms-only";

const FILTERS = [
  ["filterStatus", "status", "All statuses"],
  ["filterType", "location_type", "All types"],
  ["filterParent", "parent", "All parents"],
  ["filterTenant", "tenant", "All tenants"],
  ["filterTenantGroup", "tenant_group", "All tenant groups"],
];

export function populateFilters(locations) {
  for (const [name, field, label] of FILTERS) {
    const select = els[name];
    const values = [...new Set(locations.map((loc) => loc[field]).filter(Boolean))].sort();
    select.innerHTML = `<option value="">${label}</option>`;
    for (const value of values) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = value;
      select.appendChild(opt);
    }
  }
}

// The locations matching *criteria*: {status, location_type, parent, tenant,
// tenant_group}, each "" for any.
export function filterLocations(locations, criteria) {
  return locations.filter((loc) => FILTERS.every(([, field]) => !criteria[field] || loc[field] === criteria[field]));
}

export function updateLocationCount(count) {
  const scope = state.alarmsOnly ? " in alarm" : "";
  els.locationCount.textContent = `${count} location${count !== 1 ? "s" : ""}${scope} with GPS coordinates loaded`;
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
  (els.quickStatusButtons || []).forEach((button) => {
    const isActive = (button.dataset.quickStatus || "") === els.filterStatus.value;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

export function applyFilters() {
  const criteria = Object.fromEntries(FILTERS.map(([name, field]) => [field, els[name].value]));
  const filtered = filterLocations(state.allLocations, criteria);
  state.filteredLocations = filtered;
  const drawn = state.alarmsOnly ? filtered.filter((loc) => isAlarm(locationAlerts[loc.id])) : filtered;
  renderMarkers(drawn);
  updateLocationCount(drawn.length);
  openLocationFromQuery();
  syncQuickStatusButtons();
  renderAlarmList();
}

export function clearFilters() {
  for (const [name] of FILTERS) els[name].value = "";
  setAlarmsOnly(false);
  applyFilters();
}
