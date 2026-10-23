// The map's first view (#362): MAP_DEFAULT_VIEW when set, else every site
// with coordinates, rather than the whole world.

// No closer than a city when there is one site, or a few close together.
const FIT_MAX_ZOOM = 10;

// A site this many times further out than nine sites in ten is a lone one far
// from the rest (#459), never one within this many km of the middle.
const FAR_FACTOR = 3;
const NEAR_KM = 500;
const EARTH_KM = 6378;
// Web Mercator ends here: the map draws nothing further north or south.
const MAX_LAT = 85.05;

// A point as the map draws it (Web Mercator, radians): the far north is
// stretched, so a site at 82° N is much further from Europe there than in km.
function project([lat, lon]) {
  const phi = (Math.max(-MAX_LAT, Math.min(MAX_LAT, lat)) * Math.PI) / 180;
  return [(lon * Math.PI) / 180, Math.log(Math.tan(Math.PI / 4 + phi / 2))];
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// The *points* the first view frames (#459): all of them, except a lone site
// or a few (under one in ten) far from the rest on the map as drawn, e.g. one
// in northern Greenland beside a network in Europe.  A real group there is
// framed; the left-out sites stay on the map.
export function mainGroup(points) {
  if (points.length < 3) return points;
  const projected = points.map(project);
  const middle = [median(projected.map((p) => p[0])), median(projected.map((p) => p[1]))];
  const distances = projected.map(([x, y]) => Math.hypot(x - middle[0], y - middle[1]));
  const sorted = [...distances].sort((a, b) => a - b);
  const ninety = sorted[Math.floor(0.9 * (sorted.length - 1))];
  // NEAR_KM on the ground at the middle's latitude, in the map's units.
  const middleLat = Math.atan(Math.sinh(middle[1]));
  const near = NEAR_KM / (EARTH_KM * Math.cos(middleLat));
  const limit = Math.max(FAR_FACTOR * ninety, near);
  return points.filter((_, i) => distances[i] <= limit);
}

// Fit *map* to *locations*; nothing when none has coordinates.  0,0 is
// left out: in Nautobot it is a site whose coordinates were never set, and it
// would pull the view out to the Atlantic (the site is still on the map).
export function fitToSites(map, locations) {
  const points = locations
    .filter((loc) => loc.latitude != null && loc.longitude != null && (loc.latitude !== 0 || loc.longitude !== 0))
    .map((loc) => [loc.latitude, loc.longitude]);
  if (!points.length) return;
  map.fitBounds(L.latLngBounds(mainGroup(points)), { padding: [40, 40], maxZoom: FIT_MAX_ZOOM, animate: false });
}
