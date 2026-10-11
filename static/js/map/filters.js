// The sidebar's filters: which locations the map shows.
import { openLocationFromQuery } from "./inspector.js";
import { renderMarkers } from "./markers.js";
import { els, state } from "./state.js";

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
  els.locationCount.textContent = `${count} location${count !== 1 ? "s" : ""} with GPS coordinates loaded`;
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
  renderMarkers(filtered);
  updateLocationCount(filtered.length);
  openLocationFromQuery();
  syncQuickStatusButtons();
}

export function clearFilters() {
  for (const [name] of FILTERS) els[name].value = "";
  applyFilters();
}
