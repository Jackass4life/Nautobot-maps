// The map page (templates/index.html): creates the Leaflet map, finds the
// page's elements, wires the controls and loads the locations.  The only map
// module that looks at the page when it loads (#294).  Leaflet (L) is loaded
// before this, as a plain script.
import { initThemeToggle } from "../lib/theme.js";
import { showError } from "../lib/toast.js";
import { initInfoTips } from "../lib/tooltip.js";
import { applyFilters, clearFilters, populateFilters } from "./filters.js";
import {
  closeInspector,
  handleMarkerActivation,
  isInspectorOpen,
  openInspectorForLocation,
  openLocationById,
  renderInspectorContent,
  syncInspectorBackdrop,
} from "./inspector.js";
import { onMarkerActivate } from "./markers.js";
import { startLocationAlertRefresh } from "./refresh.js";
import { doSearch } from "./search.js";
import { CLUSTER_THRESHOLD, config, detailCache, els, state } from "./state.js";

const byId = (id) => document.getElementById(id);

// First, as when it was its own script: its Escape runs before the panel's.
initInfoTips();

// Settings from the server (templates/index.html, #197).
const appConfig = byId("app-config")?.dataset || {};
config.nautobotUrl = appConfig.nautobotUrl || "";
state.initialLocationId = new URLSearchParams(window.location.search).get("location_id");

state.map = L.map("map", {
  center: [20, 0],
  zoom: 3,
  preferCanvas: true,
});

// MAP_TILE_URL / MAP_TILE_ATTRIBUTION; default OpenStreetMap.
L.tileLayer(appConfig.tileUrl || "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: appConfig.tileAttribution || "",
  maxZoom: 19,
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
});

onMarkerActivate(handleMarkerActivation);

state.map.on("zoomend", () => {
  if (state.allLocations.length > CLUSTER_THRESHOLD) {
    applyFilters();
  }
  if (state.pendingLocationOpen) {
    const pending = state.pendingLocationOpen;
    state.pendingLocationOpen = null;
    const opened = openLocationById(pending.locId, pending.options);
    if (pending.options.markInitial && opened) {
      state.initialLocationOpened = true;
    }
  }
});

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

async function loadLocations() {
  showLoading(true, "Loading locations from Nautobot…");
  try {
    const resp = await fetch("/api/locations");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (data.error) throw new Error(data.error);
    state.allLocations = data.locations || [];
    showLoading(true, `Processing ${state.allLocations.length} locations…`);
    populateFilters(state.allLocations);
    applyFilters();
  } catch (err) {
    showError("Failed to load locations: " + err.message);
  } finally {
    showLoading(false);
  }
}

loadLocations().then(startLocationAlertRefresh);
