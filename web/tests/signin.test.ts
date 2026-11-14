// The sign-in page's address and where it goes back to (#717).
import { describe, expect, it } from "vitest";
import { safeRedirect, signInAddress } from "~/utils/signin";

describe("signInAddress", () => {
  it("names the page to come back to", () => {
    expect(signInAddress("/auth", "/alerts?tenant=Acme")).toBe(
      "/auth?redirect=%2Falerts%3Ftenant%3DAcme",
    );
  });
});

describe("safeRedirect", () => {
  it("a path on this site", () => {
    expect(safeRedirect("/noc/board?q=x")).toBe("/noc/board?q=x");
    expect(safeRedirect("/")).toBe("/");
  });
  it("never another site, nor back to sign-in", () => {
    for (const bad of [
      "//evil.example",
      "/\\evil.example",
      "https://evil.example",
      "javascript:alert(1)",
      "evil",
      "/\t/evil.example",
      "/auth",
      "/auth?redirect=%2F",
      undefined,
      ["/x"],
    ])
      expect(safeRedirect(bad)).toBe("/");
  });
});
