// The markers on the map (#334).  Sites that need attention (an alarm or
// maintenance) are always their own pin, on top: never hidden in a cluster.
// The rest (OK, No data) go into Leaflet.markercluster's group, which draws
// only what is in view and stays quiet (green or grey) at any count.
import { iconForLocation, makeClusterIcon, makeStackedIcon, quietDotIcon } from "./icons.js";
import { displayLevel, worstAlertLevel } from "./levels.js";
import { colocGroupByLocId, getLocationById, locationAlerts, markerByLocId, state } from "./state.js";

// Levels that are never clustered.
const ATTENTION_LEVELS = new Set(["critical", "medium", "low", "maintenance"]);
// Above quiet clusters and each other: worst on top.
const ATTENTION_Z = { critical: 4000, medium: 3000, low: 2000, maintenance: 1000 };

// What a click (or Enter) on a pin or stack does: main.js sets it to open
// the site panel.  (marker, locId, groupIds, focusElement)
let activateMarker = () => {};
// Redraw every marker: main.js sets it (filters.applyFilters).
let redrawAll = () => {};
// The sites drawn as their own pins at the last draw, and whether that draw
// was search results (all pins, no clusters: nothing crosses a line there).
let drawnAttention = new Set();
let drawnSearch = false;

export function onMarkerActivate(handler) {
  activateMarker = handler;
}

export function onRedrawNeeded(handler) {
  redrawAll = handler;
}

export function needsAttention(level) {
  return ATTENTION_LEVELS.has(level);
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

// Coordinate groups split into those drawn as pins on top (any site in the
// group needs attention) and the quiet ones that may be clustered.
export function splitGroups(groups, levelOf = displayLevel) {
  const attention = [];
  const quiet = [];
  for (const group of Object.values(groups)) {
    (group.some((loc) => needsAttention(levelOf(loc.id))) ? attention : quiet).push(group);
  }
  return { attention, quiet };
}

// A quiet cluster: green when any site in it is OK, else grey (No data).
// *idLists*: the site ids of each marker in the cluster.
export function quietCluster(idLists, levelOf = displayLevel) {
  const ids = idLists.flat();
  return { count: ids.length, level: ids.some((id) => levelOf(id) === "ok") ? "ok" : "no_data" };
}

// For L.markerClusterGroup's iconCreateFunction: counts sites, not markers
// (a co-located stack is one marker).
export function quietClusterIcon(cluster) {
  const { count, level } = quietCluster(cluster.getAllChildMarkers().map((marker) => marker.options.locIds || []));
  return makeClusterIcon(count, level);
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

// A coordinate group's icon: a pin (or stack) for a site that needs attention
// or a search result; a small dot for a quiet one.
function iconForGroup(ids, asPin) {
  const level = ids.length === 1 ? displayLevel(ids[0]) : worstAlertLevel(ids);
  if (!asPin) return quietDotIcon(level, ids.length);
  return ids.length === 1 ? iconForLocation({ id: ids[0] }) : makeStackedIcon(ids.length, level);
}

// One marker for a coordinate group: a pin, or a stack for several sites.
// *onTop*: a site that needs attention, above quiet markers and clusters;
// *asPin*: drawn as a pin (also every search result).
function markerForGroup(group, onTop, asPin = onTop) {
  const first = group[0];
  const ids = group.map((loc) => loc.id);
  const level = group.length === 1 ? displayLevel(first.id) : worstAlertLevel(ids);
  const marker = L.marker([first.latitude, first.longitude], {
    icon: iconForGroup(ids, asPin),
    title: group.map((loc) => loc.name).join(", "),
    zIndexOffset: onTop ? ATTENTION_Z[level] || 0 : 0,
    locIds: ids,
  });
  const label = group.length === 1 ? `Inspect ${first.name}` : `Inspect ${group.length} co-located locations`;

  marker.on("click", (event) => {
    stopMapClick(event);
    activateMarker(marker, first.id, ids, marker.getElement());
  });
  wireMarkerAccessibility(marker, label, (focusElement) => activateMarker(marker, first.id, ids, focusElement));

  for (const loc of group) {
    markerByLocId[loc.id] = marker;
    if (group.length > 1) colocGroupByLocId[loc.id] = ids;
  }
  return marker;
}

function clearMarkers() {
  state.markerLayer.clearLayers();
  state.clusterLayer?.clearLayers();
  for (const key of Object.keys(markerByLocId)) delete markerByLocId[key];
  for (const key of Object.keys(colocGroupByLocId)) delete colocGroupByLocId[key];
}

function drawSearchPoint({ lat, lon }) {
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

// Draw *locations*; with *searchMarker* ({lat, lon}) also the searched point
// and its 5 km circle, and every site as its own pin (a search finds few).
export function renderMarkers(locations, searchMarker) {
  clearMarkers();
  const groups = groupByCoords(locations);
  if (searchMarker) {
    drawSearchPoint(searchMarker);
    for (const group of Object.values(groups)) markerForGroup(group, false, true).addTo(state.markerLayer);
    drawnAttention = new Set(locations.map((loc) => loc.id));
    drawnSearch = true;
    return;
  }
  drawnSearch = false;
  const { attention, quiet } = splitGroups(groups);
  drawnAttention = new Set();
  for (const group of attention) {
    markerForGroup(group, true).addTo(state.markerLayer);
    for (const loc of group) drawnAttention.add(loc.id);
  }
  // In one call: the cluster group sorts them once.
  state.clusterLayer.addLayers(quiet.map((group) => markerForGroup(group, false)));
}

// Whether *locId*'s level change moves its marker between the pins on top
// and the clusters, which takes a full redraw.
export function crossesClusterLine(locId) {
  const marker = markerByLocId[locId];
  // Search results are all pins: a redraw would throw the search away (review on #336).
  if (!marker || drawnSearch) return false;
  const ids = marker.options.locIds || [locId];
  return ids.some((id) => drawnAttention.has(id)) !== ids.some((id) => needsAttention(displayLevel(id)));
}

// Draw every marker again, unless search results are shown: a redraw would
// throw the search away (reviews on #336, #337).
export function redrawMarkers() {
  if (!drawnSearch) redrawAll();
}

// Redraw the marker (or co-located stack) holding *locId* after its level changed.
export function refreshMarkerIcon(locId) {
  const marker = markerByLocId[locId];
  if (!marker) return;
  if (crossesClusterLine(locId)) {
    redrawAll();
    return;
  }
  const ids = marker.options.locIds || [locId];
  const asPin = ids.some((id) => drawnAttention.has(id));
  marker.setIcon(iconForGroup(ids, asPin));
  // Worst on top, also after a pin's level changed (critical -> low; review on #336).
  if (asPin && !drawnSearch) {
    const level = ids.length === 1 ? displayLevel(ids[0]) : worstAlertLevel(ids);
    marker.setZIndexOffset(ATTENTION_Z[level] || 0);
  }
  // A quiet site turning from No data to OK (or back) recolours its cluster.
  if (state.clusterLayer?.hasLayer(marker)) state.clusterLayer.refreshClusters(marker);
}

// A level the site panel loaded; it colours the site's marker too.
export function updateMarkerForAlert(locId, alertLevel) {
  // The site panel scores live; a site in maintenance stays blue (#283).
  if (locationAlerts[locId] === "maintenance") return;
  locationAlerts[locId] = alertLevel;
  refreshMarkerIcon(locId);
}
