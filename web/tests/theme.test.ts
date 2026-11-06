// Light or dark (#463): the old pages' stored choice, else the system's.
import { describe, expect, it } from "vitest";
import { colorsCss } from "../modules/static-ui-colors";
import { UI_COLORS } from "../ui-colors";

describe("the theme", async () => {
  // The plugin's file defines a Nuxt plugin; only its pure function is tested.
  (globalThis as Record<string, unknown>).defineNuxtPlugin = (fn: unknown) => fn;
  const { THEME_KEY, wantsDark } = await import("~/plugins/theme.client");

  it("follows the stored choice, else the system", () => {
    expect(THEME_KEY).toBe("nautobot-maps-theme");
    expect(wantsDark("dark", false)).toBe(true);
    expect(wantsDark("light", true)).toBe(false);
    expect(wantsDark(null, true)).toBe(true);
    expect(wantsDark("odd", false)).toBe(false);
  });
});

describe("Nuxt UI's colours as a stylesheet (CSP: no inline style)", () => {
  const css = colorsCss({ ...UI_COLORS });

  it("every shade of every colour, with Tailwind's value as the fallback", () => {
    expect(css).toContain("--ui-color-primary-500: var(--color-blue-500, oklch(");
    expect(css).toContain("--ui-color-error-950: var(--color-red-950, oklch(");
    expect(css).toContain("--ui-color-neutral-50: var(--color-slate-50, oklch(");
  });

  it("500 in light, 400 in dark; neutral is not a semantic colour", () => {
    expect(css).toMatch(/:root, :host, \.light \{[^}]*--ui-success: var\(--ui-color-success-500\);/);
    expect(css).toMatch(/\.dark \{[^}]*--ui-warning: var\(--ui-color-warning-400\);/);
    expect(css).not.toContain("--ui-neutral:");
  });
});
