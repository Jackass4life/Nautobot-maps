// The wall screen, /alerts?view=wall (#243; on Nuxt since #463): alarms
// only, every site open, nothing to click; the sites nobody is on first
// (#271), maintenance last (#283); a chime and a notification for a new
// Critical or a site nobody is on (#290).  No page access at import.
import { compareSites, hasActiveAlarm, siteCaseState, type BoardSite } from "./board";

// The wall reloads every minute as well, so a screen nobody touches
// recovers from errors and an unknown next update.
export const WALL_RELOAD_MS = 60000;
// No successful load for this long: said loudly, the data is old.
export const WALL_STALE_MS = 10 * 60000;
export const WALL_SOUND_KEY = "nautobot-maps-wall-sound";

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Nobody on it, someone on it, then planned maintenance.
export function caseSortRank(item: BoardSite): number {
  if (item.alert_level === "maintenance") return 2;
  return siteCaseState(item).state === "handled" ? 1 : 0;
}

// The wall's sites: those in alarm, then those in maintenance; within each
// rank newest down first.
export function wallSites(sites: BoardSite[]): BoardSite[] {
  return sites
    .filter((item) => hasActiveAlarm(item) || item.alert_level === "maintenance")
    .sort((a, b) => caseSortRank(a) - caseSortRank(b) || compareSites(a, b, "newest"));
}

// The wall's case badge: readable at a glance, with text as well as colour.
export function caseBadge(item: BoardSite): { state: "handled" | "partly" | "none"; text: string } {
  const { state, without, total } = siteCaseState(item);
  if (state === "handled") {
    const devices = item.down_devices || [];
    const cases = [...new Set(devices.flatMap((device) => device.case_numbers || []))];
    const acked = devices.some((device) => device.acknowledged);
    return { state, text: `✓ ${[cases.join(", "), acked ? "ACK" : ""].filter(Boolean).join(" · ")}` };
  }
  if (state === "partly") return { state, text: `${without} of ${total} no case or ack` };
  return { state, text: "NO CASE" };
}

// "Updated 14:05 · 3 sites with alarms · 1 without case or ack"; "NOT
// UPDATED since 14:05" once no load has worked for a while.  A screen for
// some sites says which, so it isn't read as the whole network (#364).
export function wallStatus(
  sites: BoardSite[],
  lastLoadedAt: number | null,
  now: number,
  scopeText = "",
): { text: string; stale: boolean } {
  if (lastLoadedAt === null) return { text: "Loading…", stale: false };
  const scope = scopeText ? `${scopeText} · ` : "";
  if (now - lastLoadedAt >= WALL_STALE_MS) return { text: `${scope}NOT UPDATED since ${clock(lastLoadedAt)}`, stale: true };
  const alarms = sites.filter(hasActiveAlarm);
  const without = alarms.filter((item) => siteCaseState(item).state !== "handled").length;
  const planned = sites.filter((item) => item.alert_level === "maintenance").length;
  const text =
    `${scope}Updated ${clock(lastLoadedAt)} · ${alarms.length} site${alarms.length === 1 ? "" : "s"} with alarms · ` +
    `${without} without case or ack${planned ? ` · ${planned} in maintenance` : ""}`;
  return { text, stale: false };
}

// The banner while loads fail; the last good board stays on screen.
export function wallErrorText(message: string, lastLoadedAt: number | null): string {
  const since = lastLoadedAt === null ? "" : ` Showing the board from ${clock(lastLoadedAt)}.`;
  return `Alert board not updating: ${message}.${since}`;
}

export interface WallAlarm {
  id: string;
  name: string;
  kind: "critical" | "nobody";
}

// The alarms new since *before*: sites that became Critical, and sites in
// alarm with nobody on them (no case, no acknowledgement).  *before* null =
// the first load, which only records what is there.
export function newAlarms(
  before: Map<string, Set<string>> | null,
  sites: BoardSite[],
): { events: WallAlarm[]; seen: Map<string, Set<string>> } {
  const seen = new Map<string, Set<string>>();
  const events: WallAlarm[] = [];
  sites.forEach((site) => {
    const id = String(site.id || "");
    // Only access points down is not an alarm: no sound (#521).
    if (!id || !hasActiveAlarm(site)) return;
    const kinds: WallAlarm["kind"][] = [];
    if (site.alert_level === "critical") kinds.push("critical");
    if (siteCaseState(site).state === "none") kinds.push("nobody");
    seen.set(id, new Set(kinds));
    if (!before) return;
    const previous = before.get(id) || new Set();
    kinds.filter((kind) => !previous.has(kind)).forEach((kind) => events.push({ id, name: site.name || id, kind }));
  });
  return { events, seen };
}

export function alarmText(event: WallAlarm): string {
  return event.kind === "critical" ? `${event.name} is Critical` : `${event.name}: in alarm, nobody on it`;
}

// What the notifier needs of the browser's Notification (a stand-in in tests).
export interface NotificationLike {
  addEventListener(kind: "close", listener: () => void): void;
}
export interface NotificationApi {
  permission: string;
  new (title: string, options: { body: string; tag: string }): NotificationLike;
}

// At most this many notifications open at once; the rest wait until one
// closes.  While the browser's question is unanswered, the newest 50 wait.
// After a core outage (hundreds down) the screen never floods.
const MAX_OPEN = 5;
const MAX_QUEUED = 50;

export class WallNotifier {
  private open = new Set<NotificationLike>();
  private queue: WallAlarm[] = [];
  constructor(private api: () => NotificationApi | undefined) {}

  add(events: WallAlarm[]) {
    this.queue.push(...events);
    this.show();
  }

  clear() {
    this.queue.length = 0;
  }

  show() {
    const api = this.api();
    const permission = api ? api.permission : "denied";
    if (permission === "default") {
      this.queue.splice(0, Math.max(0, this.queue.length - MAX_QUEUED));
      return;
    }
    if (permission !== "granted" || !api) {
      this.queue.length = 0; // never shown here: not kept
      return;
    }
    while (this.open.size < MAX_OPEN && this.queue.length) {
      const event = this.queue.shift()!;
      try {
        const note = new api("Nautobot Maps", { body: alarmText(event), tag: `${event.id}-${event.kind}` });
        this.open.add(note);
        note.addEventListener("close", () => {
          this.open.delete(note);
          this.show();
        });
      } catch {
        // Notifications unavailable here: the chime still played.
      }
    }
  }
}
