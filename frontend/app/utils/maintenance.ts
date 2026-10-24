// The Maintenance page (#343, #353; static/js/maintenance/): windows planned
// together are one batch, so a change across 30 sites is one line; the
// groups Active now, Planned and Ended; the site picker and the request.
import type { components } from "~/api/schema";
import { formatFeedTime, hasAllWords, searchWords } from "./format";
import { whenRequest, type WhenValues } from "./when";

export type MaintenanceWindow = components["schemas"]["DescribedWindow"];
export type PlannableSite = components["schemas"]["PlannableSite"];
export type MaintenanceDevice = components["schemas"]["MaintenanceDevice"];
export type WindowState = MaintenanceWindow["state"];
export type BatchGroup = "active" | "upcoming" | "past";

export interface Batch {
  key: string;
  group: BatchGroup;
  state: WindowState;
  windows: MaintenanceWindow[];
  starts_at: string;
  ends_at: string;
  ended_at: string | null;
  reason: string;
  created_by: string;
  tenants: string[];
  sites: string[];
  text: string;
}

// Windows, and batches, shown per group and per "Show more".
export const MAINTENANCE_STEP = 25;
// A second click within this long ends a batch of more than one (no dialogs).
export const CONFIRM_MS = 4000;

export function groupsFor(days: number): { id: BatchGroup; title: string; empty: string }[] {
  return [
    { id: "active", title: "Active now", empty: "Nothing in maintenance now." },
    { id: "upcoming", title: "Planned", empty: "Nothing planned." },
    { id: "past", title: `Ended in the last ${days} day${days === 1 ? "" : "s"}`, empty: "Nothing ended lately." },
  ];
}

export const BADGES: Record<WindowState, string> = {
  active: "ACTIVE",
  upcoming: "PLANNED",
  ended: "ENDED",
  cancelled: "CANCELLED",
};

const GROUP_ORDER: Record<BatchGroup, number> = { active: 0, upcoming: 1, past: 2 };

function groupOf(state: WindowState): BatchGroup {
  return state === "active" || state === "upcoming" ? state : "past";
}

export function siteLabel(window: MaintenanceWindow): string {
  return window.site_name || window.site_id;
}

// Windows planned in one request (batch_id) are one batch; once over, the
// cancelled ones apart from those that ran.  *keepOrder* (the history): as
// the server sent them, newest first.
export function batchesOf(windows: MaintenanceWindow[], { keepOrder = false } = {}): Batch[] {
  const batches = new Map<string, Batch>();
  for (const window of windows) {
    const group = groupOf(window.state);
    const state = group === "past" ? window.state : group;
    const key = `${state}|${window.batch_id || `window-${window.id}`}`;
    let batch = batches.get(key);
    if (!batch) {
      batch = {
        key,
        group,
        state: window.state,
        windows: [],
        starts_at: window.starts_at,
        ends_at: window.ends_at,
        ended_at: window.ended_at,
        reason: window.reason || "",
        created_by: window.created_by || "",
        tenants: [],
        sites: [],
        text: "",
      };
      batches.set(key, batch);
    }
    batch.windows.push(window);
    // Ended at different times: the latest.
    if (window.ended_at && !(Date.parse(batch.ended_at ?? "") >= Date.parse(window.ended_at))) batch.ended_at = window.ended_at;
  }
  for (const batch of batches.values()) {
    batch.tenants = [...new Set(batch.windows.flatMap((window) => window.tenants || []))].sort();
    batch.sites = [...new Set(batch.windows.map(siteLabel))];
    batch.text = [
      batch.reason,
      batch.created_by,
      ...batch.tenants,
      ...batch.windows.flatMap((w) => [siteLabel(w), w.site_path || "", w.device_name || w.device_id]),
    ]
      .join(" ")
      .toLowerCase();
  }
  if (keepOrder) return [...batches.values()];
  // By group; then ending soonest first, planned soonest first, ended latest first.
  const time = (batch: Batch) =>
    Date.parse(batch.group === "active" ? batch.ends_at : batch.group === "upcoming" ? batch.starts_at : batch.ended_at || batch.ends_at);
  return [...batches.values()].sort(
    (a, b) => GROUP_ORDER[a.group] - GROUP_ORDER[b.group] || (a.group === "past" ? time(b) - time(a) : time(a) - time(b)),
  );
}

// Search: reason, person, sites, paths, devices and tenants; and any of the chosen tenants.
export function batchMatches(batch: Batch, { words = [], tenants = [] }: { words?: string[]; tenants?: string[] }): boolean {
  if (tenants.length && !batch.tenants.some((tenant) => tenants.includes(tenant))) return false;
  return hasAllWords(batch.text, words);
}

// "Aarhus", "Aarhus · sw1", "3 devices at Aarhus", "30 sites".
export function batchTitle(batch: Batch): string {
  const { windows, sites } = batch;
  if (windows.length === 1) {
    const window = windows[0]!;
    return window.device_id ? `${siteLabel(window)} · ${window.device_name || window.device_id}` : siteLabel(window);
  }
  if (sites.length === 1) return `${windows.length} devices at ${sites[0]}`;
  return `${sites.length} sites`;
}

export function whenText(batch: Batch, now = new Date()): string {
  if (batch.state === "cancelled") return "cancelled";
  if (batch.group === "past") return `ended ${formatFeedTime(batch.ended_at || batch.ends_at, now)}`;
  if (batch.group === "active") return `until ${formatFeedTime(batch.ends_at, now)}`;
  return `${formatFeedTime(batch.starts_at, now)} – ${formatFeedTime(batch.ends_at, now)}`;
}

// The history's (#353): when it ran, and whether it ended early.
export function spanText(batch: Batch, now = new Date()): string {
  const at = (value: string) => formatFeedTime(value, now);
  if (batch.state === "cancelled") return `cancelled · was planned ${at(batch.starts_at)}`;
  if (batch.group === "active") return `since ${at(batch.starts_at)}, until ${at(batch.ends_at)}`;
  if (batch.group === "upcoming") return `planned ${at(batch.starts_at)} – ${at(batch.ends_at)}`;
  const end = batch.ended_at || batch.ends_at;
  const early = batch.ended_at && Date.parse(batch.ended_at) < Date.parse(batch.ends_at) ? " · ended early" : "";
  return `${at(batch.starts_at)} – ${at(end)}${early}`;
}

// "End now", "Cancel", "End all 30", "Cancel all 30".
export function endLabel(batch: Pick<Batch, "group">, count: number): string {
  const verb = batch.group === "upcoming" ? "Cancel" : "End";
  if (count > 1) return `${verb} all ${count}`;
  return batch.group === "upcoming" ? "Cancel" : "End now";
}

// "3 · 2 sites": how many batches, at how many sites.
export function countText(batches: Batch[]): string {
  if (!batches.length) return "";
  const sites = new Set(batches.flatMap((b) => b.windows.map((w) => w.site_id))).size;
  return `${batches.length} · ${sites} site${sites === 1 ? "" : "s"}`;
}

// --- planning ---------------------------------------------------------------

// At most this many sites listed under the search; a tenant adds all of its own.
export const MAX_MATCHES = 8;

export type PickerSite = PlannableSite & { lowerName: string; lowerTenants: [string, string][]; text: string };

const byName = new Intl.Collator("en", { numeric: true, sensitivity: "base" });

// The sites as the picker searches them: sorted by name and lower-cased once.
export function prepareSites(sites: PlannableSite[]): PickerSite[] {
  return [...sites]
    .sort((a, b) => byName.compare(a.name, b.name))
    .map((site) => ({
      ...site,
      lowerName: site.name.toLowerCase(),
      lowerTenants: (site.tenants || []).map((tenant): [string, string] => [tenant, tenant.toLowerCase()]),
      text: [site.name, site.path, ...(site.tenants || [])].join(" ").toLowerCase(),
    }));
}

// Sites with every word in the name, path or a tenant, not yet chosen: those
// whose name has the phrase first, then by name; and the tenants whose name
// has it, with all their sites (two or more, the three biggest).
export function matchPlannableSites(
  sites: PickerSite[],
  query: string,
  chosen: Set<string> = new Set(),
): { sites: PickerSite[]; tenants: { name: string; ids: string[] }[]; more: number } {
  const words = searchWords(query);
  if (!words.length) return { sites: [], tenants: [], more: 0 };
  const phrase = words.join(" ");
  const named: PickerSite[] = [];
  const other: PickerSite[] = [];
  const byTenant = new Map<string, string[]>();
  for (const site of sites) {
    if (chosen.has(site.id)) continue;
    if (hasAllWords(site.text, words)) (site.lowerName.includes(phrase) ? named : other).push(site);
    for (const [tenant, lower] of site.lowerTenants) {
      if (!lower.includes(phrase)) continue;
      if (!byTenant.has(tenant)) byTenant.set(tenant, []);
      byTenant.get(tenant)!.push(site.id);
    }
  }
  const hits = named.concat(other);
  const tenants = [...byTenant]
    .map(([name, ids]) => ({ name, ids }))
    .filter((tenant) => tenant.ids.length > 1)
    .sort((a, b) => b.ids.length - a.ids.length)
    .slice(0, 3);
  return { sites: hits.slice(0, MAX_MATCHES), tenants, more: Math.max(0, hits.length - MAX_MATCHES) };
}

export interface PlanValues extends WhenValues {
  siteIds: string[];
  scope: "site" | "devices";
  deviceIds: string[];
  reason: string;
}

// The request for new windows, or {error}; *maxSites*: the server's limit.
export function windowRequest(values: PlanValues, maxSites = 200): Record<string, unknown> & { error?: string } {
  const { siteIds } = values;
  if (!siteIds.length) return { error: "Choose at least one site." };
  if (siteIds.length > maxSites) return { error: `At most ${maxSites} sites at once.` };
  const reason = (values.reason || "").trim();
  if (!reason) return { error: "Enter a reason." };
  let body: Record<string, unknown>;
  if (values.scope === "devices") {
    if (siteIds.length !== 1) return { error: "Choose devices for one site at a time." };
    if (!values.deviceIds.length) return { error: "Tick at least one device, or choose the whole site." };
    body = { site_id: siteIds[0], reason, device_ids: values.deviceIds };
  } else {
    body = siteIds.length === 1 ? { site_id: siteIds[0], reason } : { site_ids: siteIds, reason };
  }
  const when = whenRequest(values);
  return "error" in when ? when : { ...body, ...when };
}
