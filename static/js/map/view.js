// The map's first view (#362): MAP_DEFAULT_VIEW when set, else every site
// with coordinates, rather than the whole world.

// No closer than a city when there is one site, or a few close together.
const FIT_MAX_ZOOM = 10;

// "lat,lon,zoom" (checked by the server), or null.
export function parseView(text) {
  const [lat, lon, zoom] = (text || "").split(",").map(Number);
  return text && [lat, lon, zoom].every(Number.isFinite) ? { center: [lat, lon], zoom } : null;
}

// Fit *map* to *locations*; nothing when none has coordinates.
export function fitToSites(map, locations) {
  const points = locations
    .filter((loc) => loc.latitude != null && loc.longitude != null)
    .map((loc) => [loc.latitude, loc.longitude]);
  if (!points.length) return;
  map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: FIT_MAX_ZOOM, animate: false });
}
