// The Maintenance page (#343, #353; on Nuxt since #463): batches, groups,
// the site picker and the request.
import { describe, expect, it } from "vitest";
import { searchWords } from "~/utils/format";
import {
  batchesOf,
  batchMatches,
  batchTitle,
  countText,
  endLabel,
  groupsFor,
  matchSites,
  MAX_MATCHES,
  prepareSites,
  spanText,
  whenText,
  windowRequest,
  type MaintenanceWindow,
  type PlanValues,
} from "~/utils/maintenance";

const w = (id: number, site: string, state: MaintenanceWindow["state"], extra: Partial<MaintenanceWindow> = {}): MaintenanceWindow => ({
  id,
  site_id: site,
  site_name: site.toUpperCase(),
  site_path: "EMEA",
  device_id: "",
  device_name: "",
  tenants: ["Acme"],
  state,
  reason: "Core upgrade",
  created_by: "olga",
  batch_id: "b30",
  created_at: "2026-10-14T10:00:00Z",
  starts_at: "2026-10-14T10:00:00Z",
  ends_at: "2026-10-14T16:00:00Z",
  ended_at: null,
  ended_by: "",
  ...extra,
});
// One change across 30 sites, one device window, one planned, one ended.
const batch30 = Array.from({ length: 30 }, (_, i) => w(i + 1, `s${String(i).padStart(2, "0")}`, "active"));
const device = w(40, "oslo", "active", { batch_id: "bdev", device_id: "d1", device_name: "core1", reason: "PSU", ends_at: "2026-10-14T12:00:00Z", tenants: ["Nordic"] });
const planned = w(50, "bergen", "upcoming", { batch_id: "bpl", reason: "Fibre", starts_at: "2026-10-15T20:00:00Z", ends_at: "2026-10-16T02:00:00Z", created_by: "ivan" });
const ended = w(60, "aarhus", "ended", { batch_id: "bend", ended_at: "2026-10-12T12:00:00Z" });
const windows = [...batch30, device, planned, ended];
const now = new Date("2026-10-14T13:00:00Z");

describe("batches and groups", () => {
  const batches = batchesOf(windows);

  it("one batch per request; active ending soonest first, then planned, then ended", () => {
    expect(batches).toHaveLength(4);
    expect(batches.map((b) => b.group)).toEqual(["active", "active", "upcoming", "past"]);
    expect(batchTitle(batches[0]!)).toBe("OSLO · core1");
    expect(batchTitle(batches[1]!)).toBe("30 sites");
    expect(whenText(batches[1]!, now)).toMatch(/^until /);
    expect(whenText(batches[3]!, now)).toMatch(/^ended /);
    expect(whenText({ ...batches[3]!, state: "cancelled" }, now)).toBe("cancelled");
  });

  it("groups first, whatever the input order", () => {
    expect(batchesOf([ended, planned, ...batch30, device]).map((b) => b.group)).toEqual(["active", "active", "upcoming", "past"]);
    expect(groupsFor(14)[2]!.title).toBe("Ended in the last 14 days");
  });

  it("the stored batch, not a guess", () => {
    expect(batchesOf([w(80, "x", "active", { batch_id: "one" }), w(81, "y", "active", { batch_id: "two" })])).toHaveLength(2);
    expect(batchesOf([w(90, "x", "active", { batch_id: "" }), w(91, "y", "active", { batch_id: "" })])).toHaveLength(2);
  });

  it("partly cancelled, partly ended: two lines, the latest end", () => {
    const split = batchesOf([
      w(70, "a", "cancelled", { batch_id: "bs", ended_at: "2026-10-12T08:00:00Z", starts_at: "2026-10-12T10:00:00Z" }),
      w(71, "b", "ended", { batch_id: "bs", ended_at: "2026-10-12T11:00:00Z", starts_at: "2026-10-12T10:00:00Z" }),
      w(72, "c", "ended", { batch_id: "bs", ended_at: "2026-10-12T12:30:00Z", starts_at: "2026-10-12T10:00:00Z" }),
    ]);
    expect(split.map((b) => b.state).sort()).toEqual(["cancelled", "ended"]);
    expect(split.find((b) => b.state === "ended")!.ended_at).toBe("2026-10-12T12:30:00Z");
  });

  it("search every word; any chosen tenant", () => {
    const q = (query: string) => ({ words: searchWords(query) });
    expect(batchMatches(batches[0]!, q("psu core1"))).toBe(true);
    expect(batchMatches(batches[1]!, q("psu"))).toBe(false);
    expect(batchMatches(batches[2]!, q("IVAN"))).toBe(true);
    expect(batchMatches(batches[1]!, q("s17"))).toBe(true);
    expect(batchMatches(batches[0]!, { tenants: ["Nordic"] })).toBe(true);
    expect(batchMatches(batches[1]!, { tenants: ["Nordic"] })).toBe(false);
    expect(batchMatches(batches[1]!, { tenants: ["Nordic", "Acme"] })).toBe(true);
  });

  it("End or Cancel, one or all", () => {
    expect(endLabel(batches[1]!, 30)).toBe("End all 30");
    expect(endLabel(batches[0]!, 1)).toBe("End now");
    expect(endLabel(batches[2]!, 1)).toBe("Cancel");
    expect(endLabel(batches[2]!, 3)).toBe("Cancel all 3");
    expect(countText(batches.filter((b) => b.group === "active"))).toBe("2 · 31 sites");
    expect(countText([])).toBe("");
  });

  it("the history says when it ran", () => {
    const early = w(99, "x", "ended", { batch_id: "e", starts_at: "2026-10-14T10:00:00Z", ends_at: "2026-10-14T12:00:00Z", ended_at: "2026-10-14T11:00:00Z" });
    expect(spanText(batchesOf([early])[0]!, now)).toMatch(/ – .* · ended early$/);
    expect(spanText(batches[0]!, now)).toMatch(/^since /);
    expect(spanText(batches[2]!, now)).toMatch(/^planned /);
    expect(spanText({ ...batches[3]!, state: "cancelled" }, now)).toMatch(/^cancelled · was planned /);
  });
});

describe("planning", () => {
  // 1,200 sites; Acme has 40 of them.
  const sites = prepareSites(
    Array.from({ length: 1200 }, (_, i) => ({
      id: `s${i}`,
      name: `Site ${String(i).padStart(4, "0")} ${["Oslo", "Bergen", "Aarhus"][i % 3]}`,
      path: "EMEA › NOR",
      tenants: i % 30 === 0 ? ["Acme Corp"] : [`Tenant ${i % 7}`],
      devices: 8,
      maintenance: i === 3,
    })).reverse(),
  );

  it("sorted by name once, numbers as numbers", () => {
    expect(sites[0]!.name).toBe("Site 0000 Oslo");
    expect(sites[1199]!.name).toBe("Site 1199 Aarhus");
  });

  it("eight at most, a tenant's sites at once, the chosen not again", () => {
    const oslo = matchSites(sites, "oslo");
    expect(oslo.sites).toHaveLength(MAX_MATCHES);
    expect(oslo.more).toBe(400 - MAX_MATCHES);
    const acme = matchSites(sites, "acme");
    expect(acme.tenants).toEqual([{ name: "Acme Corp", ids: expect.any(Array) }]);
    expect(acme.tenants[0]!.ids).toHaveLength(40);
    expect(matchSites(sites, "site 0003").sites.map((s) => s.id)).toEqual(["s3"]);
    expect(matchSites(sites, "site 0003", new Set(["s3"])).sites).toEqual([]);
    expect(matchSites(sites, "  ").sites).toEqual([]);
  });

  const values = (extra: Partial<PlanValues>): PlanValues => ({
    siteIds: ["s1"],
    scope: "site",
    deviceIds: [],
    reason: "x",
    when: "now",
    duration: 120,
    startDay: "",
    startTime: "",
    endDay: "",
    endTime: "",
    ...extra,
  });

  it("the request: many sites at once, devices for one site", () => {
    const ids = sites.slice(0, 40).map((s) => s.id);
    expect(windowRequest(values({ siteIds: ids, reason: " Core " }))).toEqual({ site_ids: ids, reason: "Core", duration_minutes: 120 });
    expect(windowRequest(values({}))).toEqual({ site_id: "s1", reason: "x", duration_minutes: 120 });
    expect(windowRequest(values({ scope: "devices", deviceIds: ["d1"] }))).toMatchObject({ site_id: "s1", device_ids: ["d1"] });
  });

  it("says what is missing", () => {
    expect(windowRequest(values({ siteIds: [] })).error).toBe("Choose at least one site.");
    expect(windowRequest(values({ reason: " " })).error).toBe("Enter a reason.");
    expect(windowRequest(values({ siteIds: ["a", "b"], scope: "devices", deviceIds: ["d"] })).error).toContain("one site");
    expect(windowRequest(values({ scope: "devices" })).error).toContain("Tick at least one device");
    expect(windowRequest(values({ siteIds: sites.slice(0, 201).map((s) => s.id) })).error).toContain("200");
    expect(windowRequest(values({ siteIds: ["a", "b", "c"] }), 2).error).toBe("At most 2 sites at once.");
  });
});
