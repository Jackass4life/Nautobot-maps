// What the map's modules share (#294).  main.js fills `els`, `config` and
// `state.map`; nothing here touches the page or Leaflet when imported.

// The page's elements, by the names the modules use (filled by main.js).
export const els = {};

// Settings from the server (templates/index.html, #197).
export const config = { nautobotUrl: "" };

// Above this many locations, zoomed out, nearby markers become clusters.
export const CLUSTER_THRESHOLD = 100;

export const state = {
  // The Leaflet map and the layer that holds every marker.
  map: null,
  markerLayer: null,
  allLocations: [],
  // ?location_id= on the page's URL, opened once its marker exists.
  initialLocationId: null,
  initialLocationOpened: false,
  // A location to open once the map has zoomed to it.
  pendingLocationOpen: null,
  // The site panel.
  selectedLocationId: null,
  selectedGroupIds: [],
  selectedDetail: null,
  inspectorError: "",
  inspectorLoading: false,
  inspectorDeviceFilter: "all",
  inspectorDeviceSearch: "",
  lastInspectorTrigger: null,
};

// Location id -> its alert level, from /api/location-alerts (#234).
export const locationAlerts = {};
// Location id -> the marker, co-located stack or cluster that shows it.
export const markerByLocId = {};
export const clusterByLocId = {};
// Location id -> the ids of every location in its co-located stack.
export const colocGroupByLocId = {};
// Site details (/api/locations/<id>/detail), and the requests under way.
export const detailCache = new Map();
export const inFlightDetailRequests = new Map();

export function getLocationById(locId) {
  return state.allLocations.find((loc) => loc.id === locId) || null;
}
