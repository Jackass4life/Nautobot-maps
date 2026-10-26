// The site panel (#394, #398, #400, #405): its status line, the down devices
// in the board's order, stacks as one device, and each device's facts.
import type { components } from "~/api/schema";
import { ALERT_RANK, LEVEL_NAMES, knownLevel } from "./map";

export type SiteDevice = components["schemas"]["SiteDevice"];
export type SiteCircuit = components["schemas"]["SiteCircuit"];
export type LocationDetail = components["schemas"]["LocationDetail"];

export type Health = "active" | "down" | "unknown";

export function deviceHealth(status: string | null | undefined): Health {
  const value = (status || "").trim().toLowerCase();
  if (["active", "online", "up"].includes(value)) return "active";
  if (value.includes("offline") || ["down", "failed", "inactive"].includes(value)) return "down";
  return "unknown";
}

// The site's state in one line (#394): its level's name, how many are down,
// since when; the reason under it, only for an alarm or maintenance.
export function statusLine(
  alert: { level: string; reason?: string } | null | undefined,
  counts: { down?: number | null; monitored?: number | null } | null | undefined,
  since = "",
): { level: string; text: string; reason: string } {
  const level = knownLevel(alert?.level || "no_data");
  const monitored = counts && Number.isFinite(counts.monitored) ? counts.monitored! : null;
  let what = "";
  if (ALERT_RANK[level] && monitored !== null) what = `${counts?.down || 0} of ${monitored} down`;
  else if (level === "ok" && monitored !== null) what = `${monitored} device${monitored === 1 ? "" : "s"}`;
  else if (level === "no_data") what = "no monitored devices";
  const text = [LEVEL_NAMES[level], what, ALERT_RANK[level] && since ? `since ${since}` : ""].filter(Boolean).join(" · ");
  const reason = ALERT_RANK[level] || level === "maintenance" ? alert?.reason || "" : "";
  return { level, text, reason };
}

// A device's key in the panel's open/closed state: its id, else what tells it apart.
export function deviceKey(device: Pick<SiteDevice, "id" | "name" | "serial" | "primary_ip">): string {
  return String(device.id || [device.name, device.serial, device.primary_ip].join("|"));
}

// Whether a down device counts towards the site's level: not in maintenance,
// within the alert delay or unmonitored (#316).
function counted(device: SiteDevice): boolean {
  return !device.not_monitored && !device.maintenance_until && !device.alert_pending;
}

// The down devices that count, in the board's order (#394): the level each
// gives the site (#377), core devices first within it (#285), then by name.
const DEVICE_RANK: Record<string, number> = { critical: 0, medium: 1, low: 2 };
export function sortDownDevices(devices: SiteDevice[]): SiteDevice[] {
  const rank = (device: SiteDevice) => DEVICE_RANK[device.level] ?? 3;
  return devices
    .filter((device) => deviceHealth(device.status) === "down" && counted(device))
    .sort(
      (a, b) =>
        rank(a) - rank(b) || Number(Boolean(b.core)) - Number(Boolean(a.core)) || (a.name || "").localeCompare(b.name || ""),
    );
}

export interface StackEntry {
  device: SiteDevice;
  members: SiteDevice[];
}

// Stacks (#400): a virtual chassis of two or more as one entry, led by the
// member that stands for it on the board (#405): its monitored master, else
// its first monitored member, else its master, else the first by position.
export function groupStacks(devices: SiteDevice[]): StackEntry[] {
  const chassis = new Map<string, SiteDevice[]>();
  for (const device of devices) {
    if (!device.vc_id) continue;
    const all = chassis.get(device.vc_id);
    if (all) all.push(device);
    else chassis.set(device.vc_id, [device]);
  }
  const stacks = new Map<string, { lead: SiteDevice; members: SiteDevice[] }>();
  for (const [id, all] of chassis) {
    if (all.length < 2) continue;
    const byPosition = [...all].sort((a, b) => (a.vc_position ?? Infinity) - (b.vc_position ?? Infinity));
    const monitored = byPosition.filter((d) => d.primary_ip && !d.not_monitored);
    const lead = monitored.find((d) => d.vc_master) || monitored[0] || byPosition.find((d) => d.vc_master) || byPosition[0]!;
    stacks.set(id, { lead, members: byPosition.filter((d) => d !== lead) });
  }
  const entries: StackEntry[] = [];
  for (const device of devices) {
    const stack = device.vc_id ? stacks.get(device.vc_id) : undefined;
    if (!stack) entries.push({ device, members: [] });
    else if (stack.lead === device) entries.push({ device, members: stack.members });
  }
  return entries;
}

// Why it doesn't count, where the role would be (#316).
export function deviceNote(device: SiteDevice): string {
  if (device.not_monitored) return "Not monitored";
  if (device.maintenance_until) return "Maintenance";
  return device.alert_pending ? "Alert delay" : "";
}

// What the row says after the name: why it doesn't count, "Stack of 4", or its role.
export function deviceAside(device: SiteDevice, stackSize: number): string {
  const note = deviceNote(device);
  const stack = stackSize > 1 ? `Stack of ${stackSize}` : "";
  return note ? [stack, note].filter(Boolean).join(" · ") : stack || device.role;
}

// An opened device's facts, one per line.
export function deviceFacts(device: SiteDevice, aside: string): string[] {
  const health = deviceHealth(device.status);
  return [
    aside !== device.role ? device.role || "" : "",
    [device.manufacturer, device.device_type].filter(Boolean).join(" "),
    device.primary_ip ? `IP ${String(device.primary_ip).split("/")[0]}` : "",
    device.platform ? `Software ${device.platform}` : "",
    device.serial ? `Serial ${device.serial}` : "",
    device.tenant ? `Tenant ${device.tenant}` : "",
    device.status && health !== "active" ? `Status ${device.status}` : "",
  ].filter(Boolean);
}

// Why a device isn't in the site's "N down · M monitored", if it isn't (#316).
export function deviceCountNote(device: SiteDevice): string {
  if (device.not_monitored) return `Not monitored: ${device.not_monitored}`;
  if (device.maintenance_until) {
    const until = new Date(device.maintenance_until);
    const when = Number.isNaN(until.valueOf()) ? "" : ` until ${until.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
    return `In maintenance${when}: not counted`;
  }
  return device.alert_pending ? "Down less than the alert delay: not an alarm yet" : "";
}

// A stack member in its lead's details: place, name, model, serial.
export function memberText(member: SiteDevice): string {
  const facts = [member.device_type, member.serial ? `Serial ${member.serial}` : ""].filter(Boolean).join(" · ");
  const place = member.vc_position !== null && member.vc_position !== undefined ? `${member.vc_position}. ` : "";
  return `${place}${member.name || "Unnamed device"}${facts ? ` · ${facts}` : ""}`;
}

export interface HealthCounts {
  all: number;
  active: number;
  down: number;
  unknown: number;
}

export function healthCounts(devices: SiteDevice[]): HealthCounts {
  const counts: HealthCounts = { all: devices.length, active: 0, down: 0, unknown: 0 };
  for (const device of devices) counts[deviceHealth(device.status)] += 1;
  return counts;
}

export function filterDevices(devices: SiteDevice[], filter: "all" | Health, search: string): SiteDevice[] {
  const query = (search || "").trim().toLowerCase();
  return devices.filter((device) => {
    if (filter !== "all" && deviceHealth(device.status) !== filter) return false;
    if (!query) return true;
    return [device.name, device.status, device.manufacturer, device.device_type, device.role, device.platform, device.serial, device.tenant]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
}

// "Side A · 1 Gbps", with a different upstream speed said.
export function circuitTermination(circuit: SiteCircuit): string {
  let speed = circuit.port_speed || "";
  if (circuit.upstream_speed && circuit.upstream_speed !== circuit.port_speed) {
    speed = `${speed || "?"} down / ${circuit.upstream_speed} up`;
  }
  return [circuit.term_side ? `Side ${circuit.term_side}` : "", speed].filter(Boolean).join(" · ");
}

// Found by its description being the site's address, not a termination (#383).
export function circuitMatchNote(circuit: Pick<SiteCircuit, "match" | "shared_with">): string {
  if (circuit.match !== "address") return "";
  const others = Number(circuit.shared_with) || 0;
  return `By address${others > 0 ? ` · address shared with ${others} other site${others === 1 ? "" : "s"}` : ""}`;
}

// The site's maintenance history (#353), as on the board's ⋯ menu.
export function maintenanceHistoryHref(siteId: string): string {
  return `/maintenance?${new URLSearchParams({ view: "history", site_id: siteId })}`;
}

// Who is on it (#512): the site's cases, the first few and "+N" (a core
// outage can have many), and the down devices nobody is on: counted on the
// board, without a case or an acknowledgement.
export const CASES_SHOWN = 3;

export function casesShown(cases: string[]): { shown: string[]; more: number } {
  return { shown: cases.slice(0, CASES_SHOWN), more: Math.max(0, cases.length - CASES_SHOWN) };
}

export function nobodyOnIt(down: SiteDevice[]): number {
  return down.filter((device) => counted(device) && !device.case_numbers?.length && !device.acknowledged_by).length;
}

// What the panel says about them: "No case" when nobody is on any, "29
// without a case" when someone is on some; nothing when the cases couldn't be
// read (no database), rather than a false alarm.
export function noCaseText(down: SiteDevice[], casesRead: boolean, hasCases: boolean): string {
  const waiting = nobodyOnIt(down);
  if (!casesRead || !waiting) return "";
  return !hasCases && waiting === down.filter(counted).length ? "No case" : `${waiting.toLocaleString("en")} without a case`;
}

// A down device's case on its row: "INC0048213", "INC0048213 +1".
export function deviceCase(device: SiteDevice): string {
  const cases = device.case_numbers || [];
  if (!cases.length) return "";
  return cases.length > 1 ? `${cases[0]} +${cases.length - 1}` : cases[0]!;
}
