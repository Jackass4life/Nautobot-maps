// The map (#334, #392, #420; on Nuxt since #463): its levels, alarm list,
// filters, markers' grouping, first view and folding panel.  The page and
// Leaflet themselves are tested end to end (e2e/).
import { describe, expect, it } from "vitest";
import {
  ALARM_LIST_SHOWN,
  LEVEL_COLORS,
  alarmMeta,
  alarmSites,
  alarmTitle,
  filterChoices,
  filterLocations,
  groupByCoords,
  isAlarm,
  isPanelShortcut,
  knownLevel,
  levelBreakdown,
  mainGroup,
  needsAttention,
  noCriteria,
  panelToggleLabel,
  quietCluster,
  siteCount,
  splitGroups,
  viewPoints,
  worstLevel,
  type MapLocation,
} from "~/utils/map";
import { apiError, errorMessage } from "~/utils/format";
import { inScope, linkParams, readScope, scopeLabel, scopeParams } from "~/utils/scope";
import { matchSites } from "~/utils/sitesearch";

const site = (id: string, fields: Partial<MapLocation> = {}): MapLocation =>
  ({
    id,
    name: id,
    latitude: 1,
    longitude: 1,
    status: "Active",
    location_type: "Site",
    parent: "",
    tenant: "",
    tenant_group: "",
    ...fields,
  }) as MapLocation;

describe("alert levels", () => {
  it("one colour per level; an unknown one is No data", () => {
    expect(LEVEL_COLORS.maintenance).toBe("#2563eb");
    expect(knownLevel("critical")).toBe("critical");
    expect(knownLevel("constructor")).toBe("no_data");
    expect(knownLevel(undefined)).toBe("no_data");
  });

  it("the worst of a stack or cluster", () => {
    expect(worstLevel(["low", "critical", "no_data"])).toBe("critical");
    // Without a level a site has no monitored devices: grey, not green (#311).
    expect(worstLevel(["nothing"])).toBe("no_data");
    expect(worstLevel(["no_data", "ok"])).toBe("ok");
    expect(worstLevel(["ok", "maintenance"])).toBe("maintenance");
    expect(worstLevel(["maintenance", "low"])).toBe("low");
  });

  it("alarms and the levels never clustered", () => {
    for (const level of ["critical", "medium", "low", "maintenance"]) expect(needsAttention(level)).toBe(true);
    for (const level of ["ok", "no_data"]) expect(needsAttention(level)).toBe(false);
    expect(isAlarm("maintenance")).toBe(false);
    expect(isAlarm("low")).toBe(true);
    expect(isAlarm("toString")).toBe(false);
  });
});

describe("the alarm list", () => {
  // 40 sites: 25 in alarm (9 critical, 8 medium, 8 low), 2 in maintenance, the rest OK.
  const sites = Array.from({ length: 40 }, (_, i) => site(`s${i}`, { name: `Site ${String(i).padStart(2, "0")}`, tenant: i % 2 ? "Odd" : "Even" }));
  const info = Object.fromEntries(
    sites.map((loc, i) => {
      const level = i < 25 ? (["critical", "medium", "low"] as const)[i % 3] : i < 27 ? "maintenance" : "ok";
      const since = i < 25 ? new Date(Date.UTC(2026, 9, 12, 8, i)).toISOString() : null;
      return [loc.id, { level, since, down: 2, monitored: 8, reason: "" }];
    }),
  );

  it("worst first, then newest; maintenance is not an alarm", () => {
    const list = alarmSites(sites, info);
    expect(list).toHaveLength(25);
    expect(list.slice(0, 3).map((entry) => entry.loc.id)).toEqual(["s24", "s21", "s18"]);
    expect(list.at(-1)!.loc.id).toBe("s2");
    expect(levelBreakdown(list)).toBe("9 critical · 8 medium · 8 low");
    expect(ALARM_LIST_SHOWN).toBe(8);
  });

  it("a site panel's fresher level counts", () => {
    const fresher = alarmSites(sites, info, { s0: "ok", s30: "critical" });
    expect(fresher.map((entry) => entry.loc.id)).not.toContain("s0");
    expect(fresher.find((entry) => entry.loc.id === "s30")?.level).toBe("critical");
  });

  it("follows the filters", () => {
    const odd = filterLocations(sites, { ...noCriteria(), tenant: ["Odd"] });
    expect(alarmSites(odd, info)).toHaveLength(12);
  });

  it("says what is down and since when", () => {
    const entry = { loc: site("x"), level: "critical", down: 3, monitored: 7, since: "2026-10-12T08:05:00Z" };
    // The time in the viewer's locale ("08:05" or "08:05 AM").
    expect(alarmMeta(entry, new Date("2026-10-12T12:00:00Z"))).toMatch(/^3\/7 down · since 08:05/);
    expect(alarmMeta({ ...entry, since: null })).toBe("3/7 down");
  });

  it("all OK only for every site", () => {
    expect(alarmTitle(1234, true, true)).toBe("1,234 in alarm");
    expect(alarmTitle(0, true, true)).toBe("All sites OK");
    // Filtered, or a link's scope: other sites may have alarms (#392).
    expect(alarmTitle(0, false, true)).toBe("No site in alarm");
    expect(alarmTitle(0, true, false)).toBe("No site in alarm");
  });
});

describe("the filters", () => {
  const sites = [
    site("a", { tenant: "Acme", tenants: ["Acme", "Nordic"], status: "Active" }),
    site("b", { tenant: "Nordic", status: "Planned", location_type: "Data Center" }),
    site("c", { tenant: "", status: "Active" }),
  ];

  it("each filter's choices, every tenant of a site included (#238)", () => {
    const choices = filterChoices(sites);
    expect(choices.tenant).toEqual(["Acme", "Nordic"]);
    expect(choices.status).toEqual(["Active", "Planned"]);
    expect(choices.parent).toEqual([]);
  });

  it("any of a filter's values, every filter", () => {
    const ids = (criteria: Partial<ReturnType<typeof noCriteria>>) =>
      filterLocations(sites, { ...noCriteria(), ...criteria }).map((loc) => loc.id);
    expect(ids({})).toEqual(["a", "b", "c"]);
    expect(ids({ tenant: ["Nordic"] })).toEqual(["a", "b"]);
    expect(ids({ tenant: ["Nordic"], status: ["Active"] })).toEqual(["a"]);
    expect(ids({ status: ["Active", "Planned"] })).toEqual(["a", "b", "c"]);
  });

  it("the count", () => {
    expect(siteCount(1200, false)).toBe("1,200 sites");
    expect(siteCount(1, false)).toBe("1 site");
    expect(siteCount(25, true)).toBe("25 sites in alarm");
  });
});

describe("markers at scale (#334)", () => {
  it("sites that need attention are pins on top, the rest cluster", () => {
    // 1,200 sites, 25 in alarm, 3 in maintenance; two co-located pairs.
    const sites = Array.from({ length: 1200 }, (_, i) =>
      site(`s${i}`, { latitude: 40 + (i % 40) * 0.5, longitude: -5 + Math.floor(i / 40) * 0.8 }),
    );
    sites.push(
      site("pair-alarm", { latitude: 60, longitude: 10 }),
      site("pair-ok", { latitude: 60, longitude: 10 }),
      site("pair-q1", { latitude: 61, longitude: 11 }),
      site("pair-q2", { latitude: 61, longitude: 11 }),
    );
    const levels: Record<string, string> = { "pair-alarm": "critical", "pair-ok": "ok" };
    for (let i = 0; i < 25; i += 1) levels[`s${i * 40}`] = ["critical", "medium", "low"][i % 3]!;
    for (let i = 0; i < 3; i += 1) levels[`s${i * 40 + 7}`] = "maintenance";
    const levelOf = (id: string) => knownLevel(levels[id]);

    const groups = groupByCoords(sites);
    expect(groups).toHaveLength(1202);
    const { attention, quiet } = splitGroups(groups, levelOf);
    expect(attention).toHaveLength(29);
    expect(attention.some((group) => group.length === 2)).toBe(true);
    expect(quiet).toHaveLength(1202 - 29);
    expect(quiet.find((group) => group[0]!.id === "pair-q1")).toHaveLength(2);
  });

  it("a cluster counts sites and is never an alarm colour", () => {
    expect(quietCluster(["a", "b", "c"], () => "no_data")).toEqual({ count: 3, level: "no_data" });
    expect(quietCluster(["a", "b"], (id) => (id === "b" ? "ok" : "no_data")).level).toBe("ok");
  });

  it("no coordinates: not on the map", () => {
    expect(groupByCoords([site("x", { latitude: null as unknown as number })])).toEqual([]);
  });
});

describe("the first view (#362, #459)", () => {
  it("every site with coordinates, not 0,0", () => {
    const points = viewPoints([
      site("a", { latitude: 55.68, longitude: 12.57 }),
      site("none", { latitude: null as unknown as number, longitude: null as unknown as number }),
      site("never-set", { latitude: 0, longitude: 0 }),
      site("b", { latitude: 51.5, longitude: 0 }),
    ]);
    expect(points).toEqual([
      [55.68, 12.57],
      [51.5, 0],
    ]);
  });

  it("a lone far-off site doesn't pull the view", () => {
    // Steady "random" spread, so the test is the same every run.
    let seed = 7;
    const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const spread = (n: number, lat: number, lon: number, size: number) =>
      Array.from({ length: n }, () => [lat + (rand() - 0.5) * size, lon + (rand() - 0.5) * size * 2] as [number, number]);
    const has = (points: [number, number][], [lat, lon]: [number, number]) => points.some((p) => p[0] === lat && p[1] === lon);
    const GREENLAND: [number, number] = [82.36694950836477, -25.356299993695405];

    const denmark = spread(1200, 56, 10, 3);
    let kept = mainGroup([...denmark, GREENLAND]);
    expect(has(kept, GREENLAND)).toBe(false);
    expect(kept).toHaveLength(1200);

    // Europe-wide: Reykjavik is part of it, northern Greenland still isn't.
    const europe = spread(1200, 50, 10, 20);
    kept = mainGroup([...europe, [64.15, -21.94], GREENLAND]);
    expect(has(kept, [64.15, -21.94])).toBe(true);
    expect(has(kept, GREENLAND)).toBe(false);
    // Two sites: both framed, however far apart.
    expect(mainGroup([GREENLAND, [56, 10]])).toHaveLength(2);
  });
});

describe("the folding panel (#420)", () => {
  it("its tab shows the alarm count", () => {
    expect(panelToggleLabel(false, 3)).toEqual({ text: "", label: "Hide the panel" });
    expect(panelToggleLabel(true, null).text).toBe("Show the panel");
    expect(panelToggleLabel(true, 0).text).toBe("Show the panel");
    expect(panelToggleLabel(true, 1234).text).toBe("1,234 in alarm");
    expect(panelToggleLabel(true, 23).label).toBe("Show the panel: 23 in alarm");
  });

  it("[ is not typing", () => {
    const page = { closest: () => null } as unknown as EventTarget;
    const field = { closest: (selector: string) => (selector.includes("input") ? {} : null) } as unknown as EventTarget;
    const on = (target: EventTarget, extra: Partial<KeyboardEvent> = {}) =>
      isPanelShortcut({ key: "[", target, metaKey: false, ctrlKey: false, altKey: false, ...extra });
    expect(on(page)).toBe(true);
    expect(on(field)).toBe(false);
    expect(on(page, { ctrlKey: true })).toBe(false);
    expect(on(page, { metaKey: true })).toBe(false);
    // AltGr+8 on a Danish keyboard (Windows), Option+8 on a Danish Mac.
    expect(on(page, { ctrlKey: true, altKey: true })).toBe(true);
    expect(on(page, { altKey: true })).toBe(true);
    expect(isPanelShortcut({ key: "]", target: page, metaKey: false, ctrlKey: false, altKey: false })).toBe(false);
  });
});

describe("a link for some sites (#367)", () => {
  it("reads ?tenant= and ?q=, any tenant, every text, any case", () => {
    expect(readScope("")).toBeNull();
    const scope = readScope("?tenant=acme%20corp&tenant=Nordic%20Net&q=dnk");
    expect(inScope({ id: "a", name: "Aarhus", tenant: "Acme Corp", ancestor_path: "EMEA › DNK" }, scope)).toBe(true);
    expect(inScope({ id: "b", name: "Bergen", tenants: ["Nordic Net"], ancestor_path: "EMEA › NOR" }, scope)).toBe(false);
    expect(inScope({ id: "c", name: "Odense DNK", tenant: "Other" }, scope)).toBe(false);
  });

  it("says which sites, as Nautobot spells the tenant", () => {
    const scope = readScope("?tenant=acme%20corp&q=DNK");
    expect(scopeLabel(scope, [{ id: "a", name: "A", tenant: "Acme Corp" }])).toBe("Acme Corp · “DNK”");
    expect(scopeLabel(scope, [])).toBe("acme corp · “DNK”");
  });

  it("the map's and the board's links keep it; tenant picks replace its tenants", () => {
    const scope = readScope("?tenant=Acme&q=EMEA");
    expect(scopeParams(scope).toString()).toBe("tenant=Acme&q=EMEA");
    expect(linkParams([], scope).toString()).toBe("tenant=Acme&q=EMEA");
    expect(linkParams(["Acme Corp", "Nordic Net"], scope).toString()).toBe("q=EMEA&tenant=Acme+Corp&tenant=Nordic+Net");
    expect(linkParams([], null).toString()).toBe("");
  });
});

describe("the panel's search (#392, #411)", () => {
  const sites = [
    { id: "c1", name: "Copenhagen Office 01", ancestor_path: "EMEA › DNK", physical_address: "Roersvej 33, 5000 Odense C" },
    { id: "c2", name: "Copenhagen DC 02", ancestor_path: "EMEA › DNK" },
    { id: "cph", name: "CPH-01", physical_address: "Main Street 2, Copenhagen" },
    ...Array.from({ length: 1200 }, (_, i) => ({
      id: `s${i}`,
      name: `Site ${i}`,
      ancestor_path: "Denmark",
      physical_address: "Main Street 3, Copenhagen",
    })),
  ];

  it("a full name is opened by Enter; anything else searches the map", () => {
    expect(matchSites(sites, "  cPh-01  ")).toMatchObject({ nameMatch: true });
    expect(matchSites(sites, "  cPh-01  ").matches[0]!.id).toBe("cph");
    expect(matchSites(sites, "Copenhagen DC 02").nameMatch).toBe(true);
    for (const query of ["Copenhagen", "Main Street", "Denmark", "55.6761, 12.5683", "nowhere"]) {
      expect(matchSites(sites, query).nameMatch).toBe(false);
    }
  });

  it("eight at a time at 1,200 sites", () => {
    const city = matchSites(sites, "Copenhagen");
    expect(city.matches).toHaveLength(8);
    expect(city.more).toBe(1195);
    expect(city.matches.map((loc) => loc.id)).toContain("c2");
  });
});

describe("request errors", () => {
  it("the API's own message, else the error's", () => {
    const answered = Object.assign(new Error("[GET] /api/search: 404"), { data: { error: "Address not found: Atlantis" } });
    expect(apiError(answered)).toBe("Address not found: Atlantis");
    expect(errorMessage(answered)).toBe("Address not found: Atlantis");
    const page = Object.assign(new Error("[GET] /api/search: 502 Bad Gateway"), { data: "<html>…</html>" });
    expect(apiError(page)).toBe("");
    expect(errorMessage(page)).toBe("[GET] /api/search: 502 Bad Gateway");
    expect(errorMessage("offline")).toBe("offline");
  });
});
