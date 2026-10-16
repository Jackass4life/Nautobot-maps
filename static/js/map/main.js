// The map page (templates/index.html): creates the Leaflet map, finds the
// page's elements, wires the controls and loads the locations.  The only map
// module that looks at the page when it loads (#294).  Leaflet (L) is loaded
// before this, as a plain script.
import { getJson } from "../lib/http.js";
import { initThemeToggle } from "../lib/theme.js";
import { showError } from "../lib/toast.js";
import { initInfoTips } from "../lib/tooltip.js";
import { renderAlarmList, toggleAlarmList } from "./alarms.js";
import { applyFilters, clearFilters, populateFilters, restoreAlarmsOnly, setAlarmsOnly } from "./filters.js";
import {
  closeInspector,
  handleMarkerActivation,
  isInspectorOpen,
  UNCLUSTERED_ZOOM,
  openInspectorForLocation,
  openLocationById,
  renderInspectorContent,
  syncInspectorBackdrop,
} from "./inspector.js";
import { onMarkerActivate, onRedrawNeeded, quietClusterIcon } from "./markers.js";
import { loadLocationAlerts, startLocationAlertRefresh } from "./refresh.js";
import { doSearch } from "./search.js";
import { config, detailCache, els, state } from "./state.js";
import { fitToSites, parseView } from "./view.js";

const byId = (id) => document.getElementById(id);

// First, as when it was its own script: its Escape runs before the panel's.
initInfoTips();

// Settings from the server (templates/index.html, #197).
const appConfig = byId("app-config")?.dataset || {};
config.nautobotUrl = appConfig.nautobotUrl || "";
state.initialLocationId = new URLSearchParams(window.location.search).get("location_id");

// The whole world until the sites are in, unless a fixed view is set (#362).
const defaultView = parseView(appConfig.defaultView);
state.map = L.map("map", {
  center: defaultView ? defaultView.center : [20, 0],
  zoom: defaultView ? defaultView.zoom : 3,
  preferCanvas: true,
});

// MAP_TILE_URL / MAP_TILE_ATTRIBUTION; default OpenStreetMap.
L.tileLayer(appConfig.tileUrl || "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: appConfig.tileAttribution || "",
  maxZoom: 19,
}).addTo(state.map);
// The quiet sites (OK, No data) cluster; sites that need attention are pins
// in markerLayer, added after it so they are drawn on top (#334).
state.clusterLayer = L.markerClusterGroup({
  iconCreateFunction: quietClusterIcon,
  showCoverageOnHover: false,
  maxClusterRadius: 60,
  disableClusteringAtZoom: UNCLUSTERED_ZOOM,
}).addTo(state.map);
state.markerLayer = L.layerGroup().addTo(state.map);

Object.assign(els, {
  inspector: byId("location-inspector"),
  inspectorBackdrop: byId("inspector-backdrop"),
  inspectorTitle: byId("inspector-title"),
  inspectorSubtitle: byId("inspector-subtitle"),
  inspectorTabs: byId("inspector-site-tabs"),
  inspectorState: byId("inspector-state"),
  inspectorContent: byId("inspector-content"),
  inspectorCloseButton: byId("inspector-close"),
  alertLevelsNote: byId("alert-levels-note"),
  filterStatus: byId("filter-status"),
  filterType: byId("filter-type"),
  filterParent: byId("filter-parent"),
  filterTenant: byId("filter-tenant"),
  filterTenantGroup: byId("filter-tenant-group"),
  quickStatusButtons: Array.from(document.querySelectorAll("[data-quick-status]")),
  locationCount: byId("location-count"),
  searchInput: byId("search-input"),
  searchBtn: byId("search-btn"),
  searchResults: byId("search-results"),
  searchResultsHeader: byId("search-results-header"),
  searchResultsList: byId("search-results-list"),
  alarmList: byId("alarm-list"),
  alarmCount: byId("alarm-count"),
  alarmMore: byId("alarm-more"),
  alarmsOnlyBtn: byId("alarms-only"),
});

onMarkerActivate(handleMarkerActivation);
// A site that starts or stops needing attention moves out of or into a cluster.
onRedrawNeeded(() => applyFilters());

let resizeAnimationFrame = null;
window.addEventListener("resize", () => {
  if (resizeAnimationFrame != null) return;
  resizeAnimationFrame = window.requestAnimationFrame(() => {
    resizeAnimationFrame = null;
    syncInspectorBackdrop();
  });
});

// The site panel.
const { inspectorCloseButton, inspectorBackdrop, inspectorTabs, inspectorContent } = els;
inspectorCloseButton.addEventListener("click", () => closeInspector());
// The panel stays open while the map is used; × or Escape closes it, and on a
// phone so does a tap on the dimmed map behind it (#311).
inspectorBackdrop.addEventListener("click", () => closeInspector({ restoreFocus: false }));
inspectorTabs.addEventListener("click", (event) => {
  const button = event.target.closest("[data-location-id]");
  if (!button) return;
  openInspectorForLocation(button.dataset.locationId, {
    groupIds: state.selectedGroupIds,
    focusInspector: false,
  });
});
inspectorContent.addEventListener("click", (event) => {
  const retryButton = event.target.closest("#inspector-retry");
  if (retryButton && state.selectedLocationId) {
    detailCache.delete(state.selectedLocationId);
    openInspectorForLocation(state.selectedLocationId, {
      groupIds: state.selectedGroupIds,
      focusInspector: false,
    });
    return;
  }

  const filterButton = event.target.closest("[data-device-filter]");
  if (!filterButton) return;
  state.inspectorDeviceFilter = filterButton.dataset.deviceFilter || "all";
  renderInspectorContent();
});
inspectorContent.addEventListener("input", (event) => {
  if (event.target.id !== "inspector-device-search") return;
  const selectionStart = event.target.selectionStart;
  const selectionEnd = event.target.selectionEnd;
  state.inspectorDeviceSearch = event.target.value || "";
  renderInspectorContent();
  const searchInput = byId("inspector-device-search");
  if (searchInput) {
    searchInput.focus();
    const safeStart = Math.min(selectionStart ?? searchInput.value.length, searchInput.value.length);
    const safeEnd = Math.min(selectionEnd ?? searchInput.value.length, searchInput.value.length);
    searchInput.setSelectionRange(safeStart, safeEnd);
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && isInspectorOpen()) {
    closeInspector();
  }
});
renderInspectorContent();

// Filters.
[els.filterStatus, els.filterType, els.filterParent, els.filterTenant, els.filterTenantGroup].forEach((select) => {
  select.addEventListener("change", applyFilters);
});
byId("clear-filters").addEventListener("click", clearFilters);
els.quickStatusButtons.forEach((button) => {
  button.addEventListener("click", () => {
    els.filterStatus.value = button.dataset.quickStatus || "";
    applyFilters();
  });
});

// The sites in alarm (#334): the list opens a site; "Alarms only" hides the rest.
els.alarmList.addEventListener("click", (event) => {
  const button = event.target.closest(".alarm-item");
  if (!button) return;
  const open = () => openLocationById(button.dataset.locationId, { focusInspector: false, focusElement: button });
  if (open()) return;
  // Not on the map: search results are shown.  Back to the whole map (#337).
  els.searchInput.value = "";
  els.searchResults.classList.add("hidden");
  applyFilters();
  open();
});
els.alarmMore.addEventListener("click", toggleAlarmList);
restoreAlarmsOnly();
// A link to a site shows it, even a quiet one: not remembered.
setAlarmsOnly(state.alarmsOnly && !state.initialLocationId, { remember: false });
els.alarmsOnlyBtn.addEventListener("click", () => {
  setAlarmsOnly(!state.alarmsOnly);
  applyFilters();
});
renderAlarmList();

initThemeToggle(byId("theme-toggle"));

// Search.
const { searchInput, searchBtn, searchResults } = els;
searchBtn.addEventListener("click", doSearch);
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") doSearch();
});
// Clear search results when input is cleared
searchInput.addEventListener("input", () => {
  if (!searchInput.value.trim()) {
    searchResults.classList.add("hidden");
    applyFilters();
  }
});

function showLoading(visible, message) {
  byId("loading-text").textContent = message || "Loading locations from Nautobot…";
  byId("loading-overlay").style.display = visible ? "flex" : "none";
}

// *levelsLoaded*: the first alert levels, asked for at the same time.  The map
// is drawn once they are in, so a site in alarm is a pin from the start, not
// first in a cluster with every other site (#334).
async function loadLocations(levelsLoaded) {
  showLoading(true, "Loading locations from Nautobot…");
  try {
    const data = await getJson("/api/locations");
    state.allLocations = data.locations || [];
    showLoading(true, `Processing ${state.allLocations.length} locations…`);
    await levelsLoaded;
    // Every site, not only the filtered ones; a link to a site flies there itself.
    if (!defaultView && !state.initialLocationId) fitToSites(state.map, state.allLocations);
    populateFilters(state.allLocations);
    applyFilters();
  } catch (err) {
    showError("Failed to load locations: " + err.message);
  } finally {
    showLoading(false);
  }
}

loadLocations(loadLocationAlerts()).then(() => startLocationAlertRefresh({ loadNow: false }));
