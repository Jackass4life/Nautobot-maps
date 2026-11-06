// A page for some sites only (#364, #368, #369): ?tenant= / ?q= match the
// same sites in the browser as on the server (nautobot_maps/scope.py, which
// tests/test_coordinates.py checks against the same file).
import { describe, expect, it } from "vitest";
import cases from "./fixtures/scope-cases.json";
import { inScope, readScope, type TenantedSite } from "~/utils/scope";

describe("the browser matches the sites the server does", () => {
  const sites = cases.sites as unknown as TenantedSite[];
  for (const [search, ids] of Object.entries(cases.cases)) {
    it(search || "(no scope)", () => {
      expect(sites.filter((site) => inScope(site, readScope(search))).map((site) => site.id)).toEqual(ids);
    });
  }
});
