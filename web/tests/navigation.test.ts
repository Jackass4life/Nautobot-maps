// The top bar's menu (#737): the pages in the web code, at each role's addresses.
import { describe, expect, it } from "vitest";
import { MENU_PAGES, activePage, menuPages, pageHref } from "~/utils/navigation";

const hrefs = (role: string | null, allowed: string[] | null = null) =>
  menuPages(allowed).map((page) => pageHref(page, role));

describe("the menu's pages", () => {
  it("every page before page-config answers", () => {
    expect(menuPages(null).map((page) => page.key)).toEqual(MENU_PAGES.map((page) => page.key));
    expect(menuPages(null)).toHaveLength(9);
  });

  it("then only the caller's, in the menu's order", () => {
    expect(menuPages(["docs", "availability", "tenants"]).map((page) => page.key)).toEqual([
      "availability",
      "tenants",
      "docs",
    ]);
    expect(menuPages([])).toEqual([]);
  });

  it.each([
    [
      "customer",
      ["availability", "tenants", "help", "docs"],
      ["/customer", "/customer/tenants", "/help", "/api/docs"],
    ],
    [
      "noc",
      ["alerts", "availability", "maintenance", "help", "docs", "wall"],
      ["/noc/board", "/noc/availability", "/noc/maintenance", "/help", "/api/docs", "/noc"],
    ],
  ])("%s: its workspace's addresses (#697)", (role, allowed, expected) => {
    expect(hrefs(role, allowed)).toEqual(expected);
  });

  it("operator: the NWO workspace, outside it the flat addresses", () => {
    expect(hrefs("operator")).toEqual([
      "/nwo",
      "/nwo/board",
      "/nwo/availability",
      "/nwo/maintenance",
      "/nwo/circuits",
      "/nwo/tenants",
      "/help",
      "/api/docs",
      "/nwo/wall",
    ]);
  });

  it("viewer, admin and nobody: the flat addresses", () => {
    const flat = [
      "/",
      "/alerts",
      "/availability",
      "/maintenance",
      "/circuits",
      "/tenants",
      "/help",
      "/api/docs",
      "/alerts?view=wall",
    ];
    expect(hrefs("viewer")).toEqual(flat);
    expect(hrefs("admin")).toEqual(flat);
    expect(hrefs(null)).toEqual(flat);
  });

  it("the map, the board and the wall keep the scope (#366)", () => {
    const scoped = Object.fromEntries(
      MENU_PAGES.map((page) => [page.key, pageHref(page, null, "tenant=Acme%20Corp&q=DNK")]),
    );
    expect(scoped.map).toBe("/?tenant=Acme%20Corp&q=DNK");
    expect(scoped.alerts).toBe("/alerts?tenant=Acme%20Corp&q=DNK");
    expect(scoped.wall).toBe("/alerts?view=wall&tenant=Acme%20Corp&q=DNK");
    expect(scoped.circuits).toBe("/circuits");
    expect(pageHref(MENU_PAGES[0]!, "operator", "q=DNK")).toBe("/nwo?q=DNK");
  });
});

describe("the page shown", () => {
  it.each([
    ["/", false, "map"],
    ["/alerts", false, "alerts"],
    ["/alerts", true, "wall"],
    ["/nwo/board", false, "alerts"],
    ["/noc", false, "wall"],
    ["/customer", false, "availability"],
    ["/tenants", false, "tenants"],
    ["/tenants/Acme", false, "availability"],
    ["/admin", false, "admin"],
    ["/help", false, "help"],
    ["/help/sources", false, "help"],
    ["/api/docs", false, "docs"],
    ["/status/abc", false, null],
    ["/auth", false, null],
  ])("%s (wall: %s) is %s", (path, wall, expected) => {
    expect(activePage(path, wall)).toBe(expected);
  });
});
