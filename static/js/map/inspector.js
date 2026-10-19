// The site panel ("inspector"): opens from a marker, a search result or
// ?location_id=, shows the site's level, devices, circuits and ASNs.
import { formatFeedTime } from "../lib/format.js";
import { escHtml } from "../lib/html.js";
import { getJson } from "../lib/http.js";
import { getColocatedLocationIds, updateMarkerForAlert } from "./markers.js";
import {
  badgeClass,
  buildCircuitCard,
  buildHealthSummary,
  buildMaintenanceHistoryLink,
  buildNautobotLink,
  deviceFilterLabel,
  deviceKey,
  deviceRowHtml,
  filterDevicesForInspector,
  foldHtml,
  inspectorRow,
  sortDownDevices,
  statusLineHtml,
  tenantRow,
} from "./panel.js";
import {
  colocGroupByLocId,
  detailCache,
  els,
  getLocationById,
  inFlightDetailRequests,
  locationAlertInfo,
  markerByLocId,
  state,
} from "./state.js";

function isMobileViewport() {
  return window.matchMedia("(max-width: 640px)").matches;
}

export function isInspectorOpen() {
  return !els.inspector.classList.contains("hidden");
}

// The dimmed map behind the panel, on a phone only.
export function syncInspectorBackdrop() {
  const shouldShow = isInspectorOpen() && isMobileViewport();
  els.inspectorBackdrop.classList.toggle("hidden", !shouldShow);
}

// The panel changes the map's width: Leaflet must measure again, after the
// layout has settled (two frames).  The map of the call is resized, even if
// state.map changes meanwhile.
export function scheduleMapResize() {
  const currentMap = state.map;
  if (!currentMap || typeof currentMap.invalidateSize !== "function" || typeof currentMap.getContainer !== "function" || !currentMap.getContainer()) {
    return;
  }
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      if (typeof currentMap.getContainer !== "function" || !currentMap.getContainer() || typeof currentMap.invalidateSize !== "function") return;
      currentMap.invalidateSize({ pan: false, animate: false });
    });
  });
}

function updateLocationQuery(locId) {
  const params = new URLSearchParams(window.location.search);
  if (locId) params.set("location_id", locId);
  else params.delete("location_id");
  const query = params.toString();
  const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  window.history.replaceState({}, "", nextUrl);
}

function getSelectedGroupLocations() {
  const { selectedGroupIds, selectedLocationId } = state;
  const ids = selectedGroupIds.length > 0 ? selectedGroupIds : selectedLocationId ? [selectedLocationId] : [];
  return ids
    .map((locId) => getLocationById(locId))
    .filter(Boolean);
}

function renderInspectorTabs() {
  const { inspectorTabs } = els;
  const groupLocations = getSelectedGroupLocations();
  if (groupLocations.length <= 1) {
    inspectorTabs.classList.add("hidden");
    inspectorTabs.innerHTML = "";
    return;
  }
  inspectorTabs.classList.remove("hidden");
  inspectorTabs.innerHTML = groupLocations
    .map(
      (loc) =>
        `<button class="inspector-site-tab${loc.id === state.selectedLocationId ? " active" : ""}" type="button" data-location-id="${escHtml(loc.id)}" aria-pressed="${loc.id === state.selectedLocationId ? "true" : "false"}">${escHtml(loc.name)}</button>`
    )
    .join("");
}

export function renderInspectorContent() {
  const { inspectorTitle, inspectorSubtitle, inspectorTabs, inspectorState, inspectorContent } = els;
  const { selectedLocationId, selectedDetail, inspectorLoading, inspectorError } = state;
  if (!selectedLocationId) {
    inspectorTitle.textContent = "Select a location";
    inspectorSubtitle.textContent = "Click a map marker to inspect a location.";
    inspectorTabs.classList.add("hidden");
    inspectorTabs.innerHTML = "";
    inspectorState.classList.remove("hidden");
    inspectorState.textContent = "Select a location from the map to view health, metadata, equipment, and ASN details.";
    inspectorContent.classList.add("hidden");
    inspectorContent.innerHTML = "";
    return;
  }

  const loc = getLocationById(selectedLocationId);
  if (!loc) return;

  // What is wrong first, the rest folded with counts (#394).
  inspectorTitle.textContent = loc.name || "Site";
  // Co-located sites (the tabs below) say so: the status is the chosen one's.
  const here = state.selectedGroupIds.length > 1 ? `${state.selectedGroupIds.length} locations here` : "";
  inspectorSubtitle.textContent = [loc.location_type, loc.ancestor_path || loc.parent, here].filter(Boolean).join(" · ");
  renderInspectorTabs();
  inspectorState.classList.add("hidden");
  inspectorContent.classList.remove("hidden");

  const detail = !inspectorLoading && !inspectorError ? selectedDetail : null;
  const devices = detail && Array.isArray(detail.devices) ? detail.devices : [];
  const since = locationAlertInfo[selectedLocationId]?.since;
  const status = statusLineHtml({
    alert: detail?.alert || null,
    counts: detail?.counts || null,
    since: since ? formatFeedTime(since) : "",
    loading: inspectorLoading,
    error: inspectorError,
  });
  // Planned, Decommissioning...: said; Active is the norm and isn't.
  const lifecycle = loc.status && loc.status.toLowerCase() !== "active"
    ? `<span class="popup-badge ${badgeClass(loc.status)}">${escHtml(loc.status)}</span>` : "";
  const links = [buildNautobotLink(loc), buildMaintenanceHistoryLink(loc)].filter(Boolean).join("");

  let body;
  if (inspectorError) {
    body = `<div class="inspector-error">
      Could not load details: ${escHtml(inspectorError)}
      <div class="inspector-actions-row">
        <button id="inspector-retry" class="secondary-btn" type="button">Retry</button>
      </div>
    </div>`;
  } else if (inspectorLoading) {
    body = `<div class="inspector-empty">Loading devices…</div>`;
  } else {
    body = downDevicesHtml(devices) + foldsHtml(loc, detail, devices);
  }

  inspectorContent.innerHTML = `
    <div class="inspector-section site-summary">
      ${status}
      ${lifecycle || links ? `<div class="inspector-actions-row">${lifecycle}${links}</div>` : ""}
    </div>
    ${body}
  `;
}

// The down devices first (#394): the worst eight, then "Show all 34".
const DOWN_SHOWN = 8;

function downDevicesHtml(devices) {
  const down = sortDownDevices(devices);
  if (!down.length) return "";
  const shown = state.inspectorDownExpanded ? down : down.slice(0, DOWN_SHOWN);
  const rows = shown.map((device) => deviceRowHtml(device, state.inspectorOpenDevices.has(deviceKey(device)))).join("");
  const more = down.length > DOWN_SHOWN
    ? `<button class="panel-more" type="button" data-down-toggle>${state.inspectorDownExpanded ? "Show fewer" : `Show all ${down.length}`}</button>`
    : "";
  return `<section class="inspector-section" aria-labelledby="inspector-down-title">
    <h3 id="inspector-down-title" class="panel-heading">Down · ${down.length}</h3>
    <ul class="inspector-device-list device-rows" aria-label="Down devices">${rows}</ul>${more}
  </section>`;
}

// Whether a fold is open: as the operator left it, else its default.
function foldOpen(key, byDefault) {
  return state.inspectorFolds.has(key) ? state.inspectorFolds.get(key) : byDefault;
}

function foldsHtml(loc, detail, devices) {
  const { inspectorDeviceFilter, inspectorDeviceSearch } = state;
  const folds = [];
  if (devices.length) {
    // Filters and a search only where they help: more than ten devices.
    let toolbar = "";
    if (devices.length > 10) {
      const summary = buildHealthSummary(devices);
      const counts = { all: devices.length, active: summary.active, down: summary.down, unknown: summary.unknown };
      const buttons = ["all", "active", "down", "unknown"]
        .filter((key) => key === "all" || counts[key])
        .map((key) => `<button class="device-filter-btn${inspectorDeviceFilter === key ? " active" : ""}" type="button" data-device-filter="${key}" aria-pressed="${inspectorDeviceFilter === key ? "true" : "false"}">${deviceFilterLabel(key)} ${counts[key]}</button>`)
        .join("");
      toolbar = `<div class="inspector-device-toolbar">
        <div class="device-filters" aria-label="Device filters">${buttons}</div>
        <input id="inspector-device-search" class="inspector-device-search" type="search" value="${escHtml(inspectorDeviceSearch)}" placeholder="Search devices" aria-label="Search devices" />
      </div>`;
    }
    const listed = devices.length > 10 ? filterDevicesForInspector(devices, inspectorDeviceFilter, inspectorDeviceSearch) : devices;
    const rows = listed.map((device) => deviceRowHtml(device, state.inspectorOpenDevices.has(deviceKey(device)))).join("");
    const list = listed.length
      ? `<ul class="inspector-device-list device-rows" aria-label="Devices">${rows}</ul>`
      : `<div class="inspector-empty">No device matches.</div>`;
    const quiet = !sortDownDevices(devices).length;
    folds.push(foldHtml("devices", "All devices", devices.length, toolbar + list, foldOpen("devices", quiet && devices.length <= 10)));
  }

  const circuits = Array.isArray(detail?.circuits) ? detail.circuits : [];
  if (detail?.circuits_error) {
    folds.push(foldHtml("circuits", "Circuits", null, `<div class="inspector-empty">${escHtml(detail.circuits_error)}</div>`, foldOpen("circuits", false)));
  } else if (circuits.length) {
    folds.push(foldHtml("circuits", "Circuits", circuits.length,
      `<ul class="inspector-device-list" aria-label="Circuit list">${circuits.map(buildCircuitCard).join("")}</ul>`, foldOpen("circuits", false)));
  }

  const asns = Array.isArray(detail?.asns) ? detail.asns : [];
  if (asns.length) {
    const tags = asns.map((asn) => `<span class="asn-tag" title="${escHtml(asn.description || "")}">AS${escHtml(asn.asn)}${asn.tenant ? ` · ${escHtml(asn.tenant)}` : ""}</span>`).join("");
    folds.push(foldHtml("asns", "ASNs", asns.length, tags, foldOpen("asns", false)));
  }

  const rows = [
    inspectorRow("Type", loc.location_type),
    inspectorRow("Parent", loc.parent),
    // No Country row (#386): read from addresses it showed a town; the address says it.
    inspectorRow("Address", loc.physical_address),
    inspectorRow("Facility", loc.facility),
    tenantRow(loc),
    inspectorRow("Tenant group", loc.tenant_group),
    inspectorRow("ASN", loc.asn),
    inspectorRow("Time zone", loc.time_zone),
    inspectorRow("Tags", Array.isArray(loc.tags) && loc.tags.length > 0 ? loc.tags.join(", ") : ""),
    inspectorRow("Description", loc.description),
  ].filter(Boolean).join("");
  if (rows) folds.push(foldHtml("details", "Details", null, rows, foldOpen("details", false)));
  if (!devices.length && !folds.length) return `<div class="inspector-empty">No devices at this site.</div>`;
  if (!devices.length) folds.unshift(`<div class="inspector-empty">No devices at this site.</div>`);
  return `<div class="inspector-section panel-folds">${folds.join("")}</div>`;
}

// The site's details, once per site until the levels refresh; concurrent
// calls share one request.
export async function loadLocationDetail(locId) {
  if (detailCache.has(locId)) return detailCache.get(locId);
  if (inFlightDetailRequests.has(locId)) return inFlightDetailRequests.get(locId);

  const request = (async () => {
    const loc = getLocationById(locId);
    const locType = loc ? (loc.location_type || "") : "";
    const url = `/api/locations/${encodeURIComponent(locId)}/detail`
      + (locType ? `?location_type=${encodeURIComponent(locType)}` : "");
    const detail = await getJson(url);
    detailCache.set(locId, detail);
    return detail;
  })();

  inFlightDetailRequests.set(locId, request);
  try {
    return await request;
  } finally {
    inFlightDetailRequests.delete(locId);
  }
}

// What the operator opened in a site's panel; a new site starts closed (#394).
function resetPanelState() {
  Object.assign(state, {
    inspectorDeviceFilter: "all",
    inspectorDeviceSearch: "",
    inspectorFolds: new Map(),
    inspectorOpenDevices: new Set(),
    inspectorDownExpanded: false,
  });
}

export function openInspectorForLocation(locId, options = {}) {
  const loc = getLocationById(locId);
  if (!loc) return;
  const locationChanged = locId !== state.selectedLocationId;

  state.selectedLocationId = locId;
  state.selectedGroupIds = options.groupIds && options.groupIds.length > 0 ? [...options.groupIds] : [locId];
  state.selectedDetail = detailCache.get(locId) || null;
  state.inspectorError = "";
  state.inspectorLoading = !state.selectedDetail;
  if (locationChanged) resetPanelState();
  if (options.focusElement) {
    state.lastInspectorTrigger = options.focusElement;
  }

  document.body.classList.add("inspector-open");
  els.inspector.classList.remove("hidden");
  els.inspector.setAttribute("aria-hidden", "false");
  syncInspectorBackdrop();
  scheduleMapResize();
  updateLocationQuery(locId);
  renderInspectorContent();

  if (options.focusInspector !== false) {
    els.inspectorCloseButton.focus();
  }

  if (state.selectedDetail) {
    if (state.selectedDetail.alert) updateMarkerForAlert(locId, state.selectedDetail.alert.level);
    return;
  }

  loadLocationDetail(locId)
    .then((detail) => {
      if (state.selectedLocationId !== locId) return;
      state.selectedDetail = detail;
      state.inspectorLoading = false;
      state.inspectorError = "";
      renderInspectorContent();
      if (detail.alert) updateMarkerForAlert(locId, detail.alert.level);
    })
    .catch((err) => {
      if (state.selectedLocationId !== locId) return;
      state.selectedDetail = null;
      state.inspectorLoading = false;
      state.inspectorError = err.message || "Unknown error";
      renderInspectorContent();
    });
}

export function closeInspector(options = {}) {
  document.body.classList.remove("inspector-open");
  els.inspector.classList.add("hidden");
  els.inspector.setAttribute("aria-hidden", "true");
  syncInspectorBackdrop();
  scheduleMapResize();
  Object.assign(state, {
    selectedLocationId: null,
    selectedGroupIds: [],
    selectedDetail: null,
    inspectorError: "",
    inspectorLoading: false,
  });
  resetPanelState();
  renderInspectorContent();
  updateLocationQuery(null);

  const trigger = state.lastInspectorTrigger;
  if (options.restoreFocus !== false && trigger && typeof trigger.focus === "function") {
    trigger.focus();
  }
}

// The open panel follows a refreshed level (#316); it is re-drawn only on a
// change, so a search being typed in it isn't reset every minute.
export function applyLevelToOpenPanel(levels) {
  const { selectedLocationId, selectedDetail, inspectorLoading, inspectorError } = state;
  if (!selectedLocationId || !selectedDetail || inspectorLoading || inspectorError) return;
  const level = levels[selectedLocationId] || { level: "no_data", reason: "No monitored devices" };
  const counts = Number.isFinite(level.monitored) ? { down: level.down || 0, monitored: level.monitored } : selectedDetail.counts;
  const alert = { level: level.level, reason: level.reason || "" };
  const old = selectedDetail;
  if (old.alert?.level === alert.level && old.alert?.reason === alert.reason
    && old.counts?.down === counts?.down && old.counts?.monitored === counts?.monitored) return;
  state.selectedDetail = { ...old, alert, counts };
  renderInspectorContent();
  // The devices' notes (maintenance, not monitored) may have changed too:
  // reload them; the search and filter stay as they are.
  const locId = selectedLocationId;
  detailCache.delete(locId);
  loadLocationDetail(locId)
    .then((detail) => {
      if (state.selectedLocationId !== locId || state.inspectorLoading) return;
      state.selectedDetail = detail;
      renderInspectorContent();
    })
    .catch((err) => console.warn("Could not reload the site panel:", err));
}

// A marker (pin or stack) was clicked or activated with the keyboard.
export function handleMarkerActivation(marker, locId, groupIds, focusElement) {
  state.map.panTo(marker.getLatLng());
  openInspectorForLocation(locId, { groupIds, focusElement, focusInspector: true });
}

// From this zoom nothing is clustered (map/main.js), so a site is its own marker.
export const UNCLUSTERED_ZOOM = 15;

// Show *locId* on the map and open its panel; false when it isn't drawn
// (filtered out).  A quiet site in a cluster: zoom in until it is its own
// marker.  (Not markercluster's zoomToShowLayer: a redraw while it zooms
// would leave it waiting on a removed marker.)
export function openLocationById(locId, options = {}) {
  const loc = getLocationById(locId);
  const marker = markerByLocId[locId];
  if (!loc || !marker) return false;
  const zoom = state.clusterLayer?.hasLayer(marker) ? UNCLUSTERED_ZOOM : 13;
  state.map.flyTo([loc.latitude, loc.longitude], Math.max(state.map.getZoom(), zoom), { duration: 0.8 });
  openInspectorForLocation(locId, {
    groupIds: colocGroupByLocId[locId] || getColocatedLocationIds(locId),
    focusElement: options.focusElement || marker.getElement(),
    focusInspector: options.focusInspector,
  });
  return true;
}

export function openLocationFromQuery() {
  if (!state.initialLocationId || state.initialLocationOpened) return;
  if (openLocationById(state.initialLocationId, { focusInspector: false, markInitial: true })) {
    state.initialLocationOpened = true;
  }
}
