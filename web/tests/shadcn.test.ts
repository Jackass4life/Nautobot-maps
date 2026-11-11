// shadcn-vue next to Nuxt UI (#640): its colours are Nuxt UI's, never its own.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { cn } from "~/lib/utils";

const WEB = join(__dirname, "..");
const CSS = readFileSync(join(WEB, "assets/css/shadcn.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

function block(selector: string): Record<string, string> {
  const start = CSS.indexOf(`${selector} {`);
  expect(start, `${selector} in shadcn.css`).toBeGreaterThanOrEqual(0);
  const body = CSS.slice(start, CSS.indexOf("}", start));
  return Object.fromEntries([...body.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
}

// Colour classes the components in components/ui/ use (bg-muted, text-muted-foreground, ...).
function usedColours(): Set<string> {
  const used = new Set<string>();
  const dir = join(WEB, "components/ui");
  for (const entry of readdirSync(dir, { recursive: true, encoding: "utf8" })) {
    if (!entry.endsWith(".vue")) continue;
    const source = readFileSync(join(dir, entry), "utf8");
    for (const m of source.matchAll(/\b(?:bg|text|border|ring|outline|fill|stroke)-([a-z]+(?:-foreground)?)\b/g)) {
      used.add(m[1]);
    }
  }
  return used;
}

describe("shadcn's colour tokens", () => {
  const tokens = block(":root");
  const theme = block("@theme inline");

  it("are each one of Nuxt UI's variables", () => {
    expect(Object.keys(tokens).length).toBeGreaterThan(20);
    for (const [name, value] of Object.entries(tokens)) {
      expect(value, name).toMatch(/^var\(--ui-[\w-]+\)$/);
    }
  });

  it("hold no colour of their own, in light or dark", () => {
    expect(CSS).not.toMatch(/#[0-9a-f]{3,8}\b|\b(?:oklch|rgb|hsl)a?\(/i);
    // Dark mode is Nuxt UI's .dark, which switches the --ui-* variables.
    expect(CSS).not.toMatch(/\.dark\b|prefers-color-scheme/);
  });

  it("leave Nuxt UI's own theme names alone", () => {
    for (const name of Object.keys(theme)) {
      expect(name, "Nuxt UI's: change ui-colors.ts").not.toMatch(
        /^--color-(primary|secondary|success|warning|error|info|neutral)(-\d+)?$|^--radius-/,
      );
      expect(theme[name]).toBe(`var(${name.replace("--color-", "--")})`);
      expect(tokens, name).toHaveProperty(name.replace("--color-", "--"));
    }
  });

  it("cover every colour the added components use", () => {
    const nuxtUi = ["primary", "secondary", "success", "warning", "error", "info", "default", "transparent", "current"];
    const missing = [...usedColours()].filter((c) => !nuxtUi.includes(c) && !(`--color-${c}` in theme));
    // Font sizes (text-sm), sides (border-b) and alignment share the prefixes: not colours.
    expect(missing.filter((c) => !/^(xs|sm|base|lg|xl|[tblrxy]|left|right|center|foreground)$/.test(c))).toEqual([]);
  });
});

describe("shadcn's base border colour", () => {
  it("covers each added component's elements, and only those", () => {
    const rule = CSS.slice(CSS.indexOf("@layer base"));
    const prefixes = [...rule.slice(0, rule.indexOf("}")).matchAll(/\[data-slot\^="([\w-]+)"\]/g)].map((m) => m[1]);
    expect(prefixes.length).toBeGreaterThan(0);
    // Not every [data-slot]: Nuxt UI's elements have one too.
    expect(rule.slice(0, rule.indexOf("}"))).not.toMatch(/\[data-slot\](?!\^)/);
    const dir = join(WEB, "components/ui");
    for (const entry of readdirSync(dir, { recursive: true, encoding: "utf8" })) {
      if (!entry.endsWith(".vue")) continue;
      for (const m of readFileSync(join(dir, entry), "utf8").matchAll(/data-slot="([\w-]+)"/g)) {
        expect(prefixes.some((p) => m[1].startsWith(p)), `${m[1]} (${entry}): add it to shadcn.css's @layer base`).toBe(true);
      }
    }
  });
});

describe("cn()", () => {
  it("joins classes and lets the later of two clashing ones win", () => {
    expect(cn("p-2", false, "text-sm", { hidden: false, block: true })).toBe("p-2 text-sm block");
    expect(cn("p-2 bg-muted", "p-4")).toBe("bg-muted p-4");
  });
});
