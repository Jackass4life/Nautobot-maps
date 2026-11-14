// Each role at its own addresses (#697): where a page load goes instead.
import { describe, expect, it } from "vitest";
import { pageAt, workspaceMove, workspaceRoutes } from "~/utils/workspaces";

const go = (role: string | null, path: string, query = "") =>
  workspaceMove(
    role,
    { customer: "/customer", noc: "/noc", operator: "/nwo" }[role ?? ""] ?? "/",
    path,
    query,
  );

describe("the workspace routes", () => {
  it("every page of each workspace, and a tenant's status", () => {
    const paths = workspaceRoutes().map((r) => r.path);
    expect(paths).toContain("/customer");
    expect(paths).toContain("/customer/tenants/:name");
    expect(paths).toContain("/noc");
    expect(paths).toContain("/noc/board");
    expect(paths).toContain("/nwo");
    expect(paths).toContain("/nwo/wall");
    expect(paths).toContain("/nwo/tenants/:name");
    expect(paths).not.toContain("/noc/circuits");
  });
});

describe("which page a path shows", () => {
  it("flat, wall by its query, in a workspace, a tenant", () => {
    expect(pageAt("/", false)).toEqual({ page: "map", rest: "" });
    expect(pageAt("/alerts", true)).toEqual({ page: "wall", rest: "" });
    expect(pageAt("/alerts", false)).toEqual({ page: "alerts", rest: "" });
    expect(pageAt("/noc", false)).toEqual({ page: "wall", rest: "" });
    expect(pageAt("/nwo/board", false)).toEqual({ page: "alerts", rest: "" });
    expect(pageAt("/tenants/Acme%20Corp", false)).toEqual({
      page: "tenants",
      rest: "/Acme%20Corp",
    });
    expect(pageAt("/customer/tenants/Acme", false)).toEqual({ page: "tenants", rest: "/Acme" });
    expect(pageAt("/help", false)).toBeNull();
    expect(pageAt("/admin", false)).toBeNull();
  });
});

describe("where a page load goes", () => {
  it("/ to the role's landing page, quietly", () => {
    expect(go("customer", "/")).toEqual({ to: "/customer", refused: false });
    expect(go("noc", "/")).toEqual({ to: "/noc", refused: false });
    expect(go("operator", "/")).toEqual({ to: "/nwo", refused: false });
  });
  it("a page to the same one in the workspace, the query kept", () => {
    expect(go("noc", "/alerts", "tenant=Acme&q=x")).toEqual({
      to: "/noc/board?tenant=Acme&q=x",
      refused: false,
    });
    expect(go("noc", "/alerts", "view=wall&tenant=Acme")).toEqual({
      to: "/noc?tenant=Acme",
      refused: false,
    });
    expect(go("operator", "/", "location_id=x")).toEqual({
      to: "/nwo?location_id=x",
      refused: false,
    });
    // Maintenance's own ?view= stays.
    expect(go("noc", "/maintenance", "view=history&site_id=s1")).toEqual({
      to: "/noc/maintenance?view=history&site_id=s1",
      refused: false,
    });
    expect(go("customer", "/tenants/Acme%20Corp")).toEqual({
      to: "/customer/tenants/Acme%20Corp",
      refused: false,
    });
    // Another role's workspace: its own.
    expect(go("operator", "/noc/board")).toEqual({ to: "/nwo/board", refused: false });
  });
  it("a page outside the workspace to its landing page, saying so", () => {
    expect(go("noc", "/circuits")).toEqual({ to: "/noc", refused: true });
    expect(go("customer", "/alerts")).toEqual({ to: "/customer", refused: true });
    expect(go("noc", "/tenants/Acme")).toEqual({ to: "/noc", refused: true });
    expect(go("customer", "/nwo")).toEqual({ to: "/customer", refused: true });
  });
  it("stays where it is", () => {
    expect(go("noc", "/noc")).toBeNull();
    expect(go("noc", "/noc/board", "q=x")).toBeNull();
    expect(go("customer", "/customer/tenants/Acme")).toBeNull();
    expect(go("noc", "/help/roles")).toBeNull();
    expect(go("operator", "/admin")).toBeNull();
    expect(go("viewer", "/alerts", "view=wall")).toBeNull();
    expect(go(null, "/")).toBeNull();
  });
  it("roles without a workspace: the flat address", () => {
    expect(go("viewer", "/nwo/board", "q=x")).toEqual({ to: "/alerts?q=x", refused: false });
    expect(go("admin", "/noc")).toEqual({ to: "/alerts?view=wall", refused: false });
    expect(go(null, "/customer/tenants/Acme")).toEqual({ to: "/tenants/Acme", refused: false });
    expect(go("admin", "/nwo")).toEqual({ to: "/", refused: false });
  });
  it("a capitalised workspace goes lowercase, a tenant's name stays", () => {
    expect(go("noc", "/NOC")).toEqual({ to: "/noc", refused: false });
    expect(go("operator", "/NWO/Board")).toEqual({ to: "/nwo/board", refused: false });
    expect(go("customer", "/Customer/Tenants/AcMe")).toEqual({
      to: "/customer/tenants/AcMe",
      refused: false,
    });
    expect(go("viewer", "/Help")).toBeNull();
  });
  it("the old /dov addresses open the same page at /noc (#703)", () => {
    expect(go("noc", "/dov")).toEqual({ to: "/noc", refused: false });
    expect(go("noc", "/DOV/Board", "q=x")).toEqual({ to: "/noc/board?q=x", refused: false });
    expect(go("admin", "/dov")).toEqual({ to: "/alerts?view=wall", refused: false });
  });
});
