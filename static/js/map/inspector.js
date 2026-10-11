// The site panel ("inspector"): opens from a marker, a search result or
// ?location_id=, shows the site's level, devices, circuits and ASNs.
import { escHtml } from "../lib/html.js";
import { ALERT_RANK } from "./levels.js";
import { getColocatedLocationIds, updateMarkerForAlert } from "./markers.js";
import {
  badgeClass,
  buildAlertBanner,
  buildCircuitCard,
  buildHealthSummary,
  buildNautobotLink,
  deviceCard,
  deviceFilterLabel,
  filterDevicesForInspector,
  inspectorRow,
  tenantRow,
} from "./panel.js";
import {
  colocGroupByLocId,
  detailCache,
  els,
  getLocationById,
  inFlightDetailRequests,
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
  const { selectedLocationId, selectedDetail, inspectorLoading, inspectorError, inspectorDeviceFilter, inspectorDeviceSearch } = state;
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

  const statusLabel = loc.status || "Unknown";
  const primaryRows = [
    inspectorRow("Type", loc.location_type),
    inspectorRow("Parent", loc.parent),
    inspectorRow("Address", loc.physical_address),
    inspectorRow("Country", loc.country),
    inspectorRow("Facility", loc.facility),
  ].filter(Boolean).join("");
  const secondaryRows = [
    tenantRow(loc),
    inspectorRow("Tenant group", loc.tenant_group),
    inspectorRow("ASN", loc.asn),
    inspectorRow("Time zone", loc.time_zone),
    inspectorRow("Tags", Array.isArray(loc.tags) && loc.tags.length > 0 ? loc.tags.join(", ") : ""),
    inspectorRow("Description", loc.description),
  ].filter(Boolean).join("");

  const devices = selectedDetail && Array.isArray(selectedDetail.devices) ? selectedDetail.devices : [];
  const summary = buildHealthSummary(devices);
  const filteredDevices = filterDevicesForInspector(devices, inspectorDeviceFilter, inspectorDeviceSearch);
  const asns = selectedDetail && Array.isArray(selectedDetail.asns) ? selectedDetail.asns : [];
  const alert = selectedDetail && selectedDetail.alert ? selectedDetail.alert : null;
  let alertPill = `<span class="inspector-pill">${escHtml("Healthy")}</span>`;
  if (inspectorLoading) {
    alertPill = `<span class="inspector-pill">${escHtml("Loading alert status…")}</span>`;
  } else if (inspectorError) {
    alertPill = `<span class="inspector-pill">${escHtml("Alert status unavailable")}</span>`;
  } else if (alert && ALERT_RANK[alert.level]) {
    alertPill = `<span class="inspector-pill inspector-alert-pill is-${escHtml(alert.level)}">${escHtml(alert.level.toUpperCase())}</span>`;
  } else if (alert && alert.level === "maintenance") {
    alertPill = `<span class="inspector-pill inspector-alert-pill is-maintenance">MAINTENANCE</span>`;
  } else if (alert && alert.level === "no_data") {
    alertPill = `<span class="inspector-pill">${escHtml("No monitored devices")}</span>`;
  }
  // The board's count (#316): the devices that decide the level.
  const counts = !inspectorLoading && !inspectorError && selectedDetail && selectedDetail.counts;
  const countsLine = counts && Number.isFinite(counts.monitored)
    ? `<span class="inspector-counts">${counts.down || 0} down · ${counts.monitored} monitored</span>`
    : "";

  inspectorTitle.textContent = loc.name || "Location inspector";
  inspectorSubtitle.textContent = state.selectedGroupIds.length > 1
    ? `${state.selectedGroupIds.length} co-located locations`
    : "Location details";
  renderInspectorTabs();
  inspectorState.classList.add("hidden");
  inspectorContent.classList.remove("hidden");

  let equipmentBody;
  if (inspectorLoading) {
    equipmentBody = `<div class="inspector-empty">Loading equipment and health details…</div>`;
  } else if (inspectorError) {
    equipmentBody = `<div class="inspector-error">
      Could not load details: ${escHtml(inspectorError)}
      <div class="inspector-actions-row">
        <button id="inspector-retry" class="secondary-btn" type="button">Retry</button>
      </div>
    </div>`;
  } else if (devices.length === 0) {
    equipmentBody = `<div class="inspector-empty">No equipment found for this location.</div>`;
  } else {
    const filterCounts = {
      all: devices.length,
      active: summary.active,
      down: summary.down,
      unknown: summary.unknown,
    };
    const filterButtons = ["all", "active", "down", "unknown"]
      .map(
        (key) =>
          `<button class="device-filter-btn${inspectorDeviceFilter === key ? " active" : ""}" type="button" data-device-filter="${key}" aria-pressed="${inspectorDeviceFilter === key ? "true" : "false"}">${deviceFilterLabel(key)} (${filterCounts[key] || 0})</button>`
      )
      .join("");
    const cards = filteredDevices.map(deviceCard).join("");
    equipmentBody = `<div class="inspector-device-toolbar">
      <div class="device-filters" aria-label="Equipment health filters">${filterButtons}</div>
      <input id="inspector-device-search" class="inspector-device-search" type="search" value="${escHtml(inspectorDeviceSearch)}" placeholder="Search equipment" aria-label="Search equipment" />
    </div>
    ${filteredDevices.length > 0
      ? `<ul class="inspector-device-list" aria-label="Equipment list">${cards}</ul>`
      : `<div class="inspector-empty">No equipment matches the current filter.</div>`}`;
  }

  let asnBody;
  if (inspectorLoading) {
    asnBody = `<div class="inspector-empty">Loading ASN details…</div>`;
  } else if (inspectorError) {
    asnBody = `<div class="inspector-empty">Retry loading to retrieve ASN details.</div>`;
  } else if (asns.length === 0) {
    asnBody = `<div class="inspector-empty">No ASN or network information found for this location.</div>`;
  } else {
    asnBody = asns
      .map(
        (asn) =>
          `<span class="asn-tag" title="${escHtml(asn.description || "")}">AS${escHtml(asn.asn)}${asn.tenant ? ` · ${escHtml(asn.tenant)}` : ""}</span>`
      )
      .join("");
  }

  const circuits = selectedDetail && Array.isArray(selectedDetail.circuits) ? selectedDetail.circuits : [];
  const circuitsError = selectedDetail ? selectedDetail.circuits_error || "" : "";
  let circuitBody;
  if (inspectorLoading) {
    circuitBody = `<div class="inspector-empty">Loading circuits…</div>`;
  } else if (inspectorError) {
    circuitBody = `<div class="inspector-empty">Retry loading to retrieve circuits.</div>`;
  } else if (circuitsError) {
    circuitBody = `<div class="inspector-empty">${escHtml(circuitsError)}</div>`;
  } else if (circuits.length === 0) {
    circuitBody = `<div class="inspector-empty">No circuits found for this location.</div>`;
  } else {
    circuitBody = `<ul class="inspector-device-list" aria-label="Circuit list">${circuits.map(buildCircuitCard).join("")}</ul>`;
  }
  const circuitCount = !inspectorLoading && !inspectorError && circuits.length > 0 ? ` (${circuits.length})` : "";

  inspectorContent.innerHTML = `
    <div class="inspector-section">
      <div class="inspector-header-row">
        <div class="inspector-status-group">
          <span class="popup-badge ${badgeClass(statusLabel)}">${escHtml(statusLabel)}</span>
          ${alertPill}
          ${countsLine}
        </div>
      </div>
      ${buildAlertBanner(alert)}
      <div class="inspector-actions-row">
        ${buildNautobotLink(loc)}
      </div>
    </div>

    <section class="inspector-section inspector-meta" aria-labelledby="inspector-meta-title">
      <div id="inspector-meta-title" class="inspector-section-title">Location metadata</div>
      ${primaryRows || `<div class="inspector-empty">No primary metadata available for this location.</div>`}
      ${secondaryRows ? `<details><summary>More metadata</summary>${secondaryRows}</details>` : ""}
    </section>

    <section class="inspector-section" aria-labelledby="inspector-equipment-title">
      <div id="inspector-equipment-title" class="inspector-section-title">Network equipment</div>
      ${equipmentBody}
    </section>

    <section class="inspector-section" aria-labelledby="inspector-circuits-title">
      <div id="inspector-circuits-title" class="inspector-section-title">Circuits${circuitCount}</div>
      ${circuitBody}
    </section>

    <section class="inspector-section" aria-labelledby="inspector-asn-title">
      <div id="inspector-asn-title" class="inspector-section-title">ASN and network information</div>
      ${asnBody}
    </section>
  `;
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
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const detail = await resp.json();
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

export function openInspectorForLocation(locId, options = {}) {
  const loc = getLocationById(locId);
  if (!loc) return;
  const locationChanged = locId !== state.selectedLocationId;

  state.selectedLocationId = locId;
  state.selectedGroupIds = options.groupIds && options.groupIds.length > 0 ? [...options.groupIds] : [locId];
  state.selectedDetail = detailCache.get(locId) || null;
  state.inspectorError = "";
  state.inspectorLoading = !state.selectedDetail;
  if (locationChanged) {
    state.inspectorDeviceFilter = "all";
    state.inspectorDeviceSearch = "";
  }
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
    inspectorDeviceFilter: "all",
    inspectorDeviceSearch: "",
  });
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
