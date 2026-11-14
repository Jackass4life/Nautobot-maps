// Buttons and links follow the caller's permissions (#695).
import { describe, expect, it } from "vitest";
import { allows, scopeCovered } from "~/composables/useCan";

describe("allows", () => {
  it("a permission page-config lists", () => {
    expect(allows(["page:board", "alerts:ack"], "alerts:ack")).toBe(true);
  });
  it("not one it doesn't list", () => {
    expect(allows(["page:board"], "alerts:ack")).toBe(false);
    // A customer: Availability, no status link controls.
    expect(allows(["page:availability", "tenant:status:view"], "status:links:view")).toBe(false);
  });
  it("nothing before page-config has answered", () => {
    expect(allows(undefined, "page:map")).toBe(false);
    expect(allows([], "page:map")).toBe(false);
  });
  it("a wildcard covers one part (#711)", () => {
    expect(allows(["alerts:*"], "alerts:ack")).toBe(true);
    expect(allows(["*:*:*"], "status:links:view")).toBe(true);
    expect(allows(["*:*"], "status:links:view")).toBe(false);
    expect(allows(["*:*"], "admin")).toBe(false);
  });
});

describe("scopeCovered", () => {
  it("capitals don't matter, the number of parts does", () => {
    expect(scopeCovered("Alerts:Ack", ["alerts:ACK"])).toBe(true);
    expect(scopeCovered("tenant:acme:view", ["tenant:*:view"])).toBe(true);
    expect(scopeCovered("tenant:acme:view", ["tenant:*"])).toBe(false);
  });
});
