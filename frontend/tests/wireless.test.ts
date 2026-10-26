// Access points from the wireless controllers (#521): a site with only
// access points down is under the Wireless chip, not an alarm, a quiet dot
// on the map; many access points fold into one line.
import { describe, expect, it } from "vitest";
import {
  accessPointsText,
  chipCounts,
  compareSites,
  downDeviceRows,
  filterSites,
  hasActiveAlarm,
  noBoardCriteria,
  scopeSummary,
  shownChips,
  siteSummaryText,
  type BoardSite,
  type DownDevice,
} from "~/utils/board";
import { alarmSites, displayLevel, worstLevel, type MapLocation } from "~/utils/map";
import { deviceAddress, deviceFacts, isWatchedAccessPoint, statusLine, type SiteDevice } from "~/utils/sitepanel";
import { newAlarms, wallSites } from "~/utils/wall";

const device = (id: string, fields: Partial<DownDevice> = {}): DownDevice =>
  ({ device_id: id, device_name: id, device_ip: "", status: "offline", role: "", case_numbers: [], ...fields }) as DownDevice;
const site = (id: string, fields: Partial<BoardSite> = {}): BoardSite =>
  ({ id, name: id, alert_level: "ok", down_devices: [], latest_down_at: null, tier_rank: 99, down_device_count: 0, ...fields }) as BoardSite;
const aps = (count: number) => Array.from({ length: count }, (_, n) => device(`ap${n + 1}`, { access_point: true, role: "Access Point" }));

// A normal day: 5 sites with a switch down, 30 with only access points down, 85 fine.
const day = [
  ...Array.from({ length: 5 }, (_, n) => site(`alarm${n}`, { alert_level: "low", down_device_count: 1, latest_down_at: "2026-10-09T10:00:00Z" })),
  ...Array.from({ length: 30 }, (_, n) =>
    site(`wifi${n}`, { alert_level: "low", wireless_only: true, down_device_count: 2 + (n % 15), latest_down_at: "2026-10-09T11:59:00Z" }),
  ),
  ...Array.from({ length: 85 }, (_, n) => site(`fine${n}`)),
];

describe("the board", () => {
  it("is not an alarm, and has its own chip", () => {
    expect(hasActiveAlarm(site("a", { alert_level: "low", wireless_only: true }))).toBe(false);
    expect(hasActiveAlarm(site("a", { alert_level: "low" }))).toBe(true);
    const summary = scopeSummary(day);
    expect([summary.low, summary.wireless]).toEqual([5, 30]);
    expect(chipCounts(summary)).toMatchObject({ alarms: 5, wireless: 30, total: 120 });
    expect(filterSites(day, noBoardCriteria()).map((item) => item.id)).toEqual(day.slice(0, 5).map((item) => item.id));
    expect(filterSites(day, { ...noBoardCriteria(), severity: "wireless" })).toHaveLength(30);
  });

  it("shows the Wireless chip only when it has sites or is chosen", () => {
    const labels = (counts: Record<string, number>, selected = "alarms" as const) => shownChips(counts, selected).map((chip) => chip.label);
    expect(labels({ wireless: 0 })).toEqual(["Alarms", "All sites", "No data", "Maintenance"]);
    expect(labels({ wireless: 30 })).toEqual(["Alarms", "Wireless", "All sites", "No data", "Maintenance"]);
    expect(shownChips({ wireless: 0 }, "wireless").map((chip) => chip.label)).toContain("Wireless");
  });

  it("puts every alarm first, however new the access points are", () => {
    for (const sort of ["newest", "severity"] as const) {
      const sorted = [...day].sort((a, b) => compareSites(a, b, sort));
      expect(sorted.slice(0, 5).every((item) => !item.wireless_only)).toBe(true);
      expect(sorted.slice(5, 35).every((item) => item.wireless_only)).toBe(true);
    }
  });

  it("counts access points apart on the row", () => {
    expect(siteSummaryText({ down_device_count: 1, device_count: 6 })).toBe("1 down · 6 monitored");
    expect(siteSummaryText({ down_device_count: 4, device_count: 46, down_access_point_count: 3, down_network_device_count: 1 })).toBe(
      "1 down · 3 APs down · 46 monitored",
    );
    expect(siteSummaryText({ down_device_count: 1, device_count: 46, down_access_point_count: 1, down_network_device_count: 0 })).toBe(
      "1 AP down · 46 monitored",
    );
  });

  it("folds more than three access points into one line, under the others", () => {
    const sw = device("sw1");
    expect(downDeviceRows([...aps(3), sw]).shown.map((d) => d.device_id)).toEqual(["sw1", "ap1", "ap2", "ap3"]);
    const many = downDeviceRows([...aps(34), sw]);
    expect([many.shown.map((d) => d.device_id), many.folded.length]).toEqual([["sw1"], 34]);
    // One set in Nautobot, or core, is what made the site's level: never folded.
    const critical = device("ap9", { access_point: true, set_level: "critical" });
    expect(downDeviceRows([...aps(5), critical]).shown.map((d) => d.device_id)).toEqual(["ap9"]);
    expect(accessPointsText(34)).toBe("34 access points down");
    expect(accessPointsText(1)).toBe("1 access point down");
  });
});

describe("the wall", () => {
  it("never lists or sounds for a site with only access points down", () => {
    const wifi = site("wifi", { alert_level: "critical", wireless_only: true });
    expect(wallSites([wifi, site("sw", { alert_level: "low" })]).map((item) => item.id)).toEqual(["sw"]);
    const { seen } = newAlarms(null, [wifi]);
    expect(newAlarms(seen, [wifi]).events).toEqual([]);
    expect(seen.has("wifi")).toBe(false);
  });
});

describe("the map", () => {
  const loc = (id: string) => ({ id, name: id }) as MapLocation;

  it("is a quiet dot, not in the alarm list", () => {
    expect(displayLevel({ level: "low", wireless_only: true })).toBe("wireless");
    expect(displayLevel({ level: "low" })).toBe("low");
    const info = { a: { level: "low" as const, wireless_only: true }, b: { level: "low" as const } };
    const levels = { a: displayLevel(info.a), b: displayLevel(info.b) };
    expect(alarmSites([loc("a"), loc("b")], info, levels).map((entry) => entry.loc.id)).toEqual(["b"]);
    // A cluster of quiet sites takes its colour from them, never above an alarm's.
    expect(worstLevel(["ok", "wireless", "no_data"])).toBe("wireless");
    expect(worstLevel(["wireless", "low"])).toBe("low");
  });
});

describe("the site panel", () => {
  const ap = (fields: Partial<SiteDevice> = {}) =>
    ({
      name: "ap1",
      role: "Access Point",
      status: "offline",
      primary_ip: "",
      criticality: "",
      core: false,
      wireless: { up: false, ip: "10.20.31.47", controller: "Catalyst Center dk", stale: false },
      ...fields,
    }) as SiteDevice;

  it("gives the controller's address and says where it comes from", () => {
    expect(deviceAddress(ap())).toBe("IP 10.20.31.47 · from Catalyst Center dk");
    expect(deviceAddress(ap({ wireless: { up: false, ip: "10.20.31.47", controller: "Catalyst Center dk", stale: true } }))).toBe(
      "IP 10.20.31.47 · last known from Catalyst Center dk, which can't be reached",
    );
    expect(deviceAddress(ap({ primary_ip: "10.0.0.9/24" }))).toBe("IP 10.0.0.9");
    expect(deviceAddress(ap({ wireless: { up: true, ip: "", controller: "Aruba Central eu", stale: false } }))).toBe(
      "Watched by Aruba Central eu",
    );
    expect(deviceFacts(ap(), "Access Point")).toContain("IP 10.20.31.47 · from Catalyst Center dk");
  });

  it("says access points down, as its marker, whatever the level", () => {
    const line = statusLine({ level: "critical", reason: "40 access points offline", wireless_only: true }, { down: 40, monitored: 46 }, "14:05");
    expect(line).toEqual({ level: "wireless", text: "Access points down · 40 of 46 down · since 14:05", reason: "40 access points offline" });
    expect(statusLine({ level: "critical", reason: "x" }, { down: 1, monitored: 6 }).level).toBe("critical");
  });

  it("folds access points at the wireless level, not one set in Nautobot", () => {
    expect(isWatchedAccessPoint(ap())).toBe(true);
    expect(isWatchedAccessPoint(ap({ criticality: "critical" }))).toBe(false);
    expect(isWatchedAccessPoint(ap({ core: true }))).toBe(false);
    expect(isWatchedAccessPoint(ap({ wireless: null }))).toBe(false);
  });
});
