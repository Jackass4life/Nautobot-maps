// The map (#334, #392; on Nuxt since #463): alert levels, the alarm list,
// the filters, the first view and the folding panel.  No Leaflet here: the
// markers are in leafletmap.ts.
import type { components } from "~/api/schema";
import { formatFeedTime } from "./format";
import { siteTenants } from "./scope";

export type MapLocation = components["schemas"]["Location"];
export type NearbySite = components["schemas"]["NearbyLocation"];
export type LevelInfo = components["schemas"]["LocationLevel"];
export type Level = LevelInfo["level"];

// The alert level only (#311).  Status (Active, Planned) is a filter, not a
// colour.  The markers' classes in main.css use the same colours.
export const LEVEL_COLORS: Record<string, string> = {
  critical: "#e74c3c",
  medium: "#ff8c00",
  low: "#eab308",
  // Only access points down (#521): the Low colour, softer; a quiet dot, not a pin.
  wireless: "#fde68a",
  maintenance: "#2563eb",
  ok: "#2ecc71",
  no_data: "#94a3b8",
};

// Each level's name, for the alarm list, the legend and the site panel.
export const LEVEL_NAMES: Record<string, string> = {
  critical: "Critical",
  medium: "Medium",
  low: "Low",
  ok: "OK",
  maintenance: "In maintenance",
  no_data: "No data",
  wireless: "Access points down",
};

// Alarms, least to most severe; "ok" and "no_data" are not alerts (#124).
export const ALERT_RANK: Record<string, number> = { low: 1, medium: 2, critical: 3 };

// What a stack or cluster shows: its worst member (#234, #311).
const DISPLAY_RANK: Record<string, number> = { no_data: 0, ok: 1, wireless: 2, maintenance: 3, low: 4, medium: 5, critical: 6 };

// Levels that are never clustered: always their own pin, on top (#334).
const ATTENTION = new Set(["critical", "medium", "low", "maintenance"]);

// A level for its colour: an unknown one (or Object.prototype's keys) is No data.
export function knownLevel(level: string | null | undefined): string {
  return level && Object.hasOwn(LEVEL_COLORS, level) ? level : "no_data";
}

// The level a marker shows (#521): "wireless" for a site with only access
// points down, so it is neither in the alarm list nor its own pin.
export function displayLevel(entry: { level: string; wireless_only?: boolean | null }): string {
  return entry.wireless_only ? "wireless" : entry.level;
}

export function isAlarm(level: string | null | undefined): boolean {
  return Boolean(level && Object.hasOwn(ALERT_RANK, level));
}

export function needsAttention(level: string): boolean {
  return ATTENTION.has(level);
}

// The worst of *levels*, for a stack or a cluster.
export function worstLevel(levels: string[]): string {
  return levels.map(knownLevel).reduce((worst, level) => (DISPLAY_RANK[level]! > DISPLAY_RANK[worst]! ? level : worst), "no_data");
}

// --- The alarm list (#334, #392) ------------------------------------------

export const ALARM_LIST_SHOWN = 8;
const ALARM_ORDER: Record<string, number> = { critical: 0, medium: 1, low: 2 };

export interface AlarmSite {
  loc: MapLocation;
  level: string;
  down: number;
  monitored: number;
  since?: string | null;
}

// The sites of *locations* in alarm, worst first, then newest, then by name.
// *levels*: the level each site shows, when newer than *info*'s (a site
// panel's fresher one).
export function alarmSites(
  locations: MapLocation[],
  info: Record<string, Partial<LevelInfo>>,
  levels: Record<string, string> = {},
): AlarmSite[] {
  const time = (entry: AlarmSite) => Date.parse(entry.since || "") || null;
  const levelOf = (id: string) => levels[id] ?? info[id]?.level;
  return locations
    .filter((loc) => isAlarm(levelOf(loc.id)))
    .map((loc) => ({ down: 0, monitored: Number.NaN, ...info[loc.id], level: levelOf(loc.id), loc }) as AlarmSite)
    .sort(
      (a, b) =>
        ALARM_ORDER[a.level]! - ALARM_ORDER[b.level]!
        || (time(b) ?? -Infinity) - (time(a) ?? -Infinity)
        || (a.loc.name || "").localeCompare(b.loc.name || ""),
    );
}

// "2/8 down · since 14:05".
export function alarmMeta(entry: AlarmSite, now: Date = new Date()): string {
  const since = entry.since ? formatFeedTime(entry.since, now) : "";
  return [Number.isFinite(entry.monitored) ? `${entry.down || 0}/${entry.monitored} down` : "", since ? `since ${since}` : ""]
    .filter(Boolean)
    .join(" · ");
}

// "8 critical · 9 medium · 11 low", the levels that have sites.
export function levelBreakdown(sites: AlarmSite[]): string {
  return Object.keys(ALARM_ORDER)
    .map((level) => [level, sites.filter((entry) => entry.level === level).length] as const)
    .filter(([, count]) => count)
    .map(([level, count]) => `${count} ${level}`)
    .join(" · ");
}

// The list's heading: "28 in alarm"; "All sites OK" only for every site (a
// filter or a link's scope may hide alarms).
export function alarmTitle(count: number, everySite: boolean, anySite: boolean): string {
  if (count) return `${count.toLocaleString("en")} in alarm`;
  return everySite && anySite ? "All sites OK" : "No site in alarm";
}

// --- Filters (#311, #392) -------------------------------------------------

// No location type or parent location (#480): folded away, since the site
// panel and the search show both.
export type FilterField = "status" | "tenant" | "tenant_group";

export const FILTERS: { field: FilterField; label: string; all: string }[] = [
  { field: "status", label: "Status", all: "All statuses" },
  { field: "tenant", label: "Tenant", all: "All tenants" },
  { field: "tenant_group", label: "Tenant group", all: "All tenant groups" },
];

export type Criteria = Record<FilterField, string[]>;

export function noCriteria(): Criteria {
  return { status: [], tenant: [], tenant_group: [] };
}

function valuesOf(loc: MapLocation, field: FilterField): string[] {
  return field === "tenant" ? siteTenants(loc) : [loc[field]].filter(Boolean);
}

// Each filter's choices: the values some site has, sorted.
export function filterChoices(locations: MapLocation[]): Criteria {
  const choices = noCriteria();
  for (const { field } of FILTERS) {
    choices[field] = [...new Set(locations.flatMap((loc) => valuesOf(loc, field)))].sort();
  }
  return choices;
}

// The locations matching every filter (each any of its values).
export function filterLocations(locations: MapLocation[], criteria: Criteria): MapLocation[] {
  const active = FILTERS.filter(({ field }) => criteria[field].length);
  if (!active.length) return locations;
  return locations.filter((loc) =>
    active.every(({ field }) => valuesOf(loc, field).some((value) => criteria[field].includes(value))),
  );
}

// "1,200 sites", "25 sites in alarm".
export function siteCount(count: number, alarmsOnly: boolean): string {
  return `${count.toLocaleString("en")} site${count === 1 ? "" : "s"}${alarmsOnly ? " in alarm" : ""}`;
}

// --- Markers (#334) ---------------------------------------------------------

// Sites at the same coordinates (to 4 decimals, ~10 m).
function coordKey(loc: Pick<MapLocation, "latitude" | "longitude">): string {
  return `${loc.latitude.toFixed(4)},${loc.longitude.toFixed(4)}`;
}

export function groupByCoords(locations: MapLocation[]): MapLocation[][] {
  const groups = new Map<string, MapLocation[]>();
  for (const loc of locations) {
    if (loc.latitude == null || loc.longitude == null) continue;
    const key = coordKey(loc);
    const group = groups.get(key);
    if (group) group.push(loc);
    else groups.set(key, [loc]);
  }
  return [...groups.values()];
}

// Groups drawn as pins on top (any site needs attention) and the quiet ones.
export function splitGroups(groups: MapLocation[][], levelOf: (id: string) => string) {
  const attention: MapLocation[][] = [];
  const quiet: MapLocation[][] = [];
  for (const group of groups) (group.some((loc) => needsAttention(levelOf(loc.id))) ? attention : quiet).push(group);
  return { attention, quiet };
}

// A quiet cluster: green when any site in it is OK, else grey (No data).
export function quietCluster(ids: string[], levelOf: (id: string) => string): { count: number; level: string } {
  return { count: ids.length, level: ids.some((id) => levelOf(id) === "ok") ? "ok" : "no_data" };
}

// --- The first view (#362, #459) ---------------------------------------------

// A site this many times further out than nine sites in ten is a lone one far
// from the rest (#459), never one within this many km of the middle.
const FAR_FACTOR = 3;
const NEAR_KM = 500;
const EARTH_KM = 6378;
// Web Mercator ends here: the map draws nothing further north or south.
const MAX_LAT = 85.05;

type Point = [number, number];

// A point as the map draws it (Web Mercator, radians): the far north is
// stretched, so a site at 82° N is much further from Europe there than in km.
function project([lat, lon]: Point): Point {
  const phi = (Math.max(-MAX_LAT, Math.min(MAX_LAT, lat)) * Math.PI) / 180;
  return [(lon * Math.PI) / 180, Math.log(Math.tan(Math.PI / 4 + phi / 2))];
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2;
}

// The *points* the first view frames: all of them, except a lone site or a
// few (under one in ten) far from the rest on the map as drawn, e.g. one in
// northern Greenland beside a network in Europe.  They stay on the map.
export function mainGroup(points: Point[]): Point[] {
  if (points.length < 3) return points;
  const projected = points.map(project);
  const middle = [median(projected.map((p) => p[0])), median(projected.map((p) => p[1]))] as Point;
  const distances = projected.map(([x, y]) => Math.hypot(x - middle[0], y - middle[1]));
  const sorted = [...distances].sort((a, b) => a - b);
  const ninety = sorted[Math.floor(0.9 * (sorted.length - 1))]!;
  // NEAR_KM on the ground at the middle's latitude, in the map's units.
  const near = NEAR_KM / (EARTH_KM * Math.cos(Math.atan(Math.sinh(middle[1]))));
  const limit = Math.max(FAR_FACTOR * ninety, near);
  return points.filter((_, i) => distances[i]! <= limit);
}

// The points the first view fits: 0,0 is left out, a site whose coordinates
// were never set in Nautobot (it would pull the view out to the Atlantic).
export function viewPoints(locations: MapLocation[]): Point[] {
  return mainGroup(
    locations
      .filter((loc) => loc.latitude != null && loc.longitude != null && (loc.latitude !== 0 || loc.longitude !== 0))
      .map((loc) => [loc.latitude, loc.longitude] as Point),
  );
}

// --- The folding panel (#420) -------------------------------------------------

// The folded panel's tab: "23 in alarm", else "Show the panel".  *count*:
// null until the alert levels are in.
export function panelToggleLabel(hidden: boolean, count: number | null): { text: string; label: string } {
  if (!hidden) return { text: "", label: "Hide the panel" };
  const alarms = count ? `${count.toLocaleString("en")} in alarm` : "";
  return { text: alarms || "Show the panel", label: alarms ? `Show the panel: ${alarms}` : "Show the panel" };
}

// `[` toggles it, unless typing in a field or another shortcut (Ctrl, Cmd).
// A Danish or German "[" needs AltGr (Ctrl+Alt) or Option (Alt): allowed.
export function isPanelShortcut(event: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "target">): boolean {
  if (event.key !== "[" || event.metaKey || (event.ctrlKey && !event.altKey)) return false;
  const target = event.target as HTMLElement | null;
  return !(target?.closest?.("input, textarea, select, [contenteditable]") || target?.isContentEditable);
}
