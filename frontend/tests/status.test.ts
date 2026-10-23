// The tenant status page's logic (#291, #407; on Nuxt since #463).
import { describe, expect, it } from "vitest";
import { errorText, FOLD_ABOVE, headline, siteWhen, splitSites, stateLabel, statusKey } from "~/utils/status";

const now = new Date("2026-10-09T12:00:00Z");

describe("what the page says", () => {
  it("headline", () => {
    expect(headline({ summary: { sites: 3, affected: 0 } })).toBe("All 3 sites operational");
    expect(headline({ summary: { sites: 1, affected: 0 } })).toBe("Operational");
    expect(headline({ summary: { sites: 5, affected: 2 } })).toBe("2 of 5 sites affected");
    expect(headline({ summary: { sites: 1, affected: 1 } })).toBe("1 of 1 site affected");
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
    expect(errorText(404)).toBe("No sites found.");
    expect(errorText(503)).toContain("Retrying");
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
