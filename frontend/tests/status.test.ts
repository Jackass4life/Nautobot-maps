// The tenant status page's logic (#291, #407; on Nuxt since #463).
import { describe, expect, it } from "vitest";
import { circuitsLabel, circuitText, errorText, FOLD_ABOVE, headline, siteDevices, sitePlace, siteWhen, splitSites, stateLabel, statusKey } from "~/utils/status";

const now = new Date("2026-10-09T12:00:00Z");

describe("what the page says", () => {
  it("headline", () => {
    expect(headline({ summary: { sites: 3, affected: 0, devices_down: 0 } })).toBe("All 3 sites operational");
    expect(headline({ summary: { sites: 1, affected: 0, devices_down: 0 } })).toBe("Operational");
    expect(headline({ summary: { sites: 5, affected: 2, devices_down: 0 } })).toBe("2 of 5 sites affected");
    expect(headline({ summary: { sites: 1, affected: 1, devices_down: 0 } })).toBe("1 of 1 site affected");
    // How many devices are down (#503); after a core outage, hundreds.
    expect(headline({ summary: { sites: 30, affected: 2, devices_down: 4 } })).toBe("2 of 30 sites affected · 4 devices down");
    expect(headline({ summary: { sites: 300, affected: 40, devices_down: 1250 } })).toBe("40 of 300 sites affected · 1,250 devices down");
    expect(headline({ summary: { sites: 3, affected: 1, devices_down: 1 } })).toBe("1 of 3 sites affected · 1 device down");
    // Access points a wireless controller reports down never make a site affected (#508).
    expect(headline({ summary: { sites: 75, affected: 0, devices_down: 3 } })).toBe("All 75 sites operational · 3 devices down");
  });

  it("each site's devices: counts only, what is down when affected (#503)", () => {
    const site = (state: string, network: number, aps: number, networkDown = 0, apsDown = 0) =>
      siteDevices({ state: state as "outage", network_devices: network, access_points: aps, network_devices_down: networkDown, access_points_down: apsDown });
    expect(site("outage", 12, 40, 3, 1)).toBe("3 of 12 network devices down · 1 of 40 access points down");
    expect(site("degraded", 12, 40, 0, 5)).toBe("12 network devices · 5 of 40 access points down");
    expect(site("outage", 1, 0, 1)).toBe("1 of 1 network device down");
    expect(site("outage", 0, 1200, 0, 300)).toBe("300 of 1,200 access points down");
    expect(site("operational", 12, 40)).toBe("12 network · 40 APs");
    expect(site("maintenance", 0, 1)).toBe("1 AP");
    expect(site("unknown", 0, 0)).toBe("");
    // Nothing down (an alert from a webhook), or in maintenance or without data: the quiet line.
    expect(site("outage", 12, 40)).toBe("12 network · 40 APs");
    expect(site("maintenance", 12, 40, 3, 1)).toBe("12 network · 40 APs");
    expect(site("unknown", 12, 40, 0, 2)).toBe("12 network · 40 APs");
    // An operational site with access points down per its wireless controller (#508).
    expect(site("operational", 12, 40, 0, 2)).toBe("12 network devices · 2 of 40 access points down");
  });

  it("each site's state and since or until when", () => {
    expect(stateLabel("outage")).toBe("Outage");
    expect(stateLabel("maintenance")).toBe("Planned maintenance");
    expect(stateLabel("weird")).toBe("No data");
    expect(siteWhen({ since: "2026-10-09T11:00:00Z", until: null }, now)).toMatch(/^since \d\d:\d\d/);
    expect(siteWhen({ since: null, until: "2026-10-12T14:00:00Z" }, now)).toMatch(/^until (12 Oct|Oct 12) /);
    expect(siteWhen({ since: null, until: null }, now)).toBe("");
  });

  it("the key from the link, and what a refusal says", () => {
    expect(statusKey("#key=nmt_abc")).toBe("nmt_abc");
    expect(statusKey("")).toBe("");
    expect(errorText(403)).toContain("doesn't work");
    expect(errorText(401)).toContain("doesn't work");
    expect(errorText(404)).toBe("No sites found for this link. If it is an old link, ask your contact for a new one.");
    expect(errorText(503)).toContain("Retrying");
  });
});

describe("each site's circuits (#511)", () => {
  it("folded as how many, one line each when opened, without what is missing", () => {
    expect(circuitsLabel(1)).toBe("1 circuit");
    expect(circuitsLabel(1200)).toBe("1,200 circuits");
    expect(circuitText({ cid: "DK-TELIA-123456", type: "Internet Transit", bandwidth: "1 Gbps" })).toBe("DK-TELIA-123456 · Internet Transit · 1 Gbps");
    expect(circuitText({ cid: "GTT-7781", type: "", bandwidth: "500 Mbps" })).toBe("GTT-7781 · 500 Mbps");
  });
});

describe("a large tenant sees what is wrong first (#407)", () => {
  const site = (i: number, state: string) => ({ name: `Site ${i}`, state });
  // 300 sites as the API sorts them: 4 outage, 3 degraded, 3 maintenance, 2 no data, 288 operational.
  const sites = [
    ...[0, 1, 2, 3].map((i) => site(i, "outage")),
    ...[4, 5, 6].map((i) => site(i, "degraded")),
    ...[7, 8, 9].map((i) => site(i, "maintenance")),
    ...[10, 11].map((i) => site(i, "unknown")),
    ...Array.from({ length: 288 }, (_, i) => site(12 + i, "operational")),
  ];

  it("folds the operational ones, worst first as the API sorts", () => {
    const { attention, operational } = splitSites(sites);
    expect(attention.map((s) => s.name)).toEqual(sites.slice(0, 12).map((s) => s.name));
    expect(operational).toHaveLength(288);
  });

  it("all operational: nothing first, all folded", () => {
    const fine = splitSites(Array.from({ length: 300 }, (_, i) => site(i, "operational")));
    expect(fine.attention).toEqual([]);
    expect(fine.operational).toHaveLength(300);
  });

  it("ten sites or fewer: every one open", () => {
    expect(FOLD_ABOVE).toBe(10);
    const small = splitSites([site(1, "outage"), ...Array.from({ length: 9 }, (_, i) => site(i + 2, "operational"))]);
    expect(small.attention).toHaveLength(10);
    expect(small.operational).toEqual([]);
  });

  it("nothing operational: no fold", () => {
    expect(splitSites(sites.slice(0, 12)).operational).toEqual([]);
  });
});

describe("where a site is (#493)", () => {
  it("the address, as an OpenStreetMap search", () => {
    expect(sitePlace({ address: "Nørregade 7, 1165 København", latitude: null, longitude: null })).toEqual({
      text: "Nørregade 7, 1165 København",
      href: "https://www.openstreetmap.org/search?query=N%C3%B8rregade%207%2C%201165%20K%C3%B8benhavn",
    });
  });

  it("the address, as a pin when the coordinates are known (exact)", () => {
    expect(sitePlace({ address: "Unit 4, Industrial Estate", latitude: 55.68, longitude: 12.58 })).toEqual({
      text: "Unit 4, Industrial Estate",
      href: "https://www.openstreetmap.org/?mlat=55.68000&mlon=12.58000#map=16/55.68000/12.58000",
    });
  });

  it("else the coordinates, as a pin", () => {
    expect(sitePlace({ address: null, latitude: 55.676098, longitude: -0.0235 })).toEqual({
      text: "55.6761, -0.0235",
      href: "https://www.openstreetmap.org/?mlat=55.67610&mlon=-0.02350#map=16/55.67610/-0.02350",
    });
    expect(sitePlace({ address: null, latitude: 0, longitude: 0 })?.text).toBe("0.0000, 0.0000");
  });

  it("nothing when Nautobot has neither", () => {
    expect(sitePlace({ address: null, latitude: null, longitude: null })).toBeNull();
    expect(sitePlace({ address: null, latitude: 55.1, longitude: null })).toBeNull();
  });
});
