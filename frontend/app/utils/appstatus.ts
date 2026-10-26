// The top bar's "Updated 14:05" (#419; static/js/lib/nav.js): from
// /api/app-status, once a minute; the worst source speaks.
import type { components } from "~/api/schema";
import { formatFeedTime } from "./format";

export type AppStatus = components["schemas"]["AppStatus"];
export type SyncState = AppStatus["state"];

// Once a minute, from what the scheduler records: nothing upstream (#419).
export const APP_STATUS_REFRESH_MS = 60 * 1000;

export interface AppStatusView {
  state: SyncState;
  text: string;
  intro: string;
  lines: { state: SyncState; what: string; when: string }[];
}

// *status* null: the app didn't answer.
export function appStatusView(status: AppStatus | null, now: Date = new Date()): AppStatusView {
  if (!status) {
    return {
      state: "down",
      text: "Can't reach Nautobot Maps",
      intro: "This page can't reach the app: what you see may be out of date. It tries again every minute.",
      lines: [],
    };
  }
  const sources = status.sources || [];
  const worst = sources.find((s) => s.state === "down") || sources.find((s) => s.state === "stale");
  const updated = status.updated_at ? formatFeedTime(status.updated_at, now) : "";
  // Several unreachable (wireless controllers, #508): one line says how many; the list names them.
  const unreachable = sources.filter((s) => s.state !== "ok" && s.message.startsWith("Can't reach")).length;
  return {
    state: status.state,
    text: unreachable > 1 ? `Can't reach ${unreachable} sources` : worst ? worst.message : updated ? `Updated ${updated}` : "Up to date",
    intro:
      status.state === "ok"
        ? "The map and board are up to date."
        : "The map and board show the last good data: alarms may be missing or out of date.",
    // "Nautobot · synced 14:05"; a problem in its own words, and when it was last good.
    lines: sources.map((source) => {
      const time = source.last_success ? formatFeedTime(source.last_success, now) : "";
      return source.state === "ok"
        ? { state: source.state, what: source.name, when: time ? `synced ${time}` : "" }
        : { state: source.state, what: source.message, when: time ? `last good ${time}` : "" };
    }),
  };
}

// "/" jumps to the site search: not while typing, or with Ctrl, Cmd or Alt.
export function isSearchShortcut(event: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "target">): boolean {
  if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return false;
  const target = event.target as HTMLElement | null;
  return !(target?.closest?.("input, textarea, select, [contenteditable]") || target?.isContentEditable);
}
