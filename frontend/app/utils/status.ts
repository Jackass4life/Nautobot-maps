// A tenant's status page (#291, on Nuxt since #463): what it says, from
// GET /api/status/{tenant}.  The key from a share link's "#key=" goes in the
// Authorization header; the fragment is never sent to the server.
import type { components } from "~/api/schema";
import { formatFeedTime } from "./format";

export type TenantStatus = components["schemas"]["TenantStatus"];
export type StatusSite = components["schemas"]["StatusSite"];
export type SiteState = StatusSite["state"];

export const STATE_LABELS: Record<SiteState, string> = {
  operational: "Operational",
  degraded: "Degraded",
  outage: "Outage",
  maintenance: "Planned maintenance",
  unknown: "No data",
};

export function headline(data: Pick<TenantStatus, "summary">): string {
  const { sites, affected } = data.summary;
  if (!affected) return sites === 1 ? "Operational" : `All ${sites} sites operational`;
  return `${affected} of ${sites} site${sites === 1 ? "" : "s"} affected`;
}

export function stateLabel(state: string): string {
  return STATE_LABELS[state as SiteState] ?? STATE_LABELS.unknown;
}

// "since 14:05" today, else "since 3 Oct 14:05"; "until ..." for maintenance.
export function siteWhen(site: Pick<StatusSite, "since" | "until">, now: Date): string {
  if (site.since) return `since ${formatFeedTime(site.since, now)}`;
  if (site.until) return `until ${formatFeedTime(site.until, now)}`;
  return "";
}

// Up to this many sites, every one is listed open; above, the operational
// ones fold into one line, so a large tenant sees what is wrong first (#407).
export const FOLD_ABOVE = 10;

// The sites to list first (worst first, as the API sorts them) and those
// folded into "N sites operational" (none for a small tenant).
export function splitSites<T extends { state: string }>(sites: T[]): { attention: T[]; operational: T[] } {
  if (sites.length <= FOLD_ABOVE) return { attention: sites, operational: [] };
  return {
    attention: sites.filter((site) => site.state !== "operational"),
    operational: sites.filter((site) => site.state === "operational"),
  };
}

export function statusKey(hash: string | null | undefined): string {
  return new URLSearchParams(String(hash || "").replace(/^#/, "")).get("key") || "";
}

export function errorText(code: number): string {
  if (code === 401 || code === 403) return "This link doesn't work (anymore). Ask your contact for a new one.";
  if (code === 404) return "No sites found.";
  return "Status is unavailable right now. Retrying…";
}
