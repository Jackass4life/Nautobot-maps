// The map page (templates/index.html): creates the Leaflet map, finds the
// page's elements, wires the controls and loads the locations.  The only map
// module that looks at the page when it loads (#294).  Leaflet (L) is loaded
// before this, as a plain script.
import { getJson } from "../lib/http.js";
import { selectedValues, setSelectedValues } from "../lib/multi-select.js";
import { inScope, readScope } from "../lib/scope.js";
import { initTopBar } from "../lib/nav.js";
import { showError } from "../lib/toast.js";
import { initInfoTips } from "../lib/tooltip.js";
import { renderAlarmList, toggleAlarmList } from "./alarms.js";
import {
  applyFilters,
  clearFilters,
  linkedSiteOutOfScope,
  populateFilters,
  restoreAlarmsOnly,
  setAlarmsOnly,
  showScope,
} from "./filters.js";
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
import { initPanelToggle } from "./sidebar.js";
import { matchSites, siteMatchesHtml } from "./sitesearch.js";
import { COORDINATE_REFRESH_MS, initCoordinateCheck, loadCoordinateCheck } from "./coordinates.js";
import { config, detailCache, els, locationAlerts, state } from "./state.js";
import { fitToSites } from "./view.js";

const byId = (id) => document.getElementById(id);

// First, as when it was its own script: its Escape runs before the panel's.
initInfoTips();

// Settings from the server (templates/index.html, #197).
const appConfig = byId("app-config")?.dataset || {};
config.nautobotUrl = appConfig.nautobotUrl || "";
state.initialLocationId = new URLSearchParams(window.location.search).get("location_id");
// ?tenant= (repeatable) and ?q=: the map for some sites only, as the board (#367).
config.scope = readScope(window.location.search);

// The whole world until the sites are in, unless a fixed view is set (#362).
const fixedView = Boolean(appConfig.viewZoom);
state.map = L.map("map", {
  center: fixedView ? [Number(appConfig.viewLat), Number(appConfig.viewLon)] : [20, 0],
  zoom: fixedView ? Number(appConfig.viewZoom) : 3,
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
  mapScope: byId("map-scope"),
  coordinateNote: byId("coordinate-note"),
  coordinateText: byId("coordinate-text"),
  coordinateToggle: byId("coordinate-toggle"),
  coordinateList: byId("coordinate-list"),
  searchInput: byId("search-input"),
  searchBtn: byId("search-btn"),
  searchResults: byId("search-results"),
  searchResultsHeader: byId("search-results-header"),
  searchResultsList: byId("search-results-list"),
  siteMatches: byId("site-matches"),
  filtersToggle: byId("filters-toggle"),
  filterSection: byId("filter-section"),
  filterChips: byId("filter-chips"),
  filterCount: byId("filter-count"),
  alarmList: byId("alarm-list"),
  alarmCount: byId("alarm-count"),
  alarmBreakdown: byId("alarm-breakdown"),
  alarmMore: byId("alarm-more"),
  alarmsOnlyBtn: byId("alarms-only"),
  sidebar: byId("sidebar"),
  panelToggle: byId("panel-toggle"),
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

  // A device opens in place; "Show all" lists every down device (#394).
  const row = event.target.closest(".device-row");
  if (row) {
    const key = row.dataset.deviceKey;
    // The same device can be in the Down list and under All devices: focus stays in the list clicked.
    const list = row.closest("ul")?.getAttribute("aria-label") || "";
    if (state.inspectorOpenDevices.has(key)) state.inspectorOpenDevices.delete(key);
    else state.inspectorOpenDevices.add(key);
    renderInspectorContent();
    els.inspectorContent
      .querySelector(`ul[aria-label="${CSS.escape(list)}"] .device-row[data-device-key="${CSS.escape(key)}"]`)
      ?.focus();
    return;
  }
  // A fold the operator opens or closes stays so while the panel is redrawn
  // (#394); one open by default isn't remembered as chosen.
  const foldTitle = event.target.closest(".panel-fold-title");
  if (foldTitle) {
    const fold = foldTitle.parentElement;
    state.inspectorFolds.set(fold.dataset.fold, !fold.open);
    return;
  }
  if (event.target.closest("[data-down-toggle]")) {
    state.inspectorDownExpanded = !state.inspectorDownExpanded;
    renderInspectorContent();
    els.inspectorContent.querySelector("[data-down-toggle]")?.focus();
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

// Filters, folded behind one button (#392); a chip removes its filter.
els.filtersToggle.addEventListener("click", () => {
  const open = els.filterSection.hidden;
  els.filterSection.hidden = !open;
  els.filtersToggle.setAttribute("aria-expanded", open ? "true" : "false");
});
els.filterChips.addEventListener("click", (event) => {
  const chip = event.target.closest(".filter-chip");
  if (!chip || !els[chip.dataset.filter]) return;
  const select = els[chip.dataset.filter];
  setSelectedValues(select, selectedValues(select).filter((value) => value !== chip.dataset.value));
  applyFilters();
});
[els.filterStatus, els.filterType, els.filterParent, els.filterTenant, els.filterTenantGroup].forEach((select) => {
  select.addEventListener("change", applyFilters);
});
byId("clear-filters").addEventListener("click", clearFilters);
els.quickStatusButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const status = button.dataset.quickStatus;
    const selected = selectedValues(els.filterStatus);
    setSelectedValues(els.filterStatus, !status ? [] : selected.includes(status)
      ? selected.filter((value) => value !== status) : [...selected, status]);
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
// Before the alarm list is drawn, so its count lands on a folded panel's ».
initPanelToggle();
renderAlarmList();

initTopBar();

// Search (#392): the matching sites as you type; Enter, the button or the
// map row search the map for an address or coordinates.
const { searchInput, searchBtn, searchResults, siteMatches } = els;
function hideSiteMatches() {
  siteMatches.hidden = true;
  siteMatches.innerHTML = "";
}
function mapSearch() {
  hideSiteMatches();
  doSearch();
}
searchBtn.addEventListener("click", mapSearch);
// Enter follows the first row: a full site name, otherwise the map search (#411).
function openSiteMatch(id) {
  hideSiteMatches();
  searchInput.value = "";
  // Back to the whole map after a map search (its pins and results go).
  searchResults.classList.add("hidden");
  applyFilters();
  if (openLocationById(id, { focusInspector: false })) return;
  // A site the filters leave out: every site, for now (a saved Alarms only stays).
  clearFilters({ remember: false });
  openLocationById(id, { focusInspector: false });
}
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const first = siteMatches.hidden ? null : siteMatches.querySelector("button");
    if (first?.dataset.locationId) openSiteMatch(first.dataset.locationId);
    else mapSearch();
  }
  else if (e.key === "ArrowDown" && !siteMatches.hidden) {
    e.preventDefault();
    siteMatches.querySelector("button")?.focus();
  } else if (e.key === "Escape" && !siteMatches.hidden) {
    e.stopPropagation(); // the list, not also the site panel
    hideSiteMatches();
  }
});
searchInput.addEventListener("input", () => {
  const query = searchInput.value;
  if (!query.trim()) {
    hideSiteMatches();
    searchResults.classList.add("hidden");
    applyFilters();
    return;
  }
  siteMatches.innerHTML = siteMatchesHtml(matchSites(state.allLocations, query), query, locationAlerts);
  siteMatches.hidden = false;
});
// A click on the list keeps focus in the field (Safari doesn't focus buttons).
siteMatches.addEventListener("mousedown", (event) => event.preventDefault());
siteMatches.addEventListener("click", (event) => {
  const button = event.target.closest(".site-match");
  if (!button) return;
  if (button.classList.contains("site-match-map")) {
    mapSearch();
    return;
  }
  openSiteMatch(button.dataset.locationId);
});
siteMatches.addEventListener("keydown", (event) => {
  const buttons = Array.from(siteMatches.querySelectorAll("button"));
  const at = buttons.indexOf(document.activeElement);
  if (event.key === "ArrowDown" && at < buttons.length - 1) buttons[at + 1].focus();
  else if (event.key === "ArrowUp") (at > 0 ? buttons[at - 1] : searchInput).focus();
  else if (event.key === "Escape") {
    event.stopPropagation();
    hideSiteMatches();
    searchInput.focus();
  } else return;
  event.preventDefault();
});
// Leaving the search (not into its list) closes the list.
byId("search-section").addEventListener("focusout", (event) => {
  if (!byId("search-section").contains(event.relatedTarget)) hideSiteMatches();
});

// The legend (#392): on the map, behind a small button; clicks there stay off the map.
const legend = byId("map-legend");
const legendControl = L.control({ position: "bottomleft" });
legendControl.onAdd = () => {
  L.DomEvent.disableClickPropagation(legend);
  L.DomEvent.disableScrollPropagation(legend);
  return legend;
};
legendControl.addTo(state.map);
byId("legend-toggle").addEventListener("click", () => {
  const panel = byId("legend");
  panel.hidden = !panel.hidden;
  byId("legend-toggle").setAttribute("aria-expanded", panel.hidden ? "false" : "true");
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
    const everySite = data.locations || [];
    // Only the sites the link asks for: the markers, alarm list, filters,
    // count and first view all follow from here (#367).
    state.allLocations = everySite.filter((loc) => inScope(loc, config.scope));
    showScope(everySite.length);
    if (linkedSiteOutOfScope(everySite)) {
      showError("The linked site isn't among the sites this link shows (tenant= / q=): Show all sites to open it.");
    }
    showLoading(true, `Processing ${state.allLocations.length} locations…`);
    await levelsLoaded;
    // Every site, not only the filtered ones, before the markers are drawn; a
    // link to a site flies on from here.  The loading overlay keeps hands off.
    if (!fixedView) fitToSites(state.map, state.allLocations);
    populateFilters(state.allLocations);
    applyFilters();
  } catch (err) {
    showError("Failed to load locations: " + err.message);
  } finally {
    showLoading(false);
  }
}

initCoordinateCheck();
loadLocations(loadLocationAlerts()).then(() => {
  startLocationAlertRefresh({ loadNow: false });
  loadCoordinateCheck();
  setInterval(loadCoordinateCheck, COORDINATE_REFRESH_MS);
});
