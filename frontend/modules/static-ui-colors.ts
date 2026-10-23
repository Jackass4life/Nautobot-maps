// Nuxt UI's colour variables as a stylesheet made at build time (#463), not
// the inline <style> its colours plugin adds in the browser: the CSP allows
// no inline style (style-src 'self').  The same CSS as that plugin
// (@nuxt/ui/dist/runtime/plugins/colors.js), from UI_COLORS.
import { addTemplate, defineNuxtModule } from "@nuxt/kit";
import palette from "tailwindcss/colors";
import { UI_COLORS } from "../ui-colors";

const SHADES = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950] as const;

// Tailwind's value, the fallback when the theme doesn't define the variable.
function hex(color: string, shade: number): string {
  const shades = (palette as unknown as Record<string, Record<number, string>>)[color];
  return shades?.[shade] ?? "";
}

export function colorsCss(colors: Record<string, string>): string {
  const { neutral: _neutral, ...semantic } = colors;
  const shades = Object.entries(colors).flatMap(([key, value]) =>
    SHADES.map(
      (shade) =>
        `--ui-color-${key}-${shade}: var(--color-${value === "neutral" ? "old-neutral" : value}-${shade}, ${hex(value, shade)});`,
    ),
  );
  const at = (shade: number) => Object.keys(semantic).map((key) => `--ui-${key}: var(--ui-color-${key}-${shade});`);
  return `@layer theme {
  :root, :host { ${shades.join(" ")} }
  :root, :host, .light { ${at(500).join(" ")} }
  .dark { ${at(400).join(" ")} }
}
`;
}

export default defineNuxtModule({
  meta: { name: "static-ui-colors" },
  setup(_options, nuxt) {
    const css = addTemplate({ filename: "ui-colors.css", write: true, getContents: () => colorsCss(UI_COLORS) });
    nuxt.options.css.push(css.dst);
    nuxt.hook("app:resolve", (app) => {
      app.plugins = app.plugins.filter((plugin) => !/@nuxt[\\/]ui[\\/].*plugins[\\/]colors/.test(plugin.src));
    });
  },
});
