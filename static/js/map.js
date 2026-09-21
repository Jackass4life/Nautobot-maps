/* global L */
"use strict";

// ---------------------------------------------------------------------------
// Map initialisation
// ---------------------------------------------------------------------------
const map = L.map("map", {
  center: [20, 0],
  zoom: 3,
  preferCanvas: true,
});

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
}).addTo(map);

// ---------------------------------------------------------------------------
// Marker icon factory
// ---------------------------------------------------------------------------
function makeIcon(color, pulse) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 36">
    <path d="M12 0C5.373 0 0 5.373 0 12c0 9 12 24 12 24S24 21 24 12C24 5.373 18.627 0 12 0z"
          fill="${color}" stroke="#fff" stroke-width="1.5"/>
    <circle cx="12" cy="12" r="4.5" fill="#fff"/>
  </svg>`;
  const cls = pulse ? ' class="marker-pulse"' : '';
  return L.divIcon({
    html: `<div${cls} style="width:24px;height:36px">${svg}</div>`,
    iconSize: [24, 36],
    iconAnchor: [12, 36],
    popupAnchor: [0, -36],
    className: "",
  });
}

const ICONS = {
  active:   makeIcon("#2ecc71"),
  planned:  makeIcon("#f0a500"),
  other:    makeIcon("#888888"),
  search:   makeIcon("#e74c3c"),
  medium:   makeIcon("#ff8c00"),
  critical: makeIcon("#e74c3c", true),
};

function iconForStatus(status) {
  const s = (status || "").toLowerCase();
  if (s === "active") return ICONS.active;
  if (s === "planned") return ICONS.planned;
  return ICONS.other;
}

function badgeClass(status) {
  const s = (status || "").toLowerCase();
  if (s === "active") return "badge-active";
  if (s === "planned") return "badge-planned";
  return "badge-other";
}

// ---------------------------------------------------------------------------
// Location inspector helpers
// ---------------------------------------------------------------------------
function escHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function inspectorRow(label, value) {
  if (!value && value !== 0) return "";
  return `<div class="inspector-row">
    <span class="inspector-row-label">${escHtml(label)}</span>
    <span class="inspector-row-value">${escHtml(String(value))}</span>
  </div>`;
}

function normalizeDeviceHealth(status) {
  const value = (status || "").trim().toLowerCase();
  if (["active", "online", "up"].includes(value)) return "active";
  if (value.includes("offline") || ["down", "failed", "inactive"].includes(value)) return "down";
  return "unknown";
}

function deviceFilterLabel(filterKey) {
  if (filterKey === "active") return "Active";
  if (filterKey === "down") return "Down";
  if (filterKey === "unknown") return "Unknown";
  return "All";
}

function buildNautobotLink(loc) {
  if (!window.NAUTOBOT_URL || !loc.id) return "";
  return `<a class="nautobot-link" href="${escHtml(window.NAUTOBOT_URL)}/dcim/locations/${encodeURIComponent(loc.id)}/" target="_blank" rel="noopener noreferrer">Open in Nautobot ↗</a>`;
}

function buildAlertBanner(alert) {
  if (!alert || alert.level === "ok") return "";
  const lvl = alert.level === "critical" ? "critical" : "medium";
  const icon = lvl === "critical" ? "🔴" : "🟠";
  return `<div class="alert-banner alert-${escHtml(lvl)}">
    <span class="alert-banner-icon">${icon}</span>
    <div>
      <div class="alert-banner-level">${escHtml(lvl.toUpperCase())}</div>
      ${alert.reason ? `<div class="alert-banner-reason">${escHtml(alert.reason)}</div>` : ""}
    </div>
  </div>`;
}

function buildHealthSummary(devices) {
  const counts = { total: devices.length, active: 0, down: 0, unknown: 0 };
  for (const device of devices) {
    counts[normalizeDeviceHealth(device.status)] += 1;
  }
  return counts;
}

function filterDevicesForInspector(devices, filterKey, searchTerm) {
  const query = (searchTerm || "").trim().toLowerCase();
  return devices.filter((device) => {
    if (filterKey !== "all" && normalizeDeviceHealth(device.status) !== filterKey) return false;
    if (!query) return true;
    const haystack = [
      device.name,
      device.status,
      device.manufacturer,
      device.device_type,
      device.role,
      device.platform,
      device.serial,
      device.tenant,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
}

// ---------------------------------------------------------------------------
// Co-located site helpers (multiple sites at the same coordinates)
// ---------------------------------------------------------------------------
function groupByCoords(locations) {
  const groups = {};
  for (const loc of locations) {
    if (loc.latitude == null || loc.longitude == null) continue;
    const key = `${loc.latitude.toFixed(4)},${loc.longitude.toFixed(4)}`;
    if (!groups[key]) groups[key] = [];
    groups[key].push(loc);
  }
  return groups;
}

function makeStackedIcon(count, alertLevel) {
  const color = alertLevel === "critical" ? "#e74c3c"
              : alertLevel === "medium"   ? "#ff8c00"
              : "#3388ff";
  const pulse = alertLevel === "critical";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 36">
    <path d="M12 0C5.373 0 0 5.373 0 12c0 9 12 24 12 24S24 21 24 12C24 5.373 18.627 0 12 0z"
          fill="${color}" stroke="#fff" stroke-width="1.5"/>
    <circle cx="12" cy="12" r="4.5" fill="#fff"/>
  </svg>`;
  const cls = pulse ? ' class="marker-pulse"' : '';
  return L.divIcon({
    html: `<div${cls} style="width:24px;height:36px;position:relative">${svg}<span class="colocated-badge">${count}</span></div>`,
    iconSize: [24, 36],
    iconAnchor: [12, 36],
    popupAnchor: [0, -36],
    className: "",
  });
}

// ---------------------------------------------------------------------------
// Fetch locations and render markers
// ---------------------------------------------------------------------------
const markerLayer = L.layerGroup().addTo(map);
let allLocations = [];
const CLUSTER_THRESHOLD = 100;
const initialLocationId = new URLSearchParams(window.location.search).get("location_id");
let initialLocationOpened = false;

const markerByLocId = {};
const clusterByLocId = {};
const colocGroupByLocId = {};
const locationAlerts = {};
const detailCache = new Map();
const inFlightDetailRequests = new Map();

const inspector = document.getElementById("location-inspector");
const inspectorBackdrop = document.getElementById("inspector-backdrop");
const inspectorTitle = document.getElementById("inspector-title");
const inspectorSubtitle = document.getElementById("inspector-subtitle");
const inspectorTabs = document.getElementById("inspector-site-tabs");
const inspectorState = document.getElementById("inspector-state");
const inspectorContent = document.getElementById("inspector-content");
const inspectorCloseButton = document.getElementById("inspector-close");
const inspectorPinButton = document.getElementById("inspector-pin");

let selectedLocationId = null;
let selectedGroupIds = [];
let selectedDetail = null;
let inspectorError = "";
let inspectorLoading = false;
let inspectorPinned = true;
let inspectorDeviceFilter = "all";
let inspectorDeviceSearch = "";
let lastInspectorTrigger = null;
let suppressNextMapClickClose = false;
let pendingLocationOpen = null;
let resizeAnimationFrame = null;

function isMobileViewport() {
  return window.matchMedia("(max-width: 640px)").matches;
}

function syncInspectorBackdrop() {
  const shouldShow = isInspectorOpen() && isMobileViewport();
  inspectorBackdrop.classList.toggle("hidden", !shouldShow);
}

function isInspectorOpen() {
  return !inspector.classList.contains("hidden");
}

function setInspectorPinned(pinned) {
  inspectorPinned = Boolean(pinned);
  inspectorPinButton.textContent = inspectorPinned ? "Pinned" : "Unpinned";
  inspectorPinButton.setAttribute("aria-pressed", inspectorPinned ? "true" : "false");
  inspectorPinButton.setAttribute(
    "aria-label",
    inspectorPinned ? "Unpin location inspector" : "Pin location inspector"
  );
}

function updateLocationQuery(locId) {
  const params = new URLSearchParams(window.location.search);
  if (locId) params.set("location_id", locId);
  else params.delete("location_id");
  const query = params.toString();
  const nextUrl = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  window.history.replaceState({}, "", nextUrl);
}

function getLocationById(locId) {
  return allLocations.find((loc) => loc.id === locId) || null;
}

function getSelectedGroupLocations() {
  const ids = selectedGroupIds.length > 0 ? selectedGroupIds : selectedLocationId ? [selectedLocationId] : [];
  return ids
    .map((locId) => getLocationById(locId))
    .filter(Boolean);
}

function getColocatedLocationIds(locId) {
  const loc = getLocationById(locId);
  if (!loc || loc.latitude == null || loc.longitude == null) return [locId];
  const key = `${loc.latitude.toFixed(4)},${loc.longitude.toFixed(4)}`;
  return allLocations
    .filter(
      (candidate) =>
        candidate.latitude != null
        && candidate.longitude != null
        && `${candidate.latitude.toFixed(4)},${candidate.longitude.toFixed(4)}` === key
    )
    .map((candidate) => candidate.id);
}

function renderInspectorTabs() {
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
        `<button class="inspector-site-tab${loc.id === selectedLocationId ? " active" : ""}" type="button" data-location-id="${escHtml(loc.id)}" aria-pressed="${loc.id === selectedLocationId ? "true" : "false"}">${escHtml(loc.name)}</button>`
    )
    .join("");
}

function renderInspectorContent() {
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
    inspectorRow("Tenant", loc.tenant),
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
  const alertPill = alert && alert.level !== "ok"
    ? `<span class="inspector-pill inspector-alert-pill${alert.level === "critical" ? " is-critical" : ""}">${escHtml(alert.level.toUpperCase())}</span>`
    : `<span class="inspector-pill">${escHtml("Healthy")}</span>`;

  inspectorTitle.textContent = loc.name || "Location inspector";
  inspectorSubtitle.textContent = selectedGroupIds.length > 1
    ? `${selectedGroupIds.length} co-located locations`
    : "Location details";
  renderInspectorTabs();
  inspectorState.classList.add("hidden");
  inspectorContent.classList.remove("hidden");

  let equipmentBody = "";
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
    const cards = filteredDevices
      .map((device) => {
        const health = normalizeDeviceHealth(device.status);
        const badgeClassName = health === "active"
          ? "device-status-active"
          : health === "down"
            ? "device-status-offline"
            : "device-status-other";
        const hardware = [device.manufacturer, device.device_type].filter(Boolean).map(escHtml).join(" · ");
        return `<li class="device-card">
          <div class="device-card-header">
            <div class="device-card-title">${escHtml(device.name || "Unnamed device")}</div>
            <span class="device-status-badge ${badgeClassName}">${escHtml(device.status || "Unknown")}</span>
          </div>
          <div class="device-card-body">
            ${hardware ? `<div class="device-meta">${hardware}</div>` : ""}
            ${device.role ? `<div class="device-meta device-role">Role: ${escHtml(device.role)}</div>` : ""}
            ${device.platform ? `<div class="device-meta device-platform">Software: ${escHtml(device.platform)}</div>` : ""}
            ${device.serial ? `<div class="device-meta">Serial: ${escHtml(device.serial)}</div>` : ""}
            ${device.tenant ? `<div class="device-meta">Tenant: ${escHtml(device.tenant)}</div>` : ""}
          </div>
        </li>`;
      })
      .join("");
    equipmentBody = `<div class="inspector-device-toolbar">
      <div class="device-filters" aria-label="Equipment health filters">${filterButtons}</div>
      <input id="inspector-device-search" class="inspector-device-search" type="search" value="${escHtml(inspectorDeviceSearch)}" placeholder="Search equipment" aria-label="Search equipment" />
    </div>
    ${filteredDevices.length > 0
      ? `<ul class="inspector-device-list" aria-label="Equipment list">${cards}</ul>`
      : `<div class="inspector-empty">No equipment matches the current filter.</div>`}`;
  }

  let asnBody = "";
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

  const loadingSummary = inspectorLoading
    ? `<div class="inspector-empty">Loading health summary…</div>`
    : "";
  const errorSummary = !inspectorLoading && inspectorError
    ? `<div class="inspector-empty">Health summary unavailable until details load successfully.</div>`
    : "";

  inspectorContent.innerHTML = `
    <div class="inspector-section">
      <div class="inspector-header-row">
        <div class="inspector-status-group">
          <span class="popup-badge ${badgeClass(statusLabel)}">${escHtml(statusLabel)}</span>
          ${alertPill}
        </div>
      </div>
      ${buildAlertBanner(alert)}
      <div class="inspector-actions-row">
        ${buildNautobotLink(loc)}
      </div>
    </div>

    <section class="inspector-section" aria-labelledby="inspector-health-title">
      <div id="inspector-health-title" class="inspector-section-title">Health summary</div>
      ${loadingSummary || errorSummary || `
        <div class="inspector-summary-grid">
          <div class="inspector-summary-card">
            <div class="inspector-summary-label">Total</div>
            <div class="inspector-summary-value">${summary.total}</div>
          </div>
          <div class="inspector-summary-card">
            <div class="inspector-summary-label">Active</div>
            <div class="inspector-summary-value">${summary.active}</div>
          </div>
          <div class="inspector-summary-card">
            <div class="inspector-summary-label">Down</div>
            <div class="inspector-summary-value">${summary.down}</div>
          </div>
          <div class="inspector-summary-card">
            <div class="inspector-summary-label">Unknown</div>
            <div class="inspector-summary-value">${summary.unknown}</div>
          </div>
        </div>`}
    </section>

    <section class="inspector-section inspector-meta" aria-labelledby="inspector-meta-title">
      <div id="inspector-meta-title" class="inspector-section-title">Location metadata</div>
      ${primaryRows || `<div class="inspector-empty">No primary metadata available for this location.</div>`}
      ${secondaryRows ? `<details><summary>More metadata</summary>${secondaryRows}</details>` : ""}
    </section>

    <section class="inspector-section" aria-labelledby="inspector-equipment-title">
      <div id="inspector-equipment-title" class="inspector-section-title">Network equipment</div>
      ${equipmentBody}
    </section>

    <section class="inspector-section" aria-labelledby="inspector-asn-title">
      <div id="inspector-asn-title" class="inspector-section-title">ASN and network information</div>
      ${asnBody}
    </section>
  `;
}

function openInspectorForLocation(locId, options = {}) {
  const loc = getLocationById(locId);
  if (!loc) return;
  const locationChanged = locId !== selectedLocationId;

  selectedLocationId = locId;
  selectedGroupIds = options.groupIds && options.groupIds.length > 0 ? [...options.groupIds] : [locId];
  selectedDetail = detailCache.get(locId) || null;
  inspectorError = "";
  inspectorLoading = !selectedDetail;
  if (locationChanged) {
    inspectorDeviceFilter = "all";
    inspectorDeviceSearch = "";
  }
  if (options.focusElement) {
    lastInspectorTrigger = options.focusElement;
  }

  document.body.classList.add("inspector-open");
  inspector.classList.remove("hidden");
  inspector.setAttribute("aria-hidden", "false");
  syncInspectorBackdrop();
  updateLocationQuery(locId);
  renderInspectorContent();

  if (options.focusInspector !== false) {
    inspectorCloseButton.focus();
  }

  if (selectedDetail) {
    if (selectedDetail.alert) updateMarkerForAlert(locId, selectedDetail.alert.level);
    return;
  }

  loadLocationDetail(locId)
    .then((detail) => {
      if (selectedLocationId !== locId) return;
      selectedDetail = detail;
      inspectorLoading = false;
      inspectorError = "";
      renderInspectorContent();
      if (detail.alert) updateMarkerForAlert(locId, detail.alert.level);
    })
    .catch((err) => {
      if (selectedLocationId !== locId) return;
      selectedDetail = null;
      inspectorLoading = false;
      inspectorError = err.message || "Unknown error";
      renderInspectorContent();
    });
}

function closeInspector(options = {}) {
  document.body.classList.remove("inspector-open");
  inspector.classList.add("hidden");
  inspector.setAttribute("aria-hidden", "true");
  syncInspectorBackdrop();
  selectedLocationId = null;
  selectedGroupIds = [];
  selectedDetail = null;
  inspectorError = "";
  inspectorLoading = false;
  inspectorDeviceFilter = "all";
  inspectorDeviceSearch = "";
  renderInspectorContent();
  updateLocationQuery(null);

  if (options.restoreFocus !== false && lastInspectorTrigger && typeof lastInspectorTrigger.focus === "function") {
    lastInspectorTrigger.focus();
  }
}

function wireMarkerAccessibility(marker, label, activate) {
  marker.on("add", () => {
    const el = marker.getElement();
    if (!el || el.dataset.inspectorWired === "true") return;
    el.dataset.inspectorWired = "true";
    el.setAttribute("role", "button");
    el.setAttribute("tabindex", "0");
    el.setAttribute("aria-label", label);
    el.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      activate(el);
    });
  });
}

function handleMarkerActivation(marker, locId, groupIds, focusElement) {
  suppressNextMapClickClose = true;
  setTimeout(() => {
    suppressNextMapClickClose = false;
  }, 0);
  map.panTo(marker.getLatLng());
  openInspectorForLocation(locId, { groupIds, focusElement, focusInspector: true });
}

function addColocatedMarker(locations) {
  const first = locations[0];
  const ids = locations.map((loc) => loc.id);
  const marker = L.marker([first.latitude, first.longitude], {
    icon: makeStackedIcon(locations.length),
    title: locations.map((loc) => loc.name).join(", "),
  });

  marker.on("click", (event) => {
    if (event && event.originalEvent) {
      event.originalEvent.preventDefault();
      event.originalEvent.stopPropagation();
    }
    handleMarkerActivation(marker, first.id, ids, marker.getElement());
  });

  wireMarkerAccessibility(
    marker,
    `Inspect ${locations.length} co-located locations`,
    (focusElement) => handleMarkerActivation(marker, first.id, ids, focusElement)
  );

  for (const loc of locations) {
    markerByLocId[loc.id] = marker;
    colocGroupByLocId[loc.id] = ids;
  }

  marker.addTo(markerLayer);
}

function openLocationById(locId, options = {}) {
  const loc = getLocationById(locId);
  if (!loc) return false;
  const expectedGroupIds = getColocatedLocationIds(locId);
  const shouldResolveColocatedGroup =
    expectedGroupIds.length > 1 && !colocGroupByLocId[locId] && map.getZoom() < 8;
  if (shouldResolveColocatedGroup) {
    pendingLocationOpen = { locId, options };
    map.flyTo([loc.latitude, loc.longitude], Math.max(map.getZoom(), 8), { duration: 0.8 });
    return false;
  }

  const marker = markerByLocId[locId];
  if (!marker) {
    const clusterMarker = clusterByLocId[locId];
    if (!clusterMarker) return false;
    pendingLocationOpen = { locId, options };
    map.flyTo(clusterMarker.getLatLng(), Math.max(map.getZoom(), 8), { duration: 0.8 });
    return false;
  }

  map.flyTo([loc.latitude, loc.longitude], Math.max(map.getZoom(), 13), { duration: 0.8 });
  openInspectorForLocation(locId, {
    groupIds: colocGroupByLocId[locId] || expectedGroupIds,
    focusElement: options.focusElement || marker.getElement(),
    focusInspector: options.focusInspector,
  });
  return true;
}

function openLocationFromQuery() {
  if (!initialLocationId || initialLocationOpened) return;
  if (openLocationById(initialLocationId, { focusInspector: false, markInitial: true })) {
    initialLocationOpened = true;
  }
}

function updateMarkerForAlert(locId, alertLevel) {
  locationAlerts[locId] = alertLevel;
  const marker = markerByLocId[locId];
  if (!marker) return;

  const groupIds = colocGroupByLocId[locId];
  if (groupIds) {
    const groupLevel = groupIds.reduce((highest, id) => {
      const level = locationAlerts[id] || "ok";
      if (level === "critical") return "critical";
      if (level === "medium" && highest !== "critical") return "medium";
      return highest;
    }, "ok");
    if (groupLevel !== "ok") marker.setIcon(makeStackedIcon(groupIds.length, groupLevel));
    return;
  }

  if (alertLevel === "critical") marker.setIcon(ICONS.critical);
  else if (alertLevel === "medium") marker.setIcon(ICONS.medium);
}

async function loadLocationDetail(locId) {
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

async function loadLocations() {
  showLoading(true, "Loading locations from Nautobot…");
  try {
    const resp = await fetch("/api/locations");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (data.error) throw new Error(data.error);
    allLocations = data.locations || [];
    showLoading(true, `Processing ${allLocations.length} locations…`);
    populateFilters(allLocations);
    applyFilters();
  } catch (err) {
    showError("Failed to load locations: " + err.message);
  } finally {
    showLoading(false);
  }
}

function renderMarkers(locations, searchMarker) {
  markerLayer.clearLayers();
  for (const key of Object.keys(markerByLocId)) delete markerByLocId[key];
  for (const key of Object.keys(clusterByLocId)) delete clusterByLocId[key];
  for (const key of Object.keys(colocGroupByLocId)) delete colocGroupByLocId[key];

  if (searchMarker) {
    const { lat, lon } = searchMarker;
    const searchIcon = L.divIcon({
      html: `<div style="width:18px;height:18px;background:#e74c3c;border:3px solid #fff;border-radius:50%;box-shadow:0 0 6px rgba(0,0,0,.5)"></div>`,
      iconSize: [18, 18],
      iconAnchor: [9, 9],
      className: "",
    });
    L.marker([lat, lon], { icon: searchIcon })
      .bindPopup(`<div class="popup-content"><div class="popup-title" aria-label="Search location">&#128269; Search point</div></div>`)
      .addTo(markerLayer);

    L.circle([lat, lon], {
      radius: 5000,
      color: "#e74c3c",
      fillColor: "#e74c3c",
      fillOpacity: 0.05,
      weight: 2,
      dashArray: "6 4",
    }).addTo(markerLayer);
  }

  if (locations.length > CLUSTER_THRESHOLD && !searchMarker) {
    renderMarkersWithClustering(locations);
  } else {
    renderMarkersSimple(locations);
  }
}

function renderMarkersSimple(locations) {
  const groups = groupByCoords(locations);
  for (const key in groups) {
    const group = groups[key];
    if (group.length === 1) addMarker(group[0]);
    else addColocatedMarker(group);
  }
}

function renderMarkersWithClustering(locations) {
  const currentZoom = map.getZoom();
  const gridSize = currentZoom < 5 ? 2 : currentZoom < 8 ? 1 : 0.5;
  const clusters = {};

  for (const loc of locations) {
    const gridLat = Math.floor(loc.latitude / gridSize) * gridSize;
    const gridLon = Math.floor(loc.longitude / gridSize) * gridSize;
    const key = `${gridLat},${gridLon}`;
    if (!clusters[key]) clusters[key] = [];
    clusters[key].push(loc);
  }

  for (const key in clusters) {
    const group = clusters[key];

    if (group.length === 1) {
      addMarker(group[0]);
    } else if (currentZoom < 8) {
      const lat = group.reduce((sum, loc) => sum + loc.latitude, 0) / group.length;
      const lon = group.reduce((sum, loc) => sum + loc.longitude, 0) / group.length;
      const clusterIcon = L.divIcon({
        html: `<div style="width:40px;height:40px;background:#3388ff;color:white;border:3px solid #fff;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:bold;box-shadow:0 0 8px rgba(0,0,0,.4)">${group.length}</div>`,
        iconSize: [40, 40],
        iconAnchor: [20, 20],
        className: "",
      });

      const clusterMarker = L.marker([lat, lon], { icon: clusterIcon });
      clusterMarker.on("click", () => {
        map.setView([lat, lon], Math.min(currentZoom + 3, 15));
      });

      const listedLocations = group.map((loc) => `<li>${escHtml(loc.name)}</li>`).slice(0, 10).join("");
      const more = group.length > 10 ? `<li style="color:#888">… and ${group.length - 10} more</li>` : "";
      clusterMarker.bindPopup(
        `<div class="popup-content">
          <div class="popup-title">${group.length} locations</div>
          <div style="font-size:0.85rem;margin-top:8px">Click to zoom in</div>
          <ul style="margin:8px 0 0 0;padding-left:20px;font-size:0.8rem;max-height:150px;overflow-y:auto">
            ${listedLocations}${more}
          </ul>
        </div>`,
        { keepInView: true }
      );
      for (const loc of group) {
        clusterByLocId[loc.id] = clusterMarker;
      }
      clusterMarker.addTo(markerLayer);
    } else {
      const subGroups = groupByCoords(group);
      for (const subKey in subGroups) {
        const subGroup = subGroups[subKey];
        if (subGroup.length === 1) addMarker(subGroup[0]);
        else addColocatedMarker(subGroup);
      }
    }
  }
}

function addMarker(loc) {
  const marker = L.marker([loc.latitude, loc.longitude], {
    icon: iconForStatus(loc.status),
    title: loc.name,
  });

  marker.on("click", (event) => {
    if (event && event.originalEvent) {
      event.originalEvent.preventDefault();
      event.originalEvent.stopPropagation();
    }
    handleMarkerActivation(marker, loc.id, [loc.id], marker.getElement());
  });

  wireMarkerAccessibility(
    marker,
    `Inspect ${loc.name}`,
    (focusElement) => handleMarkerActivation(marker, loc.id, [loc.id], focusElement)
  );

  markerByLocId[loc.id] = marker;
  marker.addTo(markerLayer);
}

map.on("zoomend", () => {
  if (allLocations.length > CLUSTER_THRESHOLD) {
    applyFilters();
  }
  if (pendingLocationOpen) {
    const pending = pendingLocationOpen;
    pendingLocationOpen = null;
    const opened = openLocationById(pending.locId, pending.options);
    if (pending.options.markInitial && opened) {
      initialLocationOpened = true;
    }
  }
});

map.on("click", () => {
  if (!isInspectorOpen() || inspectorPinned || suppressNextMapClickClose) return;
  closeInspector({ restoreFocus: false });
});

window.addEventListener("resize", () => {
  if (resizeAnimationFrame != null) return;
  resizeAnimationFrame = window.requestAnimationFrame(() => {
    resizeAnimationFrame = null;
    syncInspectorBackdrop();
  });
});

inspectorCloseButton.addEventListener("click", () => closeInspector());
inspectorPinButton.addEventListener("click", () => setInspectorPinned(!inspectorPinned));
inspectorBackdrop.addEventListener("click", () => {
  if (!inspectorPinned) closeInspector({ restoreFocus: false });
});
inspectorTabs.addEventListener("click", (event) => {
  const button = event.target.closest("[data-location-id]");
  if (!button) return;
  openInspectorForLocation(button.dataset.locationId, {
    groupIds: selectedGroupIds,
    focusInspector: false,
  });
});
inspectorContent.addEventListener("click", (event) => {
  const retryButton = event.target.closest("#inspector-retry");
  if (retryButton && selectedLocationId) {
    detailCache.delete(selectedLocationId);
    openInspectorForLocation(selectedLocationId, {
      groupIds: selectedGroupIds,
      focusInspector: false,
    });
    return;
  }

  const filterButton = event.target.closest("[data-device-filter]");
  if (!filterButton) return;
  inspectorDeviceFilter = filterButton.dataset.deviceFilter || "all";
  renderInspectorContent();
});
inspectorContent.addEventListener("input", (event) => {
  if (event.target.id !== "inspector-device-search") return;
  const selectionStart = event.target.selectionStart;
  const selectionEnd = event.target.selectionEnd;
  inspectorDeviceSearch = event.target.value || "";
  renderInspectorContent();
  const searchInput = document.getElementById("inspector-device-search");
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
setInspectorPinned(true);
renderInspectorContent();

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------
const filterStatus = document.getElementById("filter-status");
const filterType = document.getElementById("filter-type");
const filterParent = document.getElementById("filter-parent");
const filterTenant = document.getElementById("filter-tenant");
const filterTenantGroup = document.getElementById("filter-tenant-group");
const quickStatusButtons = Array.from(document.querySelectorAll("[data-quick-status]"));
const themeToggle = document.getElementById("theme-toggle");

function populateFilters(locations) {
  const statuses = [...new Set(locations.map((l) => l.status).filter(Boolean))].sort();
  const types = [...new Set(locations.map((l) => l.location_type).filter(Boolean))].sort();
  const parents = [...new Set(locations.map((l) => l.parent).filter(Boolean))].sort();
  const tenants = [...new Set(locations.map((l) => l.tenant).filter(Boolean))].sort();
  const tenantGroups = [...new Set(locations.map((l) => l.tenant_group).filter(Boolean))].sort();

  filterStatus.innerHTML = '<option value="">All statuses</option>';
  for (const status of statuses) {
    const opt = document.createElement("option");
    opt.value = status;
    opt.textContent = status;
    filterStatus.appendChild(opt);
  }

  filterType.innerHTML = '<option value="">All types</option>';
  for (const type of types) {
    const opt = document.createElement("option");
    opt.value = type;
    opt.textContent = type;
    filterType.appendChild(opt);
  }

  filterParent.innerHTML = '<option value="">All parents</option>';
  for (const parent of parents) {
    const opt = document.createElement("option");
    opt.value = parent;
    opt.textContent = parent;
    filterParent.appendChild(opt);
  }

  filterTenant.innerHTML = '<option value="">All tenants</option>';
  for (const tenant of tenants) {
    const opt = document.createElement("option");
    opt.value = tenant;
    opt.textContent = tenant;
    filterTenant.appendChild(opt);
  }

  filterTenantGroup.innerHTML = '<option value="">All tenant groups</option>';
  for (const group of tenantGroups) {
    const opt = document.createElement("option");
    opt.value = group;
    opt.textContent = group;
    filterTenantGroup.appendChild(opt);
  }
}

function applyFilters() {
  const statusVal = filterStatus.value;
  const typeVal = filterType.value;
  const parentVal = filterParent.value;
  const tenantVal = filterTenant.value;
  const tenantGroupVal = filterTenantGroup.value;

  const filtered = allLocations.filter((loc) => {
    if (statusVal && loc.status !== statusVal) return false;
    if (typeVal && loc.location_type !== typeVal) return false;
    if (parentVal && loc.parent !== parentVal) return false;
    if (tenantVal && loc.tenant !== tenantVal) return false;
    if (tenantGroupVal && loc.tenant_group !== tenantGroupVal) return false;
    return true;
  });

  renderMarkers(filtered);
  updateLocationCount(filtered.length);
  openLocationFromQuery();
  syncQuickStatusButtons();
}

filterStatus.addEventListener("change", applyFilters);
filterType.addEventListener("change", applyFilters);
filterParent.addEventListener("change", applyFilters);
filterTenant.addEventListener("change", applyFilters);
filterTenantGroup.addEventListener("change", applyFilters);

// Clear filters button
document.getElementById("clear-filters").addEventListener("click", () => {
  filterStatus.value = "";
  filterType.value = "";
  filterParent.value = "";
  filterTenant.value = "";
  filterTenantGroup.value = "";
  applyFilters();
});

function syncQuickStatusButtons() {
  quickStatusButtons.forEach((button) => {
    const isActive = (button.dataset.quickStatus || "") === filterStatus.value;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

quickStatusButtons.forEach((button) => {
  button.addEventListener("click", () => {
    filterStatus.value = button.dataset.quickStatus || "";
    applyFilters();
  });
});

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  if (themeToggle) {
    const darkEnabled = theme === "dark";
    themeToggle.textContent = darkEnabled ? "Light mode" : "Dark mode";
    themeToggle.setAttribute("aria-label", darkEnabled ? "Switch to light mode" : "Switch to dark mode");
    themeToggle.setAttribute("aria-pressed", darkEnabled ? "true" : "false");
  }
}

function initTheme() {
  let theme = "light";
  try {
    const stored = localStorage.getItem("nautobot-maps-theme");
    if (stored === "dark" || stored === "light") {
      theme = stored;
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      theme = "dark";
    }
  } catch (_err) {
    theme = "light";
  }
  applyTheme(theme);
}

if (themeToggle) {
  initTheme();
  themeToggle.addEventListener("click", () => {
    const nextTheme = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(nextTheme);
    try {
      localStorage.setItem("nautobot-maps-theme", nextTheme);
    } catch (_err) {
      // noop
    }
  });
}

const searchInput = document.getElementById("search-input");
const searchBtn = document.getElementById("search-btn");
const searchResults = document.getElementById("search-results");
const searchResultsHeader = document.getElementById("search-results-header");
const searchResultsList = document.getElementById("search-results-list");

async function doSearch() {
  const query = searchInput.value.trim();
  if (!query) return;

  searchBtn.disabled = true;
  searchBtn.style.opacity = "0.5";

  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    const data = await resp.json();

    if (!resp.ok || data.error) {
      showError(data.error || `Search failed (HTTP ${resp.status})`);
      searchResults.classList.add("hidden");
      renderMarkers(allLocations);
      return;
    }

    const { search_lat, search_lon, count, locations } = data;

    // Re-render map with only the nearby locations highlighted
    renderMarkers(locations, { lat: search_lat, lon: search_lon });

    // Fly to search point
    map.flyTo([search_lat, search_lon], 11, { duration: 1.2 });

    // Populate sidebar results
    searchResultsHeader.textContent = `${count} location${count !== 1 ? "s" : ""} within 5 km`;
    searchResultsList.innerHTML = "";

    if (count === 0) {
      searchResultsList.innerHTML =
        '<li style="padding:10px 16px;color:var(--color-text-muted);font-size:.85rem">No Nautobot locations found within 5 km.</li>';
    } else {
      for (const loc of locations) {
        const li = document.createElement("li");
        li.innerHTML = `
          <div class="result-name">${escHtml(loc.name)}</div>
          <div class="result-meta">${[loc.location_type, loc.tenant].filter(Boolean).map(escHtml).join(" · ") || "—"}</div>
          <span class="result-distance">${loc.distance_km} km</span>`;
        li.addEventListener("click", () => {
          openLocationById(loc.id, { focusInspector: false, focusElement: li });
        });
        searchResultsList.appendChild(li);
      }
    }

    searchResults.classList.remove("hidden");
  } catch (err) {
    showError("Search error: " + err.message);
  } finally {
    searchBtn.disabled = false;
    searchBtn.style.opacity = "1";
  }
}

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

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------
function showLoading(visible, message) {
  const overlay = document.getElementById("loading-overlay");
  const text = document.getElementById("loading-text");
  if (message) {
    text.textContent = message;
  } else {
    text.textContent = "Loading locations from Nautobot…";
  }
  overlay.style.display = visible ? "flex" : "none";
}

function showError(message) {
  const toast = document.getElementById("error-toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(showError._timer);
  showError._timer = setTimeout(() => toast.classList.add("hidden"), 6000);
}

function updateLocationCount(count) {
  document.getElementById("location-count").textContent =
    `${count} location${count !== 1 ? "s" : ""} with GPS coordinates loaded`;
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
loadLocations();
