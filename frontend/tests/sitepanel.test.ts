// The map's site panel (#394, #398, #400, #405) and its coordinate check
// (#368, #416); on Nuxt since #463.
import { describe, expect, it } from "vitest";
import { COORDINATE_LIST_LIMIT, canShow, coordinateList, coordinateSummary, type CoordinateProblem } from "~/utils/coordinates";
import {
  casesShown,
  deviceCase,
  nobodyOnIt,
  noCaseText,
  circuitMatchNote,
  circuitTermination,
  deviceAside,
  deviceCountNote,
  deviceFacts,
  deviceHealth,
  deviceKey,
  filterDevices,
  groupStacks,
  healthCounts,
  maintenanceHistoryHref,
  memberText,
  sortDownDevices,
  statusLine,
  type SiteCircuit,
  type SiteDevice,
} from "~/utils/sitepanel";

const device = (name: string, fields: Partial<SiteDevice> = {}): SiteDevice =>
  ({
    id: name,
    name,
    status: "Active",
    role: "Access",
    level: "low",
    core: false,
    vc_id: "",
    vc_position: null,
    vc_master: false,
    primary_ip: "10.0.0.1/24",
    not_monitored: "",
    serial: "",
    device_type: "",
    manufacturer: "",
    platform: "",
    tenant: "",
    ...fields,
  }) as SiteDevice;

describe("the status line (#394)", () => {
  it("one line per level", () => {
    expect(statusLine({ level: "no_data", reason: "No monitored devices" }, null)).toEqual({
      level: "no_data",
      text: "No data · no monitored devices",
      reason: "",
    });
    expect(statusLine({ level: "ok", reason: "" }, { down: 0, monitored: 3 }).text).toBe("OK · 3 devices");
    const low = statusLine({ level: "low", reason: "1/20 devices offline (5%)" }, { down: 1, monitored: 20 });
    expect(low).toEqual({ level: "low", text: "Low · 1 of 20 down", reason: "1/20 devices offline (5%)" });
    expect(statusLine({ level: "medium" }, { down: 6, monitored: 20 }, "14:05").text).toBe("Medium · 6 of 20 down · since 14:05");
    expect(statusLine({ level: "maintenance", reason: "Core upgrade" }, { down: 0, monitored: 4 }).reason).toBe("Core upgrade");
  });
});

describe("devices", () => {
  it("health from the status", () => {
    expect(deviceHealth("Active")).toBe("active");
    expect(deviceHealth("offline")).toBe("down");
    expect(deviceHealth("Planned")).toBe("unknown");
  });

  it("the down devices that count: level, core first, then name (#285, #316, #377)", () => {
    const down = sortDownDevices([
      device("z-acc", { status: "offline", level: "low" }),
      device("a-acc", { status: "offline", level: "low" }),
      device("core", { status: "offline", level: "low", core: true }),
      device("crit", { status: "offline", level: "critical" }),
      device("maint", { status: "offline", maintenance_until: "2026-10-10T21:00:00Z" }),
      device("pending", { status: "offline", alert_pending: true }),
      device("unmonitored", { status: "offline", not_monitored: "no primary IP" }),
      device("up"),
    ]);
    expect(down.map((d) => d.name)).toEqual(["crit", "core", "a-acc", "z-acc"]);
  });

  it("why a device isn't counted, where its role would be (#316)", () => {
    expect(deviceAside(device("a"), 1)).toBe("Access");
    expect(deviceAside(device("a", { not_monitored: "no IP" }), 1)).toBe("Not monitored");
    expect(deviceAside(device("a", { alert_pending: true }), 4)).toBe("Stack of 4 · Alert delay");
    expect(deviceCountNote(device("a"))).toBe("");
    expect(deviceCountNote(device("a", { not_monitored: "no primary IP" }))).toBe("Not monitored: no primary IP");
    expect(deviceCountNote(device("a", { maintenance_until: "2026-10-10T21:43:00Z" }))).toMatch(/^In maintenance until (21|09):43( PM)?: not counted$/);
    expect(deviceCountNote(device("a", { alert_pending: true }))).toContain("not an alarm yet");
  });

  it("an opened device's facts", () => {
    const facts = deviceFacts(
      device("sw1", { status: "offline", manufacturer: "Cisco", device_type: "C9300", serial: "FOC1", platform: "IOS-XE" }),
      "Access",
    );
    expect(facts).toEqual(["Cisco C9300", "IP 10.0.0.1", "Software IOS-XE", "Serial FOC1", "Status offline"]);
    // The role is in the facts when something else stands in its place.
    expect(deviceFacts(device("sw1"), "Stack of 4")[0]).toBe("Access");
  });

  it("a key without an id", () => {
    expect(deviceKey(device("a"))).toBe("a");
    expect(deviceKey({ id: "", name: "a", serial: "s", primary_ip: "ip" })).toBe("a|s|ip");
  });

  it("filters and search, over ten devices", () => {
    const devices = [device("a"), device("b", { status: "offline" }), device("c", { status: "Planned", role: "Core" })];
    expect(healthCounts(devices)).toEqual({ all: 3, active: 1, down: 1, unknown: 1 });
    expect(filterDevices(devices, "down", "").map((d) => d.name)).toEqual(["b"]);
    expect(filterDevices(devices, "all", "core").map((d) => d.name)).toEqual(["c"]);
  });
});

describe("stacks (#400, #405)", () => {
  it("a virtual chassis is one entry, led by its monitored master", () => {
    const members = [
      device("par-stack01:2", { vc_id: "vc", vc_position: 2, primary_ip: "" }),
      device("par-stack01", { vc_id: "vc", vc_position: 1, vc_master: true }),
      device("par-stack01:3", { vc_id: "vc", vc_position: 3, primary_ip: "" }),
      device("par-stack01:4", { vc_id: "vc", vc_position: 4, primary_ip: "" }),
    ];
    const entries = groupStacks([device("par-rt01"), ...members, device("alone", { vc_id: "solo" })]);
    expect(entries.map((entry) => entry.device.name)).toEqual(["par-rt01", "par-stack01", "alone"]);
    expect(entries[1]!.members.map(memberText)).toEqual(["2. par-stack01:2", "3. par-stack01:3", "4. par-stack01:4"]);
    expect(deviceAside(entries[1]!.device, 4)).toBe("Stack of 4");
  });

  it("without a monitored member, the master, else the first by position", () => {
    const entries = groupStacks([
      device("b", { vc_id: "vc", vc_position: 2, primary_ip: "" }),
      device("a", { vc_id: "vc", vc_position: 1, primary_ip: "" }),
    ]);
    expect(entries.map((entry) => entry.device.name)).toEqual(["a"]);
  });
});

describe("circuits (#235, #383)", () => {
  const circuit = (fields: Partial<SiteCircuit>) => ({ id: "c", cid: "C", ...fields }) as SiteCircuit;
  it("the termination and its speeds", () => {
    expect(circuitTermination(circuit({ term_side: "A", port_speed: "1 Gbps", upstream_speed: "500 Mbps" }))).toBe(
      "Side A · 1 Gbps down / 500 Mbps up",
    );
    expect(circuitTermination(circuit({ port_speed: "10 Gbps", upstream_speed: "10 Gbps" }))).toBe("10 Gbps");
  });

  it("found by the address", () => {
    expect(circuitMatchNote(circuit({}))).toBe("");
    expect(circuitMatchNote(circuit({ match: "address", shared_with: 0 }))).toBe("By address");
    expect(circuitMatchNote(circuit({ match: "address", shared_with: 2 }))).toBe("By address · address shared with 2 other sites");
  });

  it("the site's maintenance history (#353)", () => {
    expect(maintenanceHistoryHref("loc 1")).toBe("/maintenance?view=history&site_id=loc+1");
  });
});

describe("the coordinate check (#368, #416)", () => {
  const problem = (problem: CoordinateProblem["problem"], fields: Partial<CoordinateProblem> = {}) =>
    ({ site_id: problem, name: problem, problem, latitude: 1, longitude: 1, reason: "", ancestor_path: "", ...fields }) as CoordinateProblem;

  it("one line, or nothing", () => {
    expect(coordinateSummary([])).toBe("");
    expect(coordinateSummary([problem("missing"), problem("missing"), problem("zero")])).toBe(
      "2 sites aren't on the map · 1 has wrong coordinates",
    );
    expect(coordinateSummary([problem("swapped")])).toBe("1 site has wrong coordinates");
    // Far is a hint: said only when nothing is wrong.
    expect(coordinateSummary([problem("far"), problem("far")])).toBe("2 sites are far from the others");
    expect(coordinateSummary([problem("far"), problem("missing")])).toBe("1 site isn't on the map");
  });

  it("the list: wrong first, far last, the first fifty", () => {
    const many = [...Array.from({ length: 60 }, (_, i) => problem("missing", { site_id: `m${i}` })), problem("far")];
    const list = coordinateList(many);
    expect(list.wrong).toHaveLength(COORDINATE_LIST_LIMIT);
    expect(list.far).toHaveLength(0);
    expect(list.more).toBe(11);
    expect(coordinateList([problem("far"), problem("zero")]).far).toHaveLength(1);
  });

  it("Show only for a site on the map", () => {
    expect(canShow(problem("far"))).toBe(true);
    expect(canShow(problem("missing", { latitude: null }))).toBe(false);
    expect(canShow(problem("invalid"))).toBe(false);
  });
});

describe("who is on it (#512)", () => {
  const device = (extra: Partial<SiteDevice>): SiteDevice => ({ id: "d", name: "d", status: "Offline", not_monitored: "", ...extra }) as SiteDevice;

  it("the site's cases: the first three and how many more", () => {
    expect(casesShown([])).toEqual({ shown: [], more: 0 });
    expect(casesShown(["INC1", "INC2", "INC3", "INC4", "INC5"])).toEqual({ shown: ["INC1", "INC2", "INC3"], more: 2 });
  });

  it("the down devices nobody is on: counted, without a case or an acknowledgement", () => {
    expect(nobodyOnIt([device({ case_numbers: [] }), device({ acknowledged_by: "olga" }), device({ case_numbers: ["INC1"] })])).toBe(1);
    // In maintenance, within the alert delay or not monitored: not waiting for anyone.
    expect(nobodyOnIt([device({ maintenance_until: "x" }), device({ alert_pending: true }), device({ not_monitored: "no IP" })])).toBe(0);
  });

  it("what the panel says: No case, N without a case, or nothing", () => {
    const waiting = Array.from({ length: 29 }, () => device({}));
    expect(noCaseText(waiting, true, false)).toBe("No case");
    // A core outage with one old case: the 29 others still need someone.
    expect(noCaseText([...waiting, device({ case_numbers: ["INC1"] })], true, true)).toBe("29 without a case");
    expect(noCaseText([...waiting, device({ acknowledged_by: "olga" })], true, false)).toBe("29 without a case");
    expect(noCaseText([device({ case_numbers: ["INC1"] })], true, true)).toBe("");
    // Not read (no database): no false alarm.
    expect(noCaseText(waiting, false, false)).toBe("");
  });

  it("a device's case on its row", () => {
    expect(deviceCase(device({}))).toBe("");
    expect(deviceCase(device({ case_numbers: ["INC0048213"] }))).toBe("INC0048213");
    expect(deviceCase(device({ case_numbers: ["INC0048290", "INC0048213"] }))).toBe("INC0048290 +1");
  });
});
