// The map's first view (#362): MAP_DEFAULT_VIEW when set, else every site
// with coordinates, rather than the whole world.

// No closer than a city when there is one site, or a few close together.
const FIT_MAX_ZOOM = 10;

// Fit *map* to *locations*; nothing when none has coordinates.  0,0 is
// left out: in Nautobot it is a site whose coordinates were never set, and it
// would pull the view out to the Atlantic (the site is still on the map).
export function fitToSites(map, locations) {
  const points = locations
    .filter((loc) => loc.latitude != null && loc.longitude != null && (loc.latitude !== 0 || loc.longitude !== 0))
    .map((loc) => [loc.latitude, loc.longitude]);
  if (!points.length) return;
  map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: FIT_MAX_ZOOM, animate: false });
}

// Whether a link (?location_id=) opens a site the map can fly to.
export function linkedSiteOnMap(locations, locId) {
  return Boolean(locId) && locations.some((loc) => loc.id === locId && loc.latitude != null && loc.longitude != null);
}
