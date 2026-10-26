// A tenant's status page (#291, on Nuxt since #463): what it says, from
// GET /api/status/{id}, the tenant's random link id (#500).  The key from a share link's "#key=" goes in the
// Authorization header; the fragment is never sent to the server.
import type { components } from "~/api/schema";
import { plural } from "./availability";
import { formatFeedTime } from "./format";

export type TenantStatus = components["schemas"]["TenantStatus"];
export type StatusSite = components["schemas"]["StatusSite"];
export type SiteState = StatusSite["state"];
export type StatusCircuit = components["schemas"]["StatusCircuit"];

export const STATE_LABELS: Record<SiteState, string> = {
  operational: "Operational",
  degraded: "Degraded",
  outage: "Outage",
  maintenance: "Planned maintenance",
  unknown: "No data",
};

export function headline(data: Pick<TenantStatus, "summary">): string {
  const { sites, affected, devices_down: down } = data.summary;
  const text = affected
    ? `${affected} of ${sites} site${sites === 1 ? "" : "s"} affected`
    : sites === 1
      ? "Operational"
      : `All ${sites} sites operational`;
  // How many devices are down, of every kind (#503); also access points a
  // wireless controller reports down at an operational site (#508).
  return down ? `${text} · ${down.toLocaleString("en")} device${down === 1 ? "" : "s"} down` : text;
}

type SiteDevices = Pick<StatusSite, "state" | "network_devices" | "access_points" | "network_devices_down" | "access_points_down">;

// "12 network devices", or "3 of 12 network devices down".
function kindText(total: number, down: number, word: string): string {
  if (!total) return "";
  return down ? `${down.toLocaleString("en")} of ${plural(total, word)} down` : plural(total, word);
}

// Whether a site's line says what is down: something is, and the site isn't
// in maintenance or without data.  An operational one too: access points a
// wireless controller reports down never raise its state (#508).
export function hasDown(site: SiteDevices): boolean {
  return site.state !== "maintenance" && site.state !== "unknown" && site.network_devices_down + site.access_points_down > 0;
}

// What is at a site, counts only (#503): with something down, how many of
// each kind, "3 of 12 network devices down · 1 of 40 access points down";
// else just how many, "12 network devices · 40 access points": full words for
// the customer (#515; Availability's table says "12 network · 40 APs", #499).
// Empty when nothing is counted.
export function siteDevices(site: SiteDevices): string {
  if (!site.network_devices && !site.access_points) return "";
  // Nothing "down" in maintenance or without data: the quiet line.
  const [networkDown, apsDown] = hasDown(site) ? [site.network_devices_down, site.access_points_down] : [0, 0];
  return [kindText(site.network_devices, networkDown, "network device"), kindText(site.access_points, apsDown, "access point")]
    .filter(Boolean)
    .join(" · ");
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
  // An old link with the name in it (#500) answers 404 too.
  if (code === 404) return "No sites found for this link. If it is an old link, ask your contact for a new one.";
  return "Status is unavailable right now. Retrying…";
}

// Where a site is (#493): its address, else its coordinates, as a link to
// OpenStreetMap; null when Nautobot has neither.  The link is a pin on the
// coordinates when there are any (exact), else a search for the address.
export function sitePlace(site: Pick<StatusSite, "address" | "latitude" | "longitude">): { text: string; href: string } | null {
  const located = site.latitude != null && site.longitude != null;
  if (!site.address && !located) return null;
  let href = `https://www.openstreetmap.org/search?query=${encodeURIComponent(site.address ?? "")}`;
  let text = site.address ?? "";
  if (located) {
    const [lat, lon] = [site.latitude!.toFixed(5), site.longitude!.toFixed(5)];
    href = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=16/${lat}/${lon}`;
    text ||= `${site.latitude!.toFixed(4)}, ${site.longitude!.toFixed(4)}`;
  }
  return { text, href };
}

// A site's circuits (#511), folded: "1 circuit", "3 circuits".
export function circuitsLabel(count: number): string {
  return `${count.toLocaleString("en")} circuit${count === 1 ? "" : "s"}`;
}

// One circuit: "DK-TELIA-123456 · Internet Transit · 1 Gbps", without what it lacks.
export function circuitText(circuit: StatusCircuit): string {
  return [circuit.cid, circuit.type, circuit.bandwidth].filter(Boolean).join(" · ");
}
