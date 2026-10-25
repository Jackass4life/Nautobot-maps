// Availability (#342; on Nuxt since #463): the tables sorted, searched,
// filtered by tenant, the worst N first.
import { describe, expect, it } from "vitest";
import {
  barPercent,
  devicesText,
  filterRows,
  formatDowntime,
  moreText,
  siteRows,
  sortRows,
  tenantRows,
  type SiteRow,
  type SortDir,
  type SortKey,
} from "~/utils/availability";

const site = (name: string, availability: number, downtime: number, incidents: number, tenants: string[]): SiteRow => ({
  site_id: name.toLowerCase(),
  name,
  tenants,
  path: "",
  address: "",
  country: "",
  tier: "",
  monitored_devices: 1,
  // Oslo is the biggest: 12 network devices and 40 access points (#499).
  network_devices: name === "Oslo" ? 12 : 1,
  access_points: name === "Oslo" ? 40 : 0,
  downtime_seconds: downtime,
  incidents,
  outages: incidents,
  longest_outage_seconds: 0,
  mean_outage_seconds: null,
  first_outage: null,
  last_outage: null,
  maintenance_seconds: 0,
  availability,
});
const rows = siteRows([
  site("Oslo", 99.5, 432, 3, ["Acme", "Nordic Net"]),
  site("Bergen", 100, 0, 0, ["Acme"]),
  site("Aarhus", 97.25, 2000, 9, ["Nordic Net"]),
  site("Bodø", 100, 0, 0, []),
], { Acme: "Retail bank" });
const view = (options: { key?: SortKey; dir?: SortDir; query?: string; tenants?: string[]; limit?: number } = {}) =>
  filterRows(sortRows(rows, options.key, options.dir), options);
const names = (v: ReturnType<typeof view>) => v.shown.map((row) => row.name).join();

describe("the availability tables", () => {
  it("how much is at a site, not what (#499)", () => {
    expect(devicesText(12, 40)).toBe("12 network · 40 APs");
    expect(devicesText(1200, 1)).toBe("1,200 network · 1 AP");
    expect(devicesText(0, 3)).toBe("3 APs");
    expect(devicesText(5, 0)).toBe("5 network");
    expect(devicesText(0, 0)).toBe("—");
    expect(rows.find((row) => row.name === "Oslo")!.devicesText).toBe("12 network · 40 APs");
    expect(names(view({ key: "devices", dir: "desc" }))).toBe("Oslo,Aarhus,Bergen,Bodø");
  });

  it("the worst first, ties by name", () => {
    expect(names(view())).toBe("Aarhus,Oslo,Bergen,Bodø");
    expect(names(view({ key: "downtime", dir: "desc" }))).toBe("Aarhus,Oslo,Bergen,Bodø");
    expect(names(view({ key: "name" }))).toBe("Aarhus,Bergen,Bodø,Oslo");
    expect(names(view({ key: "incidents", dir: "asc" }))).toBe("Bergen,Bodø,Oslo,Aarhus");
    const ties = siteRows(["bodø", "Bergen", "Zürich", "aarhus"].map((name) => site(name, 100, 0, 0, [])), {});
    expect(filterRows(sortRows(ties)).shown.map((r) => r.name).join()).toBe("aarhus,Bergen,bodø,Zürich");
  });

  it("search the site or a tenant, every word; any chosen tenant, exactly", () => {
    expect(names(view({ query: "nordic" }))).toBe("Aarhus,Oslo");
    expect(names(view({ query: "OS acme" }))).toBe("Oslo");
    expect(names(view({ tenants: ["Acme"] }))).toBe("Oslo,Bergen");
    expect(view({ tenants: ["Acm"] }).matched).toBe(0);
  });

  it("the worst N, the rest counted", () => {
    const two = view({ limit: 2 });
    expect(names(two)).toBe("Aarhus,Oslo");
    expect(moreText(two, 4)).toBe("Showing 2 of 4 · all of the rest with no downtime");
    expect(moreText(view({ limit: 1 }), 4)).toBe("Showing 1 of 4 · 2 of the rest with no downtime");
    expect(moreText(view(), 4)).toBe("");
    expect(moreText(view({ query: "nordic" }), 4)).toBe("2 of 4");
    expect(moreText(view({ query: "zzz" }), 4)).toBe("Nothing matches.");
  });

  it("a site is found by its tenants' descriptions too (#481)", () => {
    expect(names(view({ query: "retail BANK" }))).toBe("Oslo,Bergen");
  });

  it("1,200 sites, several tenants", () => {
    const many = siteRows(
      Array.from({ length: 1200 }, (_, i) => site(`Site ${i}`, i < 60 ? 99 : 100, i < 60 ? 600 : 0, 1, [`Tenant ${i % 6}`, "Shared"])),
      {},
    );
    const limited = filterRows(sortRows(many), { limit: 25 });
    expect(moreText(limited, 1200)).toBe("Showing 25 of 1,200 · 1,140 of the rest with no downtime");
    const two = filterRows(sortRows(many), { tenants: ["Tenant 1", "Tenant 3"], limit: 25 });
    expect([two.matched, two.shown.length, two.rest.length]).toEqual([400, 25, 375]);
    expect(filterRows(many, { tenants: ["Tenant 1", "Shared"] }).matched).toBe(1200);
    expect(filterRows(many, { tenants: [] }).matched).toBe(1200);
  });

  it("tenants' rows", () => {
    const t = tenantRows([{ tenant: "Acme", sites: 3, availability: 99.9, downtime_seconds: 60 }], {});
    expect(t[0]).toMatchObject({ name: "Acme", sites: 3, downtime: 60 });
  });

  it("a tenant is found by its name or its description (#481)", () => {
    const many = tenantRows(
      Array.from({ length: 300 }, (_, i) => ({ tenant: `Tenant ${i}`, sites: 4, availability: 99 + (i % 100) / 100, downtime_seconds: i })),
      { "Tenant 7": "Retail bank, Nordic offices" },
    );
    const found = (query: string) => filterRows(sortRows(many), { query, limit: 10 });
    expect(found("tenant 299").shown.map((r) => r.name)).toEqual(["Tenant 299"]);
    expect(found("NORDIC bank").shown.map((r) => r.name)).toEqual(["Tenant 7"]);
    expect(found("tenant").matched).toBe(300);
    expect(found("tenant").shown).toHaveLength(10);
    expect(found("nobody").matched).toBe(0);
  });
});

describe("figures", () => {
  it("downtime as the board shows it", () => {
    expect(formatDowntime(0)).toBe("—");
    expect(formatDowntime(14 * 60)).toBe("14m");
    expect(formatDowntime(2 * 3600 + 14 * 60)).toBe("2h 14m");
    expect(formatDowntime(3 * 86400)).toBe("3 days");
  });

  it("the bar from 98 to 100 %, a stub below", () => {
    expect(barPercent(100)).toBe(100);
    expect(barPercent(99)).toBe(50);
    expect(barPercent(50)).toBe(2);
  });
});
