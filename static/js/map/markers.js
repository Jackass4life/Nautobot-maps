// The markers on the map: one pin per site, a stack for sites at the same
// coordinates, and, zoomed out with many sites, clusters.
import { escHtml } from "../lib/html.js";
import { iconForLocation, makeClusterIcon, makeStackedIcon } from "./icons.js";
import { worstAlertLevel } from "./levels.js";
import {
  CLUSTER_THRESHOLD,
  clusterByLocId,
  colocGroupByLocId,
  getLocationById,
  locationAlerts,
  markerByLocId,
  state,
} from "./state.js";

// What a click (or Enter) on a pin or stack does: main.js sets it to open
// the site panel.  (marker, locId, groupIds, focusElement)
let activateMarker = () => {};

export function onMarkerActivate(handler) {
  activateMarker = handler;
}

// Sites at the same coordinates (to 4 decimals, ~10 m), by "lat,lon".
export function groupByCoords(locations) {
  const groups = {};
  for (const loc of locations) {
    if (loc.latitude == null || loc.longitude == null) continue;
    const key = `${loc.latitude.toFixed(4)},${loc.longitude.toFixed(4)}`;
    if (!groups[key]) groups[key] = [];
    groups[key].push(loc);
  }
  return groups;
}

// The ids of every site at *locId*'s coordinates, itself included.
export function getColocatedLocationIds(locId) {
  const loc = getLocationById(locId);
  if (!loc || loc.latitude == null || loc.longitude == null) return [locId];
  const key = `${loc.latitude.toFixed(4)},${loc.longitude.toFixed(4)}`;
  return state.allLocations
    .filter(
      (candidate) =>
        candidate.latitude != null
        && candidate.longitude != null
        && `${candidate.latitude.toFixed(4)},${candidate.longitude.toFixed(4)}` === key
    )
    .map((candidate) => candidate.id);
}

// A marker is a button for the keyboard too: Tab to it, Enter or Space opens it.
export function wireMarkerAccessibility(marker, label, activate) {
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

function stopMapClick(event) {
  if (event && event.originalEvent) {
    event.originalEvent.preventDefault();
    event.originalEvent.stopPropagation();
  }
}

function addMarker(loc) {
  const marker = L.marker([loc.latitude, loc.longitude], {
    icon: iconForLocation(loc),
    title: loc.name,
  });

  marker.on("click", (event) => {
    stopMapClick(event);
    activateMarker(marker, loc.id, [loc.id], marker.getElement());
  });

  wireMarkerAccessibility(
    marker,
    `Inspect ${loc.name}`,
    (focusElement) => activateMarker(marker, loc.id, [loc.id], focusElement)
  );

  markerByLocId[loc.id] = marker;
  marker.addTo(state.markerLayer);
}

function addColocatedMarker(locations) {
  const first = locations[0];
  const ids = locations.map((loc) => loc.id);
  const marker = L.marker([first.latitude, first.longitude], {
    icon: makeStackedIcon(locations.length, worstAlertLevel(ids)),
    title: locations.map((loc) => loc.name).join(", "),
  });

  marker.on("click", (event) => {
    stopMapClick(event);
    activateMarker(marker, first.id, ids, marker.getElement());
  });

  wireMarkerAccessibility(
    marker,
    `Inspect ${locations.length} co-located locations`,
    (focusElement) => activateMarker(marker, first.id, ids, focusElement)
  );

  for (const loc of locations) {
    markerByLocId[loc.id] = marker;
    colocGroupByLocId[loc.id] = ids;
  }

  marker.addTo(state.markerLayer);
}

function addMarkersForGroups(groups) {
  for (const key in groups) {
    const group = groups[key];
    if (group.length === 1) addMarker(group[0]);
    else addColocatedMarker(group);
  }
}

function addCluster(group, currentZoom) {
  const { map } = state;
  const lat = group.reduce((sum, loc) => sum + loc.latitude, 0) / group.length;
  const lon = group.reduce((sum, loc) => sum + loc.longitude, 0) / group.length;
  const locIds = group.map((loc) => loc.id);
  const clusterMarker = L.marker([lat, lon], {
    icon: makeClusterIcon(group.length, worstAlertLevel(locIds)),
  });
  clusterMarker.locIds = locIds;
  clusterMarker.on("click", () => {
    map.setView([lat, lon], Math.min(currentZoom + 3, 15));
  });
  wireMarkerAccessibility(
    clusterMarker,
    `Zoom to ${group.length} clustered locations`,
    () => {
      map.setView([lat, lon], Math.min(currentZoom + 3, 15));
    }
  );

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
  clusterMarker.addTo(state.markerLayer);
}

function renderMarkersWithClustering(locations) {
  const currentZoom = state.map.getZoom();
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
    if (group.length === 1) addMarker(group[0]);
    else if (currentZoom < 8) addCluster(group, currentZoom);
    else addMarkersForGroups(groupByCoords(group));
  }
}

// Draw *locations*; with *searchMarker* ({lat, lon}) also the searched point
// and its 5 km circle, and never clusters.
export function renderMarkers(locations, searchMarker) {
  state.markerLayer.clearLayers();
  for (const key of Object.keys(markerByLocId)) delete markerByLocId[key];
  for (const key of Object.keys(clusterByLocId)) delete clusterByLocId[key];
  for (const key of Object.keys(colocGroupByLocId)) delete colocGroupByLocId[key];

  if (searchMarker) {
    const { lat, lon } = searchMarker;
    // A ring, not a colour (#311): no pin colour means "searched here".
    const searchIcon = L.divIcon({
      html: '<div class="search-point-marker"></div>',
      iconSize: [22, 22],
      iconAnchor: [11, 11],
      className: "",
    });
    L.marker([lat, lon], { icon: searchIcon })
      .bindPopup(`<div class="popup-content"><div class="popup-title" aria-label="Search location">&#128269; Search point</div></div>`)
      .addTo(state.markerLayer);

    L.circle([lat, lon], {
      radius: 5000,
      color: "#334155",
      fillColor: "#334155",
      fillOpacity: 0.05,
      weight: 2,
      dashArray: "6 4",
    }).addTo(state.markerLayer);
  }

  if (locations.length > CLUSTER_THRESHOLD && !searchMarker) {
    renderMarkersWithClustering(locations);
  } else {
    addMarkersForGroups(groupByCoords(locations));
  }
}

// Redraw the marker (or co-located stack, or cluster) holding *locId*.
export function refreshMarkerIcon(locId) {
  const marker = markerByLocId[locId];
  if (marker) {
    const groupIds = colocGroupByLocId[locId];
    if (groupIds) {
      marker.setIcon(makeStackedIcon(groupIds.length, worstAlertLevel(groupIds)));
    } else {
      const loc = getLocationById(locId);
      if (loc) marker.setIcon(iconForLocation(loc));
    }
    return;
  }
  const cluster = clusterByLocId[locId];
  if (cluster && cluster.locIds) {
    cluster.setIcon(makeClusterIcon(cluster.locIds.length, worstAlertLevel(cluster.locIds)));
  }
}

// A level the site panel loaded; it colours the site's marker too.
export function updateMarkerForAlert(locId, alertLevel) {
  // The site panel scores live; a site in maintenance stays blue (#283).
  if (locationAlerts[locId] === "maintenance") return;
  locationAlerts[locId] = alertLevel;
  refreshMarkerIcon(locId);
}
