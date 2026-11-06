// The wall screen (#243; on Nuxt since #463): its order, case badges, status
// line, error banner, and the chime's alarms and notifications (#290).
import { describe, expect, it } from "vitest";
import type { BoardSite, DownDevice } from "~/utils/board";
import {
  WALL_STALE_MS,
  WallNotifier,
  alarmText,
  caseBadge,
  caseSortRank,
  newAlarms,
  wallErrorText,
  wallSites,
  wallStatus,
  type NotificationApi,
  type WallAlarm,
} from "~/utils/wall";

const device = (id: string, fields: Partial<DownDevice> = {}): DownDevice =>
  ({ device_id: id, device_name: id, device_ip: "", status: "", role: "", case_numbers: [], ...fields }) as DownDevice;
const site = (id: string, fields: Partial<BoardSite> = {}): BoardSite =>
  ({ id, name: id, alert_level: "ok", down_devices: [], latest_down_at: null, tier_rank: 99, down_device_count: 0, ...fields }) as BoardSite;
const ack = { by: "olga", at: "", note: "" };

describe("which sites, in which order", () => {
  it("nobody on it first, then someone on it, then maintenance; newest down within each", () => {
    const sites = [
      site("handled", { alert_level: "critical", latest_down_at: "2026-10-24T10:30:00Z", down_devices: [device("d", { case_numbers: ["INC-1"] })] }),
      site("planned", { alert_level: "maintenance" }),
      site("old", { alert_level: "low", latest_down_at: "2026-10-24T09:00:00Z", down_devices: [device("d")] }),
      site("ok"),
      site("new", { alert_level: "medium", latest_down_at: "2026-10-24T10:00:00Z", down_devices: [device("d")] }),
      site("nodata", { alert_level: "no_data" }),
    ];
    expect(wallSites(sites).map((s) => s.id)).toEqual(["new", "old", "handled", "planned"]);
    expect(caseSortRank(sites[1]!)).toBe(2);
  });

  it("1,200 sites, 25 in alarm: only those", () => {
    const sites = Array.from({ length: 1200 }, (_, i) => site(`s${i}`, { alert_level: i < 25 ? "low" : "ok" }));
    expect(wallSites(sites)).toHaveLength(25);
  });
});

describe("is someone on it", () => {
  it("the case numbers and ACK, how many have none, or NO CASE", () => {
    expect(caseBadge(site("a", { down_devices: [device("1", { case_numbers: ["INC-1"] }), device("2", { acknowledged: ack })] }))).toEqual({
      state: "handled",
      text: "✓ INC-1 · ACK",
    });
    expect(caseBadge(site("a", { down_devices: [device("1", { case_numbers: ["INC-1"] }), device("2"), device("3")] }))).toEqual({
      state: "partly",
      text: "2 of 3 no case or ack",
    });
    expect(caseBadge(site("a", { down_devices: [device("1")] }))).toEqual({ state: "none", text: "NO CASE" });
  });
});

describe("the status line", () => {
  const loaded = Date.UTC(2026, 9, 24, 14, 5);
  const sites = [
    site("a", { alert_level: "critical", down_devices: [device("1")] }),
    site("b", { alert_level: "low", down_devices: [device("1", { acknowledged: ack })] }),
    site("m", { alert_level: "maintenance" }),
  ];

  it("when, how many in alarm, how many nobody is on, maintenance", () => {
    const { text, stale } = wallStatus(sites, loaded, loaded + 60000);
    expect(stale).toBe(false);
    expect(text).toMatch(/^Updated (14|02):05( PM)? · 2 sites with alarms · 1 without case or ack · 1 in maintenance$/);
    expect(wallStatus([], null, loaded).text).toBe("Loading…");
  });

  it("says which sites, and loudly when it hasn't updated for a while", () => {
    expect(wallStatus(sites, loaded, loaded, "“Etage”").text).toMatch(/^“Etage” · Updated /);
    const old = wallStatus(sites, loaded, loaded + WALL_STALE_MS, "Acme Corp");
    expect(old.stale).toBe(true);
    expect(old.text).toMatch(/^Acme Corp · NOT UPDATED since (14|02):05( PM)?$/);
  });

  it("the banner keeps the last good board", () => {
    expect(wallErrorText("timed out", null)).toBe("Alert board not updating: timed out.");
    expect(wallErrorText("timed out", loaded)).toMatch(/^Alert board not updating: timed out\. Showing the board from (14|02):05( PM)?\.$/);
  });
});

describe("the chime's alarms (#290)", () => {
  it("once per new Critical or site nobody is on", () => {
    const down = (fields: Partial<DownDevice> = {}) => [device("d1", fields)];
    const sites = [
      site("a", { name: "Aarhus", alert_level: "critical", down_devices: down({ case_numbers: ["INC-1"] }) }),
      site("o", { name: "Oslo" }),
    ];
    // The first load records what is there: nothing for what was already wrong.
    let result = newAlarms(null, sites);
    expect(result.events).toEqual([]);
    let seen = result.seen;

    // Oslo goes Critical with nobody on it: two things, once each.
    sites[1] = site("o", { name: "Oslo", alert_level: "critical", down_devices: down() });
    result = newAlarms(seen, sites);
    expect(result.events.map((e) => e.kind)).toEqual(["critical", "nobody"]);
    expect(alarmText(result.events[0]!)).toBe("Oslo is Critical");
    expect(alarmText(result.events[1]!)).toBe("Oslo: in alarm, nobody on it");
    seen = result.seen;
    expect(newAlarms(seen, sites).events).toEqual([]);

    // Acknowledged, down to Medium, then Critical again: a new Critical, but someone is on it.
    sites[1] = site("o", { name: "Oslo", alert_level: "medium", down_devices: down({ acknowledged: ack }) });
    seen = newAlarms(seen, sites).seen;
    sites[1] = { ...sites[1], alert_level: "critical" };
    result = newAlarms(seen, sites);
    expect(result.events.map((e) => e.kind)).toEqual(["critical"]);

    // Recovered, then back in alarm with nobody on it: announced again.
    sites[1] = site("o", { name: "Oslo" });
    seen = newAlarms(seen, sites).seen;
    sites[1] = site("o", { name: "Oslo", alert_level: "low", down_devices: down() });
    expect(newAlarms(seen, sites).events.map((e) => e.kind)).toEqual(["nobody"]);
  });
});

describe("notifications", () => {
  // A stand-in for the browser's Notification.
  function fake() {
    const shown: { body: string; open: boolean; close(): void }[] = [];
    class FakeNotification {
      static permission = "granted";
      body: string;
      open = true;
      listeners: (() => void)[] = [];
      constructor(_title: string, options: { body: string }) {
        this.body = options.body;
        shown.push(this);
      }
      addEventListener(_kind: string, listener: () => void) {
        this.listeners.push(listener);
      }
      close() {
        this.open = false;
        this.listeners.forEach((listener) => listener());
      }
    }
    return { api: FakeNotification as unknown as NotificationApi & { permission: string }, shown };
  }
  const alarms = (prefix: string, count: number): WallAlarm[] =>
    Array.from({ length: count }, (_, i) => ({ id: `${prefix}${i + 1}`, name: `${prefix.toUpperCase()}${i + 1}`, kind: "critical" }));

  it("at most five open; the rest wait, none dropped", () => {
    const { api, shown } = fake();
    const notifier = new WallNotifier(() => api);
    notifier.add(alarms("s", 7));
    expect(shown).toHaveLength(5);
    shown[0]!.close();
    expect(shown).toHaveLength(6);
    expect(shown[5]!.body).toBe("S6 is Critical");
    shown[1]!.close();
    expect(shown).toHaveLength(7);
    while (shown.some((n) => n.open)) shown.find((n) => n.open)!.close();
    expect(shown).toHaveLength(7);
  });

  it("before the browser's answer the newest 50 wait; denied, nothing is kept", () => {
    const { api, shown } = fake();
    const notifier = new WallNotifier(() => api);
    api.permission = "default";
    notifier.add(alarms("w", 60));
    expect(shown).toHaveLength(0);
    api.permission = "granted";
    notifier.show();
    expect(shown[0]!.body).toBe("W11 is Critical");
    while (shown.some((n) => n.open)) shown.find((n) => n.open)!.close();
    expect(shown).toHaveLength(50);

    api.permission = "default";
    notifier.add(alarms("x", 3));
    api.permission = "denied";
    notifier.show();
    api.permission = "granted";
    notifier.show();
    expect(shown).toHaveLength(50);
    // Without Notification at all: nothing, no error.
    expect(() => new WallNotifier(() => undefined).add(alarms("y", 2))).not.toThrow();
  });
});
