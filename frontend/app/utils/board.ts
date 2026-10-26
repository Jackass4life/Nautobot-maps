// The alert board (#463; static/js/alerts/*): which sites it lists, in which
// order, and the words on its rows, panels and feed.  No page access: the
// tests run these as they are.
import type { components } from "~/api/schema";
import { formatFeedTime, nautobotLocationUrl } from "./format";
import { siteAddress, siteSearchText } from "./sitesearch";
import { siteTenants, type Scope } from "./scope";

export type Board = components["schemas"]["Board"];
export type BoardSite = components["schemas"]["BoardSite"];
export type BoardSummary = components["schemas"]["BoardSummary"];
export type DownDevice = components["schemas"]["DownDevice"];
export type FeedEvent = components["schemas"]["FeedEvent"];
export type Feed = components["schemas"]["Feed"];
export type Incident = components["schemas"]["Incident"];

// Levels that are an active alarm; No data is not an alert (#124).
export const ALARM_LEVELS = ["critical", "medium", "low"];
// Most severe first; the server sorts the same way (ALERT_LEVEL_ORDER, #124).
export const SEVERITY_ORDER = ["critical", "medium", "low", "no_data", "ok"];

export type SeverityFilter = "alarms" | "wireless" | "all" | "no_data" | "maintenance";
export const DEFAULT_SEVERITY_FILTER: SeverityFilter = "alarms";
// The summary chips are the severity filter; the board opens on Alarms (#311).
// Wireless (#521): sites with only access points down, next to Alarms.
export const SEVERITY_CHIPS: { key: keyof BoardSummary | "alarms"; filter: SeverityFilter; label: string }[] = [
  { key: "alarms", filter: "alarms", label: "Alarms" },
  { key: "wireless", filter: "wireless", label: "Wireless" },
  { key: "total", filter: "all", label: "All sites" },
  { key: "no_data", filter: "no_data", label: "No data" },
  { key: "maintenance", filter: "maintenance", label: "Maintenance" },
];

export type Sort = "newest" | "severity" | "site";
// The default puts the site with the newest down device first (#228).
export const DEFAULT_SORT: Sort = "newest";
export const SORTS: { value: Sort; label: string }[] = [
  { value: "newest", label: "Sort: newest down first" },
  { value: "severity", label: "Sort: severity" },
  { value: "site", label: "Sort: site" },
];
// The operator's last Sort choice, and the feed's Hide/Show: the old board's keys.
export const SORT_KEY = "nautobot-maps-alert-sort";
export const FEED_COLLAPSED_KEY = "nautobot-maps-feed-collapsed";
// Below this width the open feed leaves the table too little room.
const FEED_OPEN_MIN_WIDTH_PX = 1700;
// Rows drawn at once; "Show more" adds as many (1,200 sites on All sites).
export const ROWS_STEP = 200;

export function isSort(value: unknown): value is Sort {
  return SORTS.some((sort) => sort.value === value);
}

export function severityWeight(level: string | null | undefined): number {
  const index = SEVERITY_ORDER.indexOf(level || "");
  return index === -1 ? SEVERITY_ORDER.length : index;
}

// Only access points down, per a wireless controller (#521): its level's
// colour, but not an alarm.
export function hasActiveAlarm(item: Pick<BoardSite, "alert_level" | "wireless_only">): boolean {
  return ALARM_LEVELS.includes(item.alert_level) && !item.wireless_only;
}

// The chips shown: Wireless only with such sites, or while it is chosen;
// without wireless controllers it never appears (#521).
export function shownChips(counts: Record<string, number>, selected: SeverityFilter) {
  return SEVERITY_CHIPS.filter((chip) => chip.filter !== "wireless" || counts.wireless || selected === "wireless");
}

export function alarmCount(summary: Partial<BoardSummary>): number {
  return summary.non_ok ?? ALARM_LEVELS.reduce((sum, level) => sum + (summary[level as keyof BoardSummary] || 0), 0);
}

// "2 alarms · 1 critical · 1 medium": only what is wrong, only levels that aren't 0 (#311).
export function boardSummaryText(summary: Partial<BoardSummary>): string {
  const alarms = alarmCount(summary);
  if (!alarms) return "No alarms";
  const levels = ALARM_LEVELS.filter((level) => summary[level as keyof BoardSummary]).map(
    (level) => `${summary[level as keyof BoardSummary]} ${level}`,
  );
  return [`${alarms} alarm${alarms === 1 ? "" : "s"}`, ...levels].join(" · ");
}

// The chips' counts: the server's summary, or for a link's sites only (#364).
export function chipCounts(summary: Partial<BoardSummary>): Record<string, number> {
  return {
    alarms: alarmCount(summary),
    total: summary.total || 0,
    no_data: summary.no_data || 0,
    maintenance: summary.maintenance || 0,
    wireless: summary.wireless || 0,
  };
}

// The server's summary for the sites in scope only: sites per level.
export function scopeSummary(sites: Pick<BoardSite, "alert_level" | "wireless_only">[]): Partial<BoardSummary> {
  const summary: Record<string, number> = { total: sites.length };
  sites.forEach((site) => {
    const level = site.wireless_only ? "wireless" : site.alert_level || "no_data";
    summary[level] = (summary[level] || 0) + 1;
  });
  return summary;
}

// "NO DATA", "CRITICAL".
export function levelLabel(level: string | null | undefined): string {
  const value = level || "no_data";
  return value === "no_data" ? "NO DATA" : value.toUpperCase();
}

// "1 down · 3 APs down · 46 monitored": access points a wireless controller
// says are down counted apart (#521), so a row of 40 APs reads at a glance.
export function siteSummaryText(
  item: Pick<BoardSite, "down_device_count" | "device_count" | "down_access_point_count" | "down_network_device_count">,
): string {
  const aps = item.down_access_point_count || 0;
  const monitored = `${item.device_count || 0} monitored`;
  if (!aps) return `${item.down_device_count || 0} down · ${monitored}`;
  const others = item.down_network_device_count || 0;
  return [others ? `${others} down` : "", `${aps} AP${aps === 1 ? "" : "s"} down`, monitored].filter(Boolean).join(" · ");
}

// Up to this many access points are listed with the other down devices;
// more fold into one line, "12 access points down" (#521).
export const ACCESS_POINTS_SHOWN = 3;

// A site's down devices for its rows: the others, then the access points a
// controller watches at the wireless level, *folded* when there are more than
// ACCESS_POINTS_SHOWN.  One set in Nautobot or core stays with the others.
export function downDeviceRows(devices: DownDevice[]): { shown: DownDevice[]; folded: DownDevice[] } {
  const quiet = (device: DownDevice) => Boolean(device.access_point && !device.set_level && !device.core);
  const others = devices.filter((device) => !quiet(device));
  const aps = devices.filter(quiet);
  return aps.length > ACCESS_POINTS_SHOWN ? { shown: others, folded: aps } : { shown: [...others, ...aps], folded: [] };
}

export function accessPointsText(count: number): string {
  return `${count.toLocaleString("en")} access point${count === 1 ? "" : "s"} down`;
}

// The site's down devices: down now, or with an alert still open.  It can be
// non-empty while down_device_count is 0 (a failed observation keeps open
// alerts), so the toggle, the actions and History all use this.
export function caseDevices(item: Pick<BoardSite, "down_devices">): DownDevice[] {
  return (item.down_devices || []).filter((device) => device.device_id);
}

// Whether someone is on a site (#271): "handled" when every down device has a
// case or an acknowledgement (#288), "partly" when some do, "none" otherwise.
export function siteCaseState(item: Pick<BoardSite, "down_devices">): {
  state: "handled" | "partly" | "none";
  without: number;
  total: number;
} {
  // Devices in maintenance are planned work, not waiting for a case (#283).
  const devices = (item.down_devices || []).filter((device) => !device.maintenance_until);
  const handled = devices.filter((device) => device.case_numbers?.length || device.acknowledged).length;
  let state: "handled" | "partly" | "none" = "none";
  if (devices.length && handled === devices.length) state = "handled";
  else if (handled) state = "partly";
  return { state, without: devices.length - handled, total: devices.length };
}

export function formatDuration(seconds: number | null | undefined): string {
  const total = Number(seconds || 0);
  if (!Number.isFinite(total) || total <= 0) return "—";
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  // "283 days", not "6787h 44m" (#311).
  if (hours >= 48) return `${Math.floor(hours / 24)} days`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

// "in alarm 3h 40m" for a site and "down 25m" for a device (#275).
// *seconds*: null when nothing is open ("—"); NaN when its start isn't known
// (never a reassuring "<1m"); under a minute is "<1m" (#279).
export function sinceLabel(what: string, seconds: number | null): string {
  if (seconds === null) return "—";
  if (Number.isNaN(seconds)) return `${what}, time unknown`;
  return seconds < 60 ? `${what} <1m` : `${what} ${formatDuration(seconds)}`;
}

export function deviceDowntimeSeconds(device: Pick<DownDevice, "down_started_at">, now = Date.now()): number {
  const since = Date.parse(device.down_started_at || "");
  return Number.isNaN(since) ? NaN : Math.max(0, (now - since) / 1000);
}

// The site's time in alarm: null with nothing down; NaN when none of its
// down devices has a known start.
export function siteAlarmSeconds(item: Pick<BoardSite, "alert_level" | "down_devices" | "current_downtime_seconds">): number | null {
  if (item.alert_level === "maintenance") return null;
  const devices = (item.down_devices || []).filter((device) => !device.maintenance_until);
  if (!devices.length) return null;
  const known = devices.some((device) => !Number.isNaN(Date.parse(device.down_started_at || "")));
  return known ? item.current_downtime_seconds || 0 : NaN;
}

// "until 14:00 · core switch upgrade" for a maintenance window (#283).
export function maintenanceNote(until: string | null | undefined, reason: string | null | undefined, now = new Date()): string {
  const when = formatFeedTime(until, now);
  return [when ? `until ${when}` : "", reason || ""].filter(Boolean).join(" · ");
}

// "ACK olga · on my way" under an acknowledged device (#288).
export function ackText(device: Pick<DownDevice, "acknowledged">): string {
  const ack = device.acknowledged;
  if (!ack) return "";
  return [ack.by ? `ACK ${ack.by}` : "ACK", ack.note || ""].filter(Boolean).join(" · ");
}

// A device going up and down (#286): the label's hover text.
export function flapTitle(device: Pick<DownDevice, "flap_changes">): string {
  const changes = Number(device.flap_changes) || 0;
  return `Up and down ${changes === 1 ? "1 time" : `${changes} times`} in the last 30 minutes`;
}

// A level set on the device in Nautobot (#377): only known levels.
export function setLevel(device: Pick<DownDevice, "set_level">): string {
  return ALARM_LEVELS.includes(device.set_level || "") ? device.set_level! : "";
}

// A down device's stack (#402, #405): "Stack sw-stack01: all 4 members down"
// (with *names*, the members too, for a ticket), else "Stack sw-stack01,
// member 2 of 4"; "" when it isn't a stack.
export function stackText(stack: DownDevice["stack"], { names = false } = {}): string {
  if (!stack || !Number.isInteger(stack.size) || stack.size < 2) return "";
  const name = `Stack${stack.name ? ` ${stack.name}` : ""}`;
  if (!stack.whole) {
    const where = stack.master ? "master" : `member${Number.isInteger(stack.position) ? ` ${stack.position}` : ""}`;
    return `${name}, ${where} of ${stack.size}`;
  }
  const members = names && stack.members?.length ? ` (${stack.members.join(", ")})` : "";
  return `${name}: all ${stack.size} members down${members}`;
}

// The line under a down device: where, its role, its stack, status, why.
export function deviceMeta(device: DownDevice): string {
  const parts = [device.location_path, device.role, stackText(device.stack), device.flapping ? "" : device.status, device.down_reason];
  return parts.filter(Boolean).join(" · ") || "Down device";
}

// A case number's ticket (#289): the template with the encoded number, or "".
export function caseUrl(value: string, template: string): string {
  return template && value ? template.split("{case}").join(encodeURIComponent(value)) : "";
}

// "INC-1 (https://itsm…/INC-1)" in plain text; the number alone without a template.
function caseWithUrl(value: string, template: string): string {
  const url = caseUrl(value, template);
  return url ? `${value} (${url})` : value;
}

export function onTheMap(item: Pick<BoardSite, "id" | "latitude" | "longitude">): boolean {
  return Boolean(item.id) && Number.isFinite(item.latitude) && Number.isFinite(item.longitude);
}

// Whether a site passes the chips: Alarms, one level, or all.
export function matchesSeverityFilter(item: Pick<BoardSite, "alert_level" | "wireless_only">, filter: SeverityFilter): boolean {
  if (filter === "all") return true;
  if (filter === "alarms") return hasActiveAlarm(item);
  if (filter === "wireless") return Boolean(item.wireless_only);
  return (item.alert_level || "no_data") === filter;
}

// Its place by severity: only access points down after every alarm, before
// No data, as the server sorts (#521).
function siteWeight(item: Pick<BoardSite, "alert_level" | "wireless_only">): number {
  return item.wireless_only ? severityWeight("low") + 0.5 : severityWeight(item.alert_level);
}

function compareText(left: string | null | undefined, right: string | null | undefined): number {
  return (left || "").localeCompare(right || "");
}

// Milliseconds of the site's newest open alert start, or null without one.
export function latestDownTime(item: Pick<BoardSite, "latest_down_at">): number | null {
  const parsed = Date.parse(item.latest_down_at || "");
  return Number.isNaN(parsed) ? null : parsed;
}

export function compareSites(a: BoardSite, b: BoardSite, sort: Sort): number {
  if (sort === "site") return compareText(a.name, b.name);
  if (sort === "newest") {
    // Newest down first (#228); sites with nothing down follow, by severity.
    // Only access points down: after the alarms, however new (#521).
    const left = latestDownTime(a);
    const right = latestDownTime(b);
    const rank = (item: BoardSite, time: number | null) => (time === null ? 2 : item.wireless_only ? 1 : 0);
    if (rank(a, left) !== rank(b, right)) return rank(a, left) - rank(b, right);
    if (left !== right) {
      if (left === null) return 1;
      if (right === null) return -1;
      return right - left;
    }
  }
  return (
    siteWeight(a) - siteWeight(b) ||
    // Within a level, more important sites first (#293).
    (a.tier_rank ?? 99) - (b.tier_rank ?? 99) ||
    (b.down_device_count || 0) - (a.down_device_count || 0) ||
    compareText(a.name, b.name)
  );
}

export interface BoardCriteria {
  site: string;
  status: string[];
  type: string[];
  tenant: string[];
  sort: Sort;
  severity: SeverityFilter;
  nonOperational: boolean;
}

export function noBoardCriteria(sort: Sort = DEFAULT_SORT): BoardCriteria {
  return { site: "", status: [], type: [], tenant: [], sort, severity: DEFAULT_SEVERITY_FILTER, nonOperational: false };
}

// A filter with values: any of them; without: everything.
function matchesValues(values: string | string[], wanted: string[]): boolean {
  if (!wanted.length) return true;
  return (Array.isArray(values) ? values : [values]).some((value) => wanted.includes(value));
}

// The filters' choices, from the sites in scope.
export function boardChoices(sites: BoardSite[]): { status: string[]; type: string[]; tenant: string[] } {
  const sorted = (values: string[]) => [...new Set(values.filter(Boolean))].sort();
  return {
    status: sorted(sites.map((site) => site.status)),
    type: sorted(sites.map((site) => site.location_type)),
    tenant: sorted(sites.flatMap(siteTenants)),
  };
}

// The sites that pass *criteria*, sorted.
export function filterSites(sites: BoardSite[], criteria: BoardCriteria): BoardSite[] {
  const needle = criteria.site.trim().toLowerCase();
  return sites
    .filter(
      (item) =>
        matchesSeverityFilter(item, criteria.severity) &&
        (!needle || siteSearchText(item).includes(needle)) &&
        matchesValues(item.status, criteria.status) &&
        matchesValues(item.location_type, criteria.type) &&
        matchesValues(siteTenants(item), criteria.tenant),
    )
    .sort((a, b) => compareSites(a, b, criteria.sort));
}

// "More filters (2)": the hidden filters say when they are in use.
export function moreFiltersInUse(criteria: BoardCriteria): number {
  return [criteria.status.length, criteria.type.length, criteria.nonOperational, criteria.sort !== DEFAULT_SORT].filter(Boolean)
    .length;
}

// Reset is shown only when there is something to reset (#311).
export function filtersInUse(criteria: BoardCriteria): boolean {
  return Boolean(
    criteria.site.trim() || criteria.tenant.length || moreFiltersInUse(criteria) || criteria.severity !== DEFAULT_SEVERITY_FILTER,
  );
}

// The wall for the sites the board shows (#366): the tenant filter, else the
// link's tenants; the link's q= words and the filter box.  null when nothing
// narrows the board.
export function wallLinkFor({ tenant, text, scope }: { tenant: string[]; text: string; scope: Scope | null }): string | null {
  const params = new URLSearchParams({ view: "wall" });
  (tenant.length ? tenant : scope?.tenants || []).forEach((name) => params.append("tenant", name));
  [...(scope?.texts || []), text.trim()].filter(Boolean).forEach((q) => params.append("q", q));
  return params.has("tenant") || params.has("q") ? `/alerts?${params}` : null;
}

// Board filters a wall can't take: it shows alarms, whatever their status or type.
export function filtersLeftOffTheWall(criteria: Pick<BoardCriteria, "status" | "type">): string[] {
  return [criteria.status.length && "Status", criteria.type.length && "Type"].filter(Boolean) as string[];
}

// Why the board is empty: no database, the first sync, a link that matches
// nothing (never an all-clear, #364), nothing in alarm, or the filters.
export function emptyBoardText(
  payload: Pick<Board, "persistence_configured" | "sync_pending" | "alerts">,
  { inScope, severity, scopeText }: { inScope: number; severity: SeverityFilter; scopeText: string },
  anyAlarm: boolean,
): { text: string; kind: "plain" | "no-match" | "all-clear" } {
  if (payload.persistence_configured === false) {
    return {
      text: "The alert board needs a PostgreSQL database. Set NAUTOBOT_MAPS_DATABASE_URL and restart the app. The map works without it.",
      kind: "plain",
    };
  }
  if (payload.sync_pending && !inScope) {
    return { text: "Inventory sync in progress – the board will update automatically.", kind: "plain" };
  }
  if (scopeText && !inScope && payload.alerts.length) {
    return { text: `No site matches ${scopeText}. Check the link's tenant= and q=.`, kind: "no-match" };
  }
  if (severity === "alarms" && !anyAlarm) {
    return { text: "No site has an active alarm. Choose All sites to see every site.", kind: "all-clear" };
  }
  return { text: "No sites match the current filters.", kind: "plain" };
}

// "12 sites with alarms · updated 14:05", "stale" only when it is (#311).
export function boardStatusText(
  payload: Pick<Board, "checked_at" | "stale" | "sync_pending">,
  count: number,
  alarmsOnly: boolean,
): string {
  const checkedAt = payload.checked_at ? new Date(payload.checked_at) : null;
  const time =
    checkedAt && !Number.isNaN(checkedAt.valueOf())
      ? checkedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : "unknown";
  return [
    `${count} site${count !== 1 ? "s" : ""}${alarmsOnly ? " with alarms" : ""}`,
    `updated ${time}`,
    payload.stale ? "data may be out of date" : "",
    payload.sync_pending ? "inventory sync in progress…" : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

// "3:42", or "1:02:05" from an hour.
export function formatCountdown(totalSeconds: number): string {
  const seconds = Math.max(0, Math.ceil(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = String(seconds % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

// The feed's Hide/Show: the user's last choice; without one, whether there is room.
export function feedStartsCollapsed(stored: string | null, viewportWidth: number): boolean {
  if (stored === "1" || stored === "0") return stored === "1";
  return viewportWidth < FEED_OPEN_MIN_WIDTH_PX;
}

// A feed entry's words (#180): what, the meta line; "down since" too when it
// went down a minute or more before it was noticed (#166).
export function feedEntry(event: FeedEvent, now = new Date()): { title: string; text: string; meta: string } {
  const at = formatFeedTime(event.at, now);
  const site = event.site_name || event.site_id || "Unknown site";
  const device = event.device_name || event.device_id || "Unknown device";
  if (event.kind === "severity") return { title: site, text: "", meta: at };
  if (event.kind === "up") return { title: device, text: "back up", meta: `${at} · ${site}` };
  const since = new Date(event.down_since ?? "");
  const earlier =
    !Number.isNaN(since.valueOf()) && new Date(event.at).getTime() - since.getTime() >= 60000
      ? ` · down since ${formatFeedTime(event.down_since, now)}`
      : "";
  return { title: device, text: "down", meta: `${at} · ${site}${earlier}` };
}

function devicesLabel(count: number): string {
  return `${count} device${count === 1 ? "" : "s"}`;
}

// The + Case button (#273): *already*, ticked devices that already have the
// typed number; saving it there again would change nothing.
export function caseButtonLabel(selected: number, already = 0): string {
  if (selected === 0) return "Select devices";
  if (already === selected) return `Already on ${devicesLabel(already)}`;
  if (already) return `Add case to ${devicesLabel(selected - already)} (already on ${already})`;
  return `Add case to ${devicesLabel(selected)}`;
}

// Whether a device already has *caseNumber*, ignoring case: "inc-881" next
// to "INC-881" would be a second ticket by mistake.
export function hasCase(device: Pick<DownDevice, "case_numbers">, caseNumber: string): boolean {
  const wanted = caseNumber.trim().toLowerCase();
  return Boolean(wanted) && (device.case_numbers || []).some((value) => value.toLowerCase() === wanted);
}

// The ticked devices that get *caseNumber*: those that don't have it yet.
// The button label and the request both use this, so they always agree.
export function caseTargets(devices: DownDevice[], ticked: Set<string>, caseNumber: string): string[] {
  return devices.filter((device) => ticked.has(device.device_id) && !hasCase(device, caseNumber)).map((device) => device.device_id);
}

// The acknowledge request (#288): no devices = every down device, sent as
// null; the API refuses an empty list, so bad input never means "all".
export function ackRequestBody(siteId: string, deviceIds: string[], note = ""): { site_id: string; device_ids: string[] | null; note: string } {
  return { site_id: siteId, device_ids: deviceIds.length ? deviceIds : null, note };
}

// "2026-09-29 13:05 +02:00": local time with its offset, so a ticket read in
// another time zone is still right (#227).
export function formatReportTime(value: string | null | undefined): string {
  const parsed = new Date(value || "");
  if (!value || Number.isNaN(parsed.valueOf())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  const offset = -parsed.getTimezoneOffset();
  const zone = `${offset >= 0 ? "+" : "-"}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`;
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())} ${zone}`;
}

// A site and its down devices as plain text, for an ITSM ticket (#227): one
// line per device, the likely cause first (#285), then newest down first.
export function siteReport(item: BoardSite, { nautobotUrl = "", caseTemplate = "" } = {}): string {
  const level = item.alert_level || "no_data";
  const lines = [`Site: ${item.name || item.id || "Unknown site"}`];
  const path = item.ancestor_path || item.parent;
  if (path) lines.push(`Location: ${path}`);
  const address = siteAddress(item);
  if (address) lines.push(`Address: ${address}`);
  const tenants = siteTenants(item);
  if (tenants.length > 1) lines.push(`Tenants (${tenants.length}): ${tenants.join(", ")}`);
  else if (tenants.length) lines.push(`Tenant: ${tenants[0]}`);
  lines.push(`Severity: ${levelLabel(level)} (${item.down_device_count || 0} of ${item.device_count || 0} devices down)`);
  if (item.alert_reason) lines.push(`Reason: ${item.alert_reason}`);
  if (item.active_cases?.length) lines.push(`Cases: ${item.active_cases.map((value) => caseWithUrl(value, caseTemplate)).join(", ")}`);
  if (nautobotUrl && item.id) lines.push(`Nautobot: ${nautobotLocationUrl(nautobotUrl, item.id)}`);

  const devices = [...(item.down_devices || [])].sort(
    (a, b) =>
      Number(Boolean(b.core)) - Number(Boolean(a.core)) ||
      (Date.parse(b.down_started_at || "") || 0) - (Date.parse(a.down_started_at || "") || 0),
  );
  const cores = devices.filter((device) => device.core).map((device) => device.device_name || device.device_id);
  const behind = devices.filter((device) => device.probable_cause).length;
  if (cores.length && behind) {
    lines.push(`Likely cause: ${cores.join(", ")} down; ${behind} other device${behind === 1 ? "" : "s"} probably behind it`);
  }
  lines.push("", `Down devices (${devices.length}):`);
  devices.forEach((device) => {
    const since = formatReportTime(device.down_started_at);
    const cases = device.case_numbers || [];
    const fields = [
      device.device_name || device.device_id || "Unknown device",
      `IP ${device.device_ip || "—"}`,
      `Role ${device.role || "—"}`,
      stackText(device.stack, { names: true }),
      `Status ${device.status || "—"}`,
      device.location_path ? `Location ${device.location_path}` : "",
      since ? `Down since ${since}` : "",
      cases.length ? `Case ${cases.map((value) => caseWithUrl(value, caseTemplate)).join(", ")}` : "",
      device.core ? "Core device" : "",
      device.set_level ? `Set ${device.set_level} in Nautobot` : "",
      device.probable_cause ? `Likely caused by ${device.probable_cause}` : "",
    ];
    lines.push(`- ${fields.filter(Boolean).join(" | ")}`);
  });
  return lines.join("\n");
}

// The site's history as a CSV download (#277).
export function historyExportUrl(siteId: string, view: "incidents" | "devices", days: string): string {
  return `/api/alert-history.csv?${new URLSearchParams({ site_id: siteId, days, view })}`;
}
export const HISTORY_PERIODS = [
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
  { value: "all", label: "All" },
];
