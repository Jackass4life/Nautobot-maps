// The map's markers (#334; on Nuxt since #463).  Sites that need attention
// (an alarm or maintenance) are always their own pin, on top, never hidden in
// a cluster.  The rest (OK, No data) go into Leaflet.markercluster's group,
// which draws only what is in view and stays quiet at any count.
//
// No style attributes in the markers' HTML: the CSP allows no inline styles
// (#370).  Leaflet sizes each marker's box from iconSize (from script, which
// the CSP allows); colours are the level-bg-* classes in main.css.
import type * as Leaflet from "leaflet";
import {
  LEVEL_COLORS,
  groupByCoords,
  knownLevel,
  needsAttention,
  quietCluster,
  splitGroups,
  viewPoints,
  worstLevel,
  type MapLocation,
} from "./map";

type LeafletModule = typeof Leaflet;
let L: LeafletModule;

// Leaflet, then markercluster, which extends the global L.
export async function loadLeaflet(): Promise<LeafletModule> {
  if (L) return L;
  const module = await import("leaflet");
  L = (module.default ?? module) as LeafletModule;
  (window as unknown as { L: LeafletModule }).L = L;
  await import("leaflet.markercluster");
  return L;
}

// From this zoom nothing is clustered, so a site is its own marker.
export const UNCLUSTERED_ZOOM = 15;
// No closer than a city when there is one site, or a few close together.
const FIT_MAX_ZOOM = 10;
// A place search's circle (#392).
export const SEARCH_RADIUS_M = 5000;
// Above quiet clusters and each other: worst on top.
const ATTENTION_Z: Record<string, number> = { critical: 4000, medium: 3000, low: 2000, maintenance: 1000 };

const PIN_PATH = "M12 0C5.373 0 0 5.373 0 12c0 9 12 24 12 24S24 21 24 12C24 5.373 18.627 0 12 0z";

function pinSvg(color: string): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 36"><path d="${PIN_PATH}" fill="${color}" stroke="#fff" stroke-width="1.5"/><circle cx="12" cy="12" r="4.5" fill="#fff"/></svg>`;
}

const pinIcons = new Map<string, Leaflet.DivIcon>();
function pinIcon(level: string): Leaflet.DivIcon {
  const known = knownLevel(level);
  let icon = pinIcons.get(known);
  if (!icon) {
    const pulse = known === "critical" ? " marker-pulse" : "";
    icon = L.divIcon({
      html: `<div class="pin-icon${pulse}">${pinSvg(LEVEL_COLORS[known]!)}</div>`,
      iconSize: [24, 36],
      iconAnchor: [12, 36],
      className: "",
    });
    pinIcons.set(known, icon);
  }
  return icon;
}

// Several sites at the same coordinates: one pin with a count.
function stackedIcon(count: number, level: string): Leaflet.DivIcon {
  const known = knownLevel(level);
  const pulse = known === "critical" ? " marker-pulse" : "";
  return L.divIcon({
    html: `<div class="pin-icon pin-stacked${pulse}">${pinSvg(LEVEL_COLORS[known]!)}<span class="colocated-badge">${count}</span></div>`,
    iconSize: [24, 36],
    iconAnchor: [12, 36],
    className: "",
  });
}

// A quiet site on its own: a dot, not a pin; with a count for several.
function quietDotIcon(level: string, count = 1): Leaflet.DivIcon {
  const size = count > 1 ? 18 : 12;
  return L.divIcon({
    html: `<div class="quiet-dot level-bg-${knownLevel(level)}">${count > 1 ? count : ""}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    className: "",
  });
}

// A cluster of quiet sites: green with an OK site in it, else grey; a little
// bigger for more sites.  The label says what is in it.
function clusterIcon(count: number, level: string): Leaflet.DivIcon {
  const size = count < 10 ? 24 : count < 100 ? 30 : 36;
  const label = `${count} location${count === 1 ? "" : "s"} without an alarm, zoom in`;
  return L.divIcon({
    html: `<div class="quiet-cluster level-bg-${knownLevel(level)}" role="img" aria-label="${label}">${count}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    className: "",
  });
}

interface SiteMarkerOptions extends Leaflet.MarkerOptions {
  locIds: string[];
}

export interface SiteMapOptions {
  tileUrl: string;
  attribution: string;
  view: { latitude: number; longitude: number; zoom: number } | null;
  // A location's level for its colour.
  levelOf: (id: string) => string;
  // A pin or stack clicked, or Enter on it: the site, the sites there, the marker.
  onActivate: (locId: string, ids: string[], element: HTMLElement | null) => void;
}

export class SiteMap {
  readonly map: Leaflet.Map;
  private readonly markerLayer: Leaflet.LayerGroup;
  private readonly clusterLayer: Leaflet.MarkerClusterGroup;
  private readonly levelOf: (id: string) => string;
  private readonly onActivate: SiteMapOptions["onActivate"];
  // Location id -> the marker that shows it, and the ids at its coordinates.
  private readonly markers = new Map<string, Leaflet.Marker>();
  // The sites drawn as their own pins at the last draw, and whether that draw
  // was search results (all pins, no clusters: nothing crosses a line there).
  private drawnAttention = new Set<string>();
  private drawnSearch = false;

  constructor(element: HTMLElement, options: SiteMapOptions) {
    this.levelOf = options.levelOf;
    this.onActivate = options.onActivate;
    // The whole world until the sites are in, unless a fixed view is set (#362).
    const view = options.view;
    this.map = L.map(element, {
      center: view ? [view.latitude, view.longitude] : [20, 0],
      zoom: view ? view.zoom : 3,
      preferCanvas: true,
    });
    L.tileLayer(options.tileUrl, { attribution: options.attribution, maxZoom: 19 }).addTo(this.map);
    this.clusterLayer = L.markerClusterGroup({
      iconCreateFunction: (cluster) => {
        const ids = cluster.getAllChildMarkers().flatMap((marker) => (marker.options as SiteMarkerOptions).locIds || []);
        const { count, level } = quietCluster(ids, this.levelOf);
        return clusterIcon(count, level);
      },
      showCoverageOnHover: false,
      maxClusterRadius: 60,
      disableClusteringAtZoom: UNCLUSTERED_ZOOM,
    }).addTo(this.map);
    // Added after the clusters, so the pins are drawn on top.
    this.markerLayer = L.layerGroup().addTo(this.map);
  }

  // Fit to every site with coordinates, except a lone far-off one (#362, #459).
  fit(locations: MapLocation[]): void {
    const points = viewPoints(locations);
    if (points.length) this.map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: FIT_MAX_ZOOM, animate: false });
  }

  // The map's box changed: measure again once the layout has settled (two
  // frames).  *keepCentre*: the same middle (the left panel folding, #420);
  // else the sites stay where they were (the site panel opening beside them).
  resize(keepCentre = false): void {
    requestAnimationFrame(() => requestAnimationFrame(() => this.map.invalidateSize({ pan: keepCentre, animate: false })));
  }

  private groupLevel(ids: string[]): string {
    return ids.length === 1 ? this.levelOf(ids[0]!) : worstLevel(ids.map(this.levelOf));
  }

  private iconFor(ids: string[], asPin: boolean): Leaflet.DivIcon {
    const level = this.groupLevel(ids);
    if (!asPin) return quietDotIcon(level, ids.length);
    return ids.length === 1 ? pinIcon(level) : stackedIcon(ids.length, level);
  }

  // One marker for a coordinate group.  *onTop*: above quiet markers and
  // clusters, worst on top; *asPin*: a pin (also every search result).
  private markerFor(group: MapLocation[], onTop: boolean, asPin = onTop): Leaflet.Marker {
    const first = group[0]!;
    const ids = group.map((loc) => loc.id);
    const marker = L.marker([first.latitude, first.longitude], {
      icon: this.iconFor(ids, asPin),
      title: group.map((loc) => loc.name).join(", "),
      zIndexOffset: onTop ? ATTENTION_Z[this.groupLevel(ids)] || 0 : 0,
      locIds: ids,
    } as SiteMarkerOptions);
    marker.on("click", (event) => {
      event.originalEvent?.preventDefault();
      event.originalEvent?.stopPropagation();
      this.onActivate(first.id, ids, marker.getElement() ?? null);
    });
    // A button for the keyboard too: Tab to it, Enter or Space opens it.
    const label = group.length === 1 ? `Inspect ${first.name}` : `Inspect ${group.length} co-located locations`;
    marker.on("add", () => {
      const element = marker.getElement();
      if (!element || element.dataset.wired) return;
      element.dataset.wired = "1";
      element.setAttribute("role", "button");
      element.setAttribute("tabindex", "0");
      element.setAttribute("aria-label", label);
      element.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        this.onActivate(first.id, ids, element);
      });
    });
    for (const loc of group) this.markers.set(loc.id, marker);
    return marker;
  }

  private clear(): void {
    this.markerLayer.clearLayers();
    this.clusterLayer.clearLayers();
    this.markers.clear();
  }

  // Draw *locations*: pins for those that need attention, the rest clustered.
  render(locations: MapLocation[]): void {
    this.clear();
    this.drawnSearch = false;
    const { attention, quiet } = splitGroups(groupByCoords(locations), this.levelOf);
    this.drawnAttention = new Set();
    for (const group of attention) {
      this.markerFor(group, true).addTo(this.markerLayer);
      for (const loc of group) this.drawnAttention.add(loc.id);
    }
    // In one call: the cluster group sorts them once.
    this.clusterLayer.addLayers(quiet.map((group) => this.markerFor(group, false)));
  }

  // A place search's sites, every one a pin (a search finds few), the point
  // searched as a ring (no pin colour means "searched here", #311) and its circle.
  renderSearch(locations: MapLocation[], point: { lat: number; lon: number }): void {
    this.clear();
    const icon = L.divIcon({ html: '<div class="search-point-marker"></div>', iconSize: [22, 22], iconAnchor: [11, 11], className: "" });
    L.marker([point.lat, point.lon], { icon, title: "Search point", keyboard: false }).addTo(this.markerLayer);
    L.circle([point.lat, point.lon], {
      radius: SEARCH_RADIUS_M,
      color: "#334155",
      fillColor: "#334155",
      fillOpacity: 0.05,
      weight: 2,
      dashArray: "6 4",
    }).addTo(this.markerLayer);
    for (const group of groupByCoords(locations)) this.markerFor(group, false, true).addTo(this.markerLayer);
    this.drawnAttention = new Set(locations.map((loc) => loc.id));
    this.drawnSearch = true;
    this.map.flyTo([point.lat, point.lon], 11, { duration: 1.2 });
  }

  get showingSearch(): boolean {
    return this.drawnSearch;
  }

  // Whether *locId*'s level change moves its marker between the pins on top
  // and the clusters, which takes a full redraw.
  crossesClusterLine(locId: string): boolean {
    const marker = this.markers.get(locId);
    // Search results are all pins (review on #336).
    if (!marker || this.drawnSearch) return false;
    const ids = (marker.options as SiteMarkerOptions).locIds;
    return ids.some((id) => this.drawnAttention.has(id)) !== ids.some((id) => needsAttention(this.levelOf(id)));
  }

  // The marker holding *locId* after its level changed (not across the line).
  refreshIcon(locId: string): void {
    const marker = this.markers.get(locId);
    if (!marker) return;
    const ids = (marker.options as SiteMarkerOptions).locIds;
    const asPin = ids.some((id) => this.drawnAttention.has(id));
    marker.setIcon(this.iconFor(ids, asPin));
    // Worst on top, also after a pin's level changed (review on #336).
    if (asPin && !this.drawnSearch) marker.setZIndexOffset(ATTENTION_Z[this.groupLevel(ids)] || 0);
    // A quiet site turning from No data to OK (or back) recolours its cluster.
    if (this.clusterLayer.hasLayer(marker)) this.clusterLayer.refreshClusters(marker);
  }

  has(locId: string): boolean {
    return this.markers.has(locId);
  }

  // The ids at *locId*'s marker (a co-located stack), else just it.
  idsAt(locId: string): string[] {
    const marker = this.markers.get(locId);
    return marker ? (marker.options as SiteMarkerOptions).locIds : [locId];
  }

  // Fly to *loc*: a quiet site in a cluster zooms in until it is its own
  // marker.  (Not markercluster's zoomToShowLayer: a redraw while it zooms
  // would leave it waiting on a removed marker.)  Its marker's element, for
  // focus when the panel closes; null when it isn't drawn.
  show(loc: MapLocation): HTMLElement | null | false {
    const marker = this.markers.get(loc.id);
    if (!marker) return false;
    const zoom = this.clusterLayer.hasLayer(marker) ? UNCLUSTERED_ZOOM : 13;
    this.map.flyTo([loc.latitude, loc.longitude], Math.max(this.map.getZoom(), zoom), { duration: 0.8 });
    return marker.getElement() ?? null;
  }

  panTo(locId: string): void {
    const marker = this.markers.get(locId);
    if (marker) this.map.panTo(marker.getLatLng());
  }
}

