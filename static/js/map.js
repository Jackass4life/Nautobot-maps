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
function makeIcon(color) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 36">
    <path d="M12 0C5.373 0 0 5.373 0 12c0 9 12 24 12 24S24 21 24 12C24 5.373 18.627 0 12 0z"
          fill="${color}" stroke="#fff" stroke-width="1.5"/>
    <circle cx="12" cy="12" r="4.5" fill="#fff"/>
  </svg>`;
  return L.divIcon({
    html: `<div style="width:24px;height:36px">${svg}</div>`,
    iconSize: [24, 36],
    iconAnchor: [12, 36],
    popupAnchor: [0, -36],
    className: "",
  });
}

const ICONS = {
  active: makeIcon("#2ecc71"),
  planned: makeIcon("#f0a500"),
  other: makeIcon("#888888"),
  search: makeIcon("#e74c3c"),
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
// Popup helpers
// ---------------------------------------------------------------------------
function popupRow(label, value) {
  if (!value && value !== 0) return "";
  return `<div class="popup-row">
    <span class="popup-label">${label}</span>
    <span class="popup-value">${escHtml(String(value))}</span>
  </div>`;
}

function escHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function buildBasicPopup(loc) {
  const statusLabel = loc.status || "Unknown";
  const rows = [
    popupRow("Type", loc.location_type),
    popupRow("Parent", loc.parent),
    popupRow("Tenant", loc.tenant),
    popupRow("ASN", loc.asn),
    popupRow("Address", loc.physical_address),
    popupRow("Time zone", loc.time_zone),
    popupRow("Description", loc.description),
  ]
    .filter(Boolean)
    .join("");

  return `<div class="popup-content">
    <div class="popup-title">${escHtml(loc.name)}</div>
    <span class="popup-badge ${badgeClass(statusLabel)}">${escHtml(statusLabel)}</span>
    ${rows ? `<div class="popup-section">${rows}</div>` : ""}
    <div id="popup-detail-${escHtml(loc.id)}" class="popup-loading">
      Loading equipment &amp; ASN details…
    </div>
  </div>`;
}

function renderDetail(locId, detail) {
  const container = document.getElementById(`popup-detail-${locId}`);
  if (!container) return;

  let html = "";

  // ASNs
  if (detail.asns && detail.asns.length > 0) {
    const tags = detail.asns
      .map(
        (a) =>
          `<span class="asn-tag" title="${escHtml(a.description || "")}">AS${a.asn}${a.tenant ? " · " + escHtml(a.tenant) : ""}</span>`
      )
      .join("");
    html += `<div class="popup-section">
      <div class="popup-section-title">ASN(s)</div>
      ${tags}
    </div>`;
  }

  // Devices / network equipment – show ALL devices with status filter tabs
  if (detail.devices && detail.devices.length > 0) {
    const statuses = [...new Set(detail.devices.map((d) => d.status).filter(Boolean))].sort();

    // Only show filter tabs when multiple statuses exist
    const filterBtns = statuses.length > 1
      ? ['All', ...statuses]
          .map(
            (s) =>
              `<button class="device-filter-btn${s === 'All' ? ' active' : ''}" data-filter="${escHtml(s)}" aria-pressed="${s === 'All' ? 'true' : 'false'}">${escHtml(s)}</button>`
          )
          .join("")
      : "";

    const items = detail.devices
      .map((d) => {
        const st = (d.status || "").toLowerCase();
        const stClass =
          st === "active" ? "device-status-active"
          : st === "offline" ? "device-status-offline"
          : "device-status-other";
        const hwMeta = [d.manufacturer, d.device_type].filter(Boolean).map(escHtml).join(" · ");
        return `<li data-device-status="${escHtml(d.status || "")}">
          <div class="device-header">
            <span class="device-name">${escHtml(d.name)}</span>
            ${d.status ? `<span class="device-status-badge ${stClass}">${escHtml(d.status)}</span>` : ""}
          </div>
          ${hwMeta ? `<div class="device-meta">${hwMeta}</div>` : ""}
          ${d.role ? `<div class="device-meta device-role">Role: ${escHtml(d.role)}</div>` : ""}
          ${d.platform ? `<div class="device-meta device-platform">Software: ${escHtml(d.platform)}</div>` : ""}
          ${d.serial ? `<div class="device-meta device-serial">Serial: ${escHtml(d.serial)}</div>` : ""}
          ${d.tenant ? `<div class="device-meta">Tenant: ${escHtml(d.tenant)}</div>` : ""}
        </li>`;
      })
      .join("");

    html += `<div class="popup-section">
      <div class="popup-section-title">Network Equipment (${detail.devices.length})</div>
      ${filterBtns ? `<div class="device-filters" id="device-filters-${escHtml(locId)}">${filterBtns}</div>` : ""}
      <ul class="device-list device-list-scroll" id="device-list-${escHtml(locId)}" tabindex="0" aria-label="Device list">${items}</ul>
    </div>`;
  }

  if (!html) {
    html = `<div class="popup-section" style="color:var(--color-text-muted);font-size:.8rem">No equipment or ASN data found.</div>`;
  }

  container.className = "";
  container.innerHTML = html;

  // Wire up status filter buttons using event delegation
  const filtersEl = document.getElementById(`device-filters-${locId}`);
  if (filtersEl) {
    filtersEl.addEventListener("click", (e) => {
      const btn = e.target.closest(".device-filter-btn");
      if (!btn) return;
      filtersEl.querySelectorAll(".device-filter-btn").forEach((b) => {
        b.classList.remove("active");
        b.setAttribute("aria-pressed", "false");
      });
      btn.classList.add("active");
      btn.setAttribute("aria-pressed", "true");
      const filterVal = btn.dataset.filter;
      const list = document.getElementById(`device-list-${locId}`);
      if (list) {
        list.querySelectorAll("li").forEach((li) => {
          li.style.display =
            filterVal === "All" || li.dataset.deviceStatus === filterVal ? "" : "none";
        });
      }
    });
  }
}

// ---------------------------------------------------------------------------
// Fetch locations and render markers
// ---------------------------------------------------------------------------
const markerLayer = L.layerGroup().addTo(map);
let allLocations = [];
const CLUSTER_THRESHOLD = 100; // Use clustering if more than 100 locations

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

  // Add search point marker if provided
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

    // Draw 5 km radius circle
    L.circle([lat, lon], {
      radius: 5000,
      color: "#e74c3c",
      fillColor: "#e74c3c",
      fillOpacity: 0.05,
      weight: 2,
      dashArray: "6 4",
    }).addTo(markerLayer);
  }

  // Use clustering for large datasets
  if (locations.length > CLUSTER_THRESHOLD && !searchMarker) {
    renderMarkersWithClustering(locations);
  } else {
    renderMarkersSimple(locations);
  }
}

function renderMarkersSimple(locations) {
  for (const loc of locations) {
    addMarker(loc);
  }
}

function renderMarkersWithClustering(locations) {
  // Simple grid-based clustering
  // Group markers by zoom level grid cells
  const currentZoom = map.getZoom();
  const gridSize = currentZoom < 5 ? 2 : currentZoom < 8 ? 1 : 0.5;

  const clusters = {};

  for (const loc of locations) {
    const gridLat = Math.floor(loc.latitude / gridSize) * gridSize;
    const gridLon = Math.floor(loc.longitude / gridSize) * gridSize;
    const key = `${gridLat},${gridLon}`;

    if (!clusters[key]) {
      clusters[key] = [];
    }
    clusters[key].push(loc);
  }

  // Render clusters or individual markers
  for (const key in clusters) {
    const group = clusters[key];

    if (group.length === 1) {
      // Single marker
      addMarker(group[0]);
    } else if (currentZoom < 8) {
      // Create cluster marker
      const lat = group.reduce((sum, l) => sum + l.latitude, 0) / group.length;
      const lon = group.reduce((sum, l) => sum + l.longitude, 0) / group.length;

      const clusterIcon = L.divIcon({
        html: `<div style="width:40px;height:40px;background:#3388ff;color:white;border:3px solid #fff;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:bold;box-shadow:0 0 8px rgba(0,0,0,.4)">${group.length}</div>`,
        iconSize: [40, 40],
        iconAnchor: [20, 20],
        className: "",
      });

      const clusterMarker = L.marker([lat, lon], { icon: clusterIcon });
      clusterMarker.on('click', () => {
        // Zoom in to show individual markers
        map.setView([lat, lon], Math.min(currentZoom + 3, 15));
      });

      const locations = group.map(l => `<li>${escHtml(l.name)}</li>`).slice(0, 10).join('');
      const more = group.length > 10 ? `<li style="color:#888">… and ${group.length - 10} more</li>` : '';
      clusterMarker.bindPopup(
        `<div class="popup-content">
          <div class="popup-title">${group.length} locations</div>
          <div style="font-size:0.85rem;margin-top:8px">Click to zoom in</div>
          <ul style="margin:8px 0 0 0;padding-left:20px;font-size:0.8rem;max-height:150px;overflow-y:auto">
            ${locations}${more}
          </ul>
        </div>`
      );
      clusterMarker.addTo(markerLayer);
    } else {
      // Zoom level high enough, show individual markers
      for (const loc of group) {
        addMarker(loc);
      }
    }
  }
}

function addMarker(loc) {
  const marker = L.marker([loc.latitude, loc.longitude], {
    icon: iconForStatus(loc.status),
    title: loc.name,
  });

  marker.bindPopup(() => buildBasicPopup(loc), { maxWidth: 360, minWidth: 280 });

  marker.on("popupopen", () => {
    fetchAndRenderDetail(loc.id);
  });

  marker.addTo(markerLayer);
}

// Re-cluster on zoom
map.on('zoomend', () => {
  if (allLocations.length > CLUSTER_THRESHOLD) {
    applyFilters();
  }
});

async function fetchAndRenderDetail(locId) {
  try {
    const resp = await fetch(`/api/locations/${encodeURIComponent(locId)}/detail`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const detail = await resp.json();
    renderDetail(locId, detail);
  } catch (err) {
    const container = document.getElementById(`popup-detail-${locId}`);
    if (container) {
      container.textContent = "Could not load details: " + err.message;
    }
  }
}

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------
const filterStatus = document.getElementById("filter-status");
const filterType = document.getElementById("filter-type");
const filterParent = document.getElementById("filter-parent");
const filterTenant = document.getElementById("filter-tenant");

function populateFilters(locations) {
  const statuses = [...new Set(locations.map((l) => l.status).filter(Boolean))].sort();
  const types = [...new Set(locations.map((l) => l.location_type).filter(Boolean))].sort();
  const parents = [...new Set(locations.map((l) => l.parent).filter(Boolean))].sort();
  const tenants = [...new Set(locations.map((l) => l.tenant).filter(Boolean))].sort();

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
}

function applyFilters() {
  const statusVal = filterStatus.value;
  const typeVal = filterType.value;
  const parentVal = filterParent.value;
  const tenantVal = filterTenant.value;

  const filtered = allLocations.filter((loc) => {
    if (statusVal && loc.status !== statusVal) return false;
    if (typeVal && loc.location_type !== typeVal) return false;
    if (parentVal && loc.parent !== parentVal) return false;
    if (tenantVal && loc.tenant !== tenantVal) return false;
    return true;
  });

  renderMarkers(filtered);
  updateLocationCount(filtered.length);
}

filterStatus.addEventListener("change", applyFilters);
filterType.addEventListener("change", applyFilters);
filterParent.addEventListener("change", applyFilters);
filterTenant.addEventListener("change", applyFilters);

// Clear filters button
document.getElementById("clear-filters").addEventListener("click", () => {
  filterStatus.value = "";
  filterType.value = "";
  filterParent.value = "";
  filterTenant.value = "";
  applyFilters();
});

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
          map.flyTo([loc.latitude, loc.longitude], 14);
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
