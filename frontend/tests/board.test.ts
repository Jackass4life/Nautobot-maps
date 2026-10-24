// The alert board (#311; on Nuxt since #463): which sites it lists and in
// which order, the summary, the rows' words, cases, acknowledgements, the
// feed, Copy and the wall link.  The page itself is tested end to end (e2e/).
import { describe, expect, it } from "vitest";
import {
  ROWS_STEP,
  ackRequestBody,
  boardChoices,
  boardStatusText,
  boardSummaryText,
  caseButtonLabel,
  caseTargets,
  caseUrl,
  chipCounts,
  compareSites,
  deviceDowntimeSeconds,
  deviceMeta,
  emptyBoardText,
  feedEntry,
  feedStartsCollapsed,
  filterSites,
  filtersInUse,
  filtersLeftOffTheWall,
  formatCountdown,
  formatDuration,
  hasCase,
  historyExportUrl,
  levelLabel,
  moreFiltersInUse,
  noBoardCriteria,
  scopeSummary,
  siteAlarmSeconds,
  siteCaseState,
  sinceLabel,
  siteReport,
  stackText,
  wallLinkFor,
  type Board,
  type BoardSite,
  type DownDevice,
  type FeedEvent,
} from "~/utils/board";
import { readScope } from "~/utils/scope";

const device = (id: string, fields: Partial<DownDevice> = {}): DownDevice =>
  ({ device_id: id, device_name: id, device_ip: "", status: "Active", role: "", case_numbers: [], ...fields }) as DownDevice;

const site = (id: string, fields: Partial<BoardSite> = {}): BoardSite =>
  ({
    id,
    name: id,
    status: "Active",
    location_type: "Site",
    ancestor_path: "",
    parent: "",
    physical_address: "",
    facility: "",
    country: "",
    tenant: "",
    tenants: [],
    tenant_descriptions: {},
    alert_level: "ok",
    alert_reason: "",
    tier: "",
    tier_rank: 99,
    device_count: 4,
    maintenance_device_count: 0,
    down_device_count: 0,
    current_downtime_seconds: 0,
    latest_down_at: null,
    active_cases: [],
    down_devices: [],
    latitude: 1,
    longitude: 1,
    ...fields,
  }) as BoardSite;

const payload = (fields: Partial<Board> = {}): Board =>
  ({
    checked_at: "2026-10-24T12:05:00Z",
    stale: false,
    sync_pending: false,
    persistence_configured: true,
    alerts: [],
    summary: { total: 0, critical: 0, medium: 0, low: 0, no_data: 0, ok: 0, maintenance: 0, non_ok: 0 },
    next_update_in_seconds: 60,
    ...fields,
  }) as Board;

describe("the summary", () => {
  it("says only what is wrong", () => {
    expect(boardSummaryText({ critical: 1, medium: 0, low: 2, non_ok: 3 })).toBe("3 alarms · 1 critical · 2 low");
    expect(boardSummaryText({ low: 1 })).toBe("1 alarm · 1 low");
    expect(boardSummaryText({ total: 1200, ok: 1200 })).toBe("No alarms");
  });

  it("the chips count alarms, all, No data and maintenance; a link's sites only", () => {
    expect(chipCounts({ total: 9, critical: 2, low: 1, no_data: 3, maintenance: 1 })).toEqual({
      alarms: 3,
      total: 9,
      no_data: 3,
      maintenance: 1,
    });
    const scoped = scopeSummary([site("a", { alert_level: "critical" }), site("b"), site("c", { alert_level: "critical" })]);
    expect(scoped).toEqual({ total: 3, critical: 2, ok: 1 });
    expect(chipCounts(scoped).alarms).toBe(2);
  });

  it("the status line: how many, when, stale and sync only when they are", () => {
    // The time in the browser's own format.
    expect(boardStatusText(payload(), 12, true)).toMatch(/^12 sites with alarms · updated 12:05( PM)?$/);
    expect(boardStatusText(payload({ stale: true, sync_pending: true }), 1, false)).toMatch(
      /^1 site · updated 12:05( PM)? · data may be out of date · inventory sync in progress…$/,
    );
    expect(boardStatusText(payload({ checked_at: "" }), 0, false)).toBe("0 sites · updated unknown");
  });

  it("the countdown", () => {
    expect(formatCountdown(222)).toBe("3:42");
    expect(formatCountdown(3725)).toBe("1:02:05");
    expect(formatCountdown(-4)).toBe("0:00");
  });
});

describe("which sites, in which order", () => {
  // 1,200 sites, 25 in alarm, 3 in maintenance: a normal bad day.
  const sites = Array.from({ length: 1200 }, (_, i) =>
    site(`s${i}`, {
      name: `Site ${String(i).padStart(4, "0")}`,
      tenants: [i % 3 ? "Acme Corp" : "Nordic Net"],
      status: i % 50 ? "Active" : "Planned",
      alert_level: i < 5 ? "critical" : i < 25 ? "low" : i < 28 ? "maintenance" : i < 40 ? "no_data" : "ok",
      latest_down_at: i < 25 ? new Date(Date.UTC(2026, 9, 24, 10, i)).toISOString() : null,
      down_device_count: i < 25 ? 1 : 0,
    }),
  );

  it("opens on the alarms, newest down first", () => {
    const shown = filterSites(sites, noBoardCriteria());
    expect(shown).toHaveLength(25);
    expect(shown[0]!.id).toBe("s24");
    expect(shown.at(-1)!.id).toBe("s0");
  });

  it("the chips: All sites, No data, Maintenance", () => {
    expect(filterSites(sites, { ...noBoardCriteria(), severity: "all" })).toHaveLength(1200);
    expect(filterSites(sites, { ...noBoardCriteria(), severity: "no_data" })).toHaveLength(12);
    expect(filterSites(sites, { ...noBoardCriteria(), severity: "maintenance" })).toHaveLength(3);
    // "Show more" draws the rest in steps.
    expect(ROWS_STEP).toBeLessThan(1200);
  });

  it("severity, then tier, then most down, then name", () => {
    const a = site("a", { alert_level: "low", tier_rank: 99, down_device_count: 1 });
    const b = site("b", { alert_level: "low", tier_rank: 0, down_device_count: 1 });
    const c = site("c", { alert_level: "critical" });
    const d = site("d", { alert_level: "low", tier_rank: 99, down_device_count: 5 });
    expect([a, b, c, d].sort((x, y) => compareSites(x, y, "severity")).map((s) => s.id)).toEqual(["c", "b", "d", "a"]);
    expect([d, c, b, a].sort((x, y) => compareSites(x, y, "site")).map((s) => s.id)).toEqual(["a", "b", "c", "d"]);
  });

  it("the filter box, tenants, status and type", () => {
    const all = { ...noBoardCriteria(), severity: "all" as const };
    expect(filterSites(sites, { ...all, site: "site 0007" }).map((s) => s.id)).toEqual(["s7"]);
    expect(filterSites(sites, { ...all, tenant: ["Nordic Net"] })).toHaveLength(400);
    expect(filterSites(sites, { ...all, tenant: ["Nordic Net", "Acme Corp"] })).toHaveLength(1200);
    expect(filterSites(sites, { ...all, status: ["Planned"] })).toHaveLength(24);
    expect(boardChoices(sites)).toEqual({ status: ["Active", "Planned"], type: ["Site"], tenant: ["Acme Corp", "Nordic Net"] });
  });

  it("More filters and Reset say when they are in use", () => {
    const none = noBoardCriteria();
    expect(filtersInUse(none)).toBe(false);
    expect(moreFiltersInUse({ ...none, status: ["Planned"], nonOperational: true, sort: "site" })).toBe(3);
    expect(filtersInUse({ ...none, site: " x " })).toBe(true);
    expect(filtersInUse({ ...none, severity: "all" })).toBe(true);
    expect(filtersInUse({ ...none, site: "   " })).toBe(false);
  });
});

describe("an empty board", () => {
  const counts = { inScope: 3, severity: "alarms" as const, scopeText: "" };
  it("says why", () => {
    expect(emptyBoardText(payload({ persistence_configured: false }), counts, false).text).toContain("PostgreSQL");
    expect(emptyBoardText(payload({ sync_pending: true }), { ...counts, inScope: 0 }, false).text).toContain("sync in progress");
    expect(emptyBoardText(payload(), counts, false)).toEqual({
      text: "No site has an active alarm. Choose All sites to see every site.",
      kind: "all-clear",
    });
    expect(emptyBoardText(payload(), { ...counts, severity: "all" }, true).text).toBe("No sites match the current filters.");
  });

  it("a link that matches nothing is never the all-clear (#364)", () => {
    const empty = emptyBoardText(payload({ alerts: [site("a")] }), { ...counts, inScope: 0, scopeText: "nobody" }, false);
    expect(empty).toEqual({ text: "No site matches nobody. Check the link's tenant= and q=.", kind: "no-match" });
  });
});

describe("a row", () => {
  it("levels by name as well as colour", () => {
    expect(levelLabel("no_data")).toBe("NO DATA");
    expect(levelLabel(undefined)).toBe("NO DATA");
    expect(levelLabel("critical")).toBe("CRITICAL");
  });

  it("time in alarm, and never a reassuring <1m when it isn't known", () => {
    expect(formatDuration(283 * 86400)).toBe("283 days");
    expect(formatDuration(3 * 3600 + 40 * 60)).toBe("3h 40m");
    expect(sinceLabel("in alarm", null)).toBe("—");
    expect(sinceLabel("down", 30)).toBe("down <1m");
    expect(sinceLabel("down", NaN)).toBe("down, time unknown");
    const down = site("a", { alert_level: "low", current_downtime_seconds: 600, down_devices: [device("d", { down_started_at: "x" })] });
    expect(siteAlarmSeconds(down)).toBeNaN();
    expect(siteAlarmSeconds({ ...down, down_devices: [device("d", { down_started_at: "2026-10-24T10:00:00Z" })] })).toBe(600);
    expect(siteAlarmSeconds({ ...down, alert_level: "maintenance" })).toBeNull();
    expect(deviceDowntimeSeconds(device("d", { down_started_at: "2026-10-24T10:00:00Z" }), Date.UTC(2026, 9, 24, 10, 25))).toBe(1500);
  });

  it("whether someone is on it: a case or an acknowledgement; maintenance doesn't count", () => {
    const ack = { by: "olga", at: "", note: "" };
    expect(siteCaseState(site("a")).state).toBe("none");
    expect(siteCaseState(site("a", { down_devices: [device("1", { case_numbers: ["INC-1"] }), device("2")] }))).toEqual({
      state: "partly",
      without: 1,
      total: 2,
    });
    expect(siteCaseState(site("a", { down_devices: [device("1", { acknowledged: ack }), device("2", { maintenance_until: "x" })] })).state).toBe(
      "handled",
    );
  });

  it("a device's line: where, role, stack, status, why", () => {
    expect(deviceMeta(device("d", { role: "switch", status: "Active", down_reason: "LibreNMS: ping" }))).toBe("switch · Active · LibreNMS: ping");
    expect(deviceMeta(device("d", { status: "", flapping: true }))).toBe("Down device");
    expect(stackText({ name: "sw1", size: 4, position: 2, master: false, whole: false })).toBe("Stack sw1, member 2 of 4");
    expect(stackText({ name: "sw1", size: 4, position: 1, master: true, whole: false })).toBe("Stack sw1, master of 4");
    const whole = { name: "sw1", size: 2, position: 1, master: true, whole: true, members: ["a", "b"] };
    expect(stackText(whole)).toBe("Stack sw1: all 2 members down");
    expect(stackText(whole, { names: true })).toBe("Stack sw1: all 2 members down (a, b)");
    expect(stackText({ name: "x", size: 1, position: 1, master: true, whole: false })).toBe("");
  });

  it("a case number links to its ticket (#289)", () => {
    expect(caseUrl("INC 1/2", "https://itsm.example/t/{case}")).toBe("https://itsm.example/t/INC%201%2F2");
    expect(caseUrl("INC-1", "")).toBe("");
  });
});

describe("cases and acknowledgements", () => {
  const devices = [device("a", { case_numbers: ["INC-881"] }), device("b"), device("c")];

  it("a number a device already has, in any case, isn't added again (#273)", () => {
    expect(hasCase(devices[0]!, " inc-881 ")).toBe(true);
    expect(hasCase(devices[1]!, "inc-881")).toBe(false);
    expect(caseTargets(devices, new Set(["a", "b"]), "INC-881")).toEqual(["b"]);
    expect(caseTargets(devices, new Set(["a", "b"]), "INC-9")).toEqual(["a", "b"]);
  });

  it("the button says what it will do", () => {
    expect(caseButtonLabel(0)).toBe("Select devices");
    expect(caseButtonLabel(1)).toBe("Add case to 1 device");
    expect(caseButtonLabel(3, 1)).toBe("Add case to 2 devices (already on 1)");
    expect(caseButtonLabel(2, 2)).toBe("Already on 2 devices");
  });

  it("no devices means every down device, sent as null (#288)", () => {
    expect(ackRequestBody("s1", [])).toEqual({ site_id: "s1", device_ids: null, note: "" });
    expect(ackRequestBody("s1", ["d1"], "on my way")).toEqual({ site_id: "s1", device_ids: ["d1"], note: "on my way" });
  });
});

describe("the feed", () => {
  const event = (fields: Partial<FeedEvent>): FeedEvent =>
    ({ kind: "down", at: "2026-10-24T10:05:00Z", site_id: "s", site_name: "Oslo", device_id: "d", device_name: "sw1", level: "low", ...fields }) as FeedEvent;
  const now = new Date("2026-10-24T12:00:00Z");

  it("down, with when it went down if earlier; up; severity", () => {
    // The time in the browser's own format.
    const at = "10:05( AM)?";
    expect(feedEntry(event({ down_since: "2026-10-24T10:05:00Z" }), now)).toMatchObject({ title: "sw1", text: "down" });
    expect(feedEntry(event({ down_since: "2026-10-24T10:05:00Z" }), now).meta).toMatch(new RegExp(`^${at} · Oslo$`));
    expect(feedEntry(event({ down_since: "2026-10-24T09:00:00Z" }), now).meta).toMatch(
      new RegExp(`^${at} · Oslo · down since 0?9:00( AM)?$`),
    );
    expect(feedEntry(event({ kind: "up" }), now)).toMatchObject({ title: "sw1", text: "back up" });
    expect(feedEntry(event({ kind: "severity", device_name: "" }), now)).toMatchObject({ title: "Oslo", text: "" });
    expect(feedEntry(event({ kind: "severity" }), now).meta).toMatch(new RegExp(`^${at}$`));
  });

  it("starts as the operator left it, else open only with room", () => {
    expect(feedStartsCollapsed("1", 2000)).toBe(true);
    expect(feedStartsCollapsed("0", 800)).toBe(false);
    expect(feedStartsCollapsed(null, 1400)).toBe(true);
    expect(feedStartsCollapsed(null, 1800)).toBe(false);
  });
});

describe("Copy, History and the wall link", () => {
  it("a site as text for a ticket: the likely cause first, then newest down (#227, #285)", () => {
    const report = siteReport(
      site("s1", {
        name: "Oslo",
        ancestor_path: "EMEA › NOR",
        tenants: ["Acme Corp", "Nordic Net"],
        alert_level: "critical",
        alert_reason: "core down",
        device_count: 7,
        down_device_count: 2,
        active_cases: ["INC-1"],
        down_devices: [
          device("e", { device_name: "edge", down_started_at: "2026-10-24T10:00:00Z", probable_cause: "core" }),
          device("c", { device_name: "core", core: true, device_ip: "10.0.0.1", down_started_at: "2026-10-24T09:00:00Z" }),
        ],
      }),
      { nautobotUrl: "https://nautobot.example/", caseTemplate: "https://itsm.example/{case}" },
    );
    expect(report).toContain("Site: Oslo\nLocation: EMEA › NOR\nTenants (2): Acme Corp, Nordic Net");
    expect(report).toContain("Severity: CRITICAL (2 of 7 devices down)");
    expect(report).toContain("Cases: INC-1 (https://itsm.example/INC-1)");
    expect(report).toContain("Nautobot: https://nautobot.example/dcim/locations/s1/");
    expect(report).toContain("Likely cause: core down; 1 other device probably behind it");
    const lines = report.split("\n");
    expect(lines.at(-2)).toMatch(/^- core \| IP 10\.0\.0\.1 \| .*Down since 2026-10-24 09:00 \+00:00 .*\| Core device$/);
    expect(lines.at(-1)).toContain("Likely caused by core");
  });

  it("the history's CSV for a period", () => {
    expect(historyExportUrl("s 1", "devices", "7")).toBe("/api/alert-history.csv?site_id=s+1&days=7&view=devices");
  });

  it("the wall for what the board shows (#366)", () => {
    const scope = readScope("?tenant=Acme%20Corp&q=DNK");
    expect(wallLinkFor({ tenant: [], text: "", scope: null })).toBeNull();
    expect(wallLinkFor({ tenant: [], text: " London ", scope: null })).toBe("/alerts?view=wall&q=London");
    expect(wallLinkFor({ tenant: [], text: "", scope })).toBe("/alerts?view=wall&tenant=Acme+Corp&q=DNK");
    // The tenant filter replaces the link's tenants; the words add up.
    expect(wallLinkFor({ tenant: ["Nordic Net"], text: "Oslo", scope })).toBe("/alerts?view=wall&tenant=Nordic+Net&q=DNK&q=Oslo");
    expect(filtersLeftOffTheWall({ status: ["Planned"], type: [] })).toEqual(["Status"]);
  });
});
