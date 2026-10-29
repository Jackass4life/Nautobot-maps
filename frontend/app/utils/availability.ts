// Availability (#287, #341, #342; static/js/availability/table.js): the
// tables sorted by a column, searched, filtered by tenant, the worst N first
// with "Show more", so 1,200 sites stay one screen.
import type { components } from "~/api/schema";
import { hasAllWords, searchWords } from "./format";

export type Report = components["schemas"]["Report"];
export type SiteRow = components["schemas"]["SiteRow"];
export type TenantRow = components["schemas"]["TenantRow"];
export type SortKey = "name" | "availability" | "downtime" | "incidents" | "sites" | "devices";
export type SortDir = "asc" | "desc";

// Which way a column sorts first: the worst first.
export const FIRST_DIRECTION: Record<SortKey, SortDir> = {
  name: "asc",
  availability: "asc",
  downtime: "desc",
  incidents: "desc",
  sites: "desc",
  devices: "desc",
};

// A row of either table, as the tables sort and search it.
export interface Row {
  key: string;
  name: string;
  lname: string;
  availability: number;
  downtime: number;
  incidents: number;
  sites: number;
  // A site's devices (#499): all of them (the sort), and "12 network · 40 APs".
  devices: number;
  devicesText: string;
  tenants: string[];
  search: string;
}

// A site is found by its name, its tenants and their descriptions (#481).
export function siteRows(sites: SiteRow[], descriptions: Record<string, string>): Row[] {
  return sites.map((site) => {
    const name = site.name || site.site_id;
    return {
      key: site.site_id,
      name,
      lname: name.toLowerCase(),
      availability: site.availability,
      downtime: site.downtime_seconds,
      incidents: site.incidents,
      sites: 0,
      devices: site.network_devices + site.access_points,
      devicesText: devicesText(site.network_devices, site.access_points),
      tenants: site.tenants,
      search: [name, ...site.tenants, ...site.tenants.map((tenant) => descriptions[tenant] ?? "")].join(" ").toLowerCase(),
    };
  });
}

// "12 network · 40 APs", "1 AP", "—": how much is at a site, not what (#499).
export function devicesText(network: number, accessPoints: number): string {
  const parts = [
    network ? `${network.toLocaleString("en")} network` : "",
    accessPoints ? plural(accessPoints, "AP") : "",
  ];
  return parts.filter(Boolean).join(" · ") || "—";
}

// A tenant is found by its name or its description (#481).
export function tenantRows(tenants: TenantRow[], descriptions: Record<string, string>): Row[] {
  return tenants.map((tenant) => ({
    key: tenant.tenant,
    name: tenant.tenant,
    lname: tenant.tenant.toLowerCase(),
    availability: tenant.availability,
    downtime: tenant.downtime_seconds,
    incidents: 0,
    sites: tenant.sites,
    devices: 0,
    devicesText: "",
    tenants: [],
    search: [tenant.tenant, descriptions[tenant.tenant] ?? ""].join(" ").toLowerCase(),
  }));
}

function byName(a: Row, b: Row): number {
  return a.lname < b.lname ? -1 : a.lname > b.lname ? 1 : 0;
}

// A sorted copy by *key* and *dir*; ties by name.
export function sortRows(rows: Row[], key: SortKey = "availability", dir: SortDir = "asc"): Row[] {
  const sign = dir === "desc" ? -1 : 1;
  return [...rows].sort((a, b) => (key === "name" ? byName(a, b) : a[key] - b[key]) * sign || byName(a, b));
}

// The sorted rows a search and the chosen tenants leave: {shown, rest, matched}.
export function filterRows(
  sorted: Row[],
  { query = "", tenants = [], limit = Infinity }: { query?: string; tenants?: string[]; limit?: number } = {},
): { shown: Row[]; rest: Row[]; matched: number } {
  const words = searchWords(query);
  const matched = sorted.filter(
    (row) => hasAllWords(row.search, words) && (!tenants.length || row.tenants.some((tenant) => tenants.includes(tenant))),
  );
  return { shown: matched.slice(0, limit), rest: matched.slice(limit), matched: matched.length };
}

// The line under a table: "Showing 25 of 1,200 · 1,140 of the rest with no downtime".
export function moreText(view: { shown: Row[]; rest: Row[]; matched: number }, total: number): string {
  const count = (n: number) => n.toLocaleString("en-GB");
  if (!view.matched) return total ? "Nothing matches." : "";
  if (!view.rest.length) return view.matched < total ? `${count(view.matched)} of ${count(total)}` : "";
  const quiet = view.rest.filter((row) => !row.downtime).length;
  const tail =
    quiet === view.rest.length ? " · all of the rest with no downtime" : quiet ? ` · ${count(quiet)} of the rest with no downtime` : "";
  return `Showing ${count(view.shown.length)} of ${count(view.matched)}${tail}`;
}

// As the board shows durations (availability.format_downtime): "—", "14m",
// "2h 14m", and in days from two days ("3 days").
export function formatDowntime(seconds: number): string {
  const total = Math.trunc(seconds || 0);
  if (total <= 0) return "—";
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours >= 48) return `${Math.floor(hours / 24)} days`;
  return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
}

// The bar runs from 98 to 100 % (most targets are 99–100 %); below 98 % a short stub.
// The bar runs from 98 % to 100 %, or from 1 point below a lower target
// (AVAILABILITY_TARGET_PCT, #525), so sites near the target aren't stubs.
export function barPercent(availability: number, target = 99.9): number {
  const from = Math.min(98, target - 1);
  return Math.max(2, Math.min(100, ((availability - from) / (100 - from)) * 100));
}

export function plural(count: number, word: string): string {
  return `${count.toLocaleString("en")} ${word}${count === 1 ? "" : "s"}`;
}
