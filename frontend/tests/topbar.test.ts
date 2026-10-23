// The top bar (#419; on Nuxt since #463): how fresh the data is, and finding a site.
import { describe, expect, it } from "vitest";
import { appStatusView, isSearchShortcut } from "~/utils/appstatus";
import { matchSites } from "~/utils/sitesearch";

const now = new Date("2026-10-22T14:00:00Z");

describe("how fresh the data is", () => {
  it("up to date: when it was updated", () => {
    const ok = appStatusView(
      {
        state: "ok",
        updated_at: "2026-10-22T13:56:00Z",
        sources: [{ name: "Nautobot", state: "ok", message: "Nautobot is up to date", last_success: "2026-10-22T13:56:00Z" }],
      },
      now,
    );
    expect(ok.state).toBe("ok");
    expect(ok.text).toMatch(/^Updated /);
    expect(ok.intro).toContain("up to date");
    expect(ok.lines).toEqual([{ state: "ok", what: "Nautobot", when: expect.stringMatching(/^synced /) }]);
  });

  it("the worst source speaks, and when it was last good", () => {
    const bad = appStatusView(
      {
        state: "down",
        updated_at: null,
        sources: [
          { name: "Nautobot", state: "stale", message: "Nautobot data is 45 min old", last_success: "2026-10-22T13:15:00Z" },
          { name: "LibreNMS", state: "down", message: "Can't reach LibreNMS", last_success: "2026-10-22T13:50:00Z" },
        ],
      },
      now,
    );
    expect(bad.text).toBe("Can't reach LibreNMS");
    expect(bad.lines[1]).toEqual({ state: "down", what: "Can't reach LibreNMS", when: expect.stringMatching(/^last good /) });
    expect(appStatusView(null).text).toBe("Can't reach Nautobot Maps");
  });
});

describe("find a site", () => {
  it("eight at most of 1,200, and how many more", () => {
    const sites = Array.from({ length: 1200 }, (_, i) => ({ id: `s${i}`, name: `Site ${i}`, ancestor_path: "EMEA › DNK" }));
    const { matches, more } = matchSites(sites, "site");
    expect(matches).toHaveLength(8);
    expect(more).toBe(1192);
  });

  it("names first, then path or address; every word", () => {
    const sites = [
      { id: "c1", name: "Copenhagen DC 02", ancestor_path: "EMEA › DNK" },
      { id: "c2", name: "Copenhagen Office 01", physical_address: "Vestergade 1, Odense" },
      { id: "x", name: "Bank HQ", ancestor_path: "EMEA › DNK › Copenhagen" },
    ];
    const names = (query: string) => matchSites(sites, query).matches.map((site) => site.name);
    expect(names("copenhagen")).toEqual(["Copenhagen DC 02", "Copenhagen Office 01", "Bank HQ"]);
    expect(names("copenhagen office")).toEqual(["Copenhagen Office 01"]);
    expect(names("odense")).toEqual(["Copenhagen Office 01"]);
    expect(names("bank hq")).toEqual(["Bank HQ"]);
    expect(matchSites(sites, "55.6761, 12.5683").matches).toEqual([]);
    expect(matchSites(sites, "   ").matches).toEqual([]);
  });

  it('"/" jumps to it, not while typing or with a modifier', () => {
    const page = { closest: () => null } as unknown as EventTarget;
    const field = { closest: () => ({}) } as unknown as EventTarget;
    const key = (target: EventTarget, extra = {}) =>
      isSearchShortcut({ key: "/", ctrlKey: false, metaKey: false, altKey: false, target, ...extra });
    expect(key(page)).toBe(true);
    expect(key(field)).toBe(false);
    expect(key(page, { ctrlKey: true })).toBe(false);
  });
});
