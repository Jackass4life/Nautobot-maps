// The pre-rendered pages without inline scripts (#594, #672): the CSP allows none
// (script-src 'self'), so Nuxt's runtime config (`window.__NUXT__=…`) moves from each
// page into a file under /_nuxt/, named by its content and cached for a year like the
// rest. Done as each page is rendered, so the web image's server (`bun run build`)
// knows the file and the page's final size; `bun run generate` gets the same pages.
// scripts/no-inline-scripts.mjs then checks no other inline script is left.
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { defineNuxtModule } from "@nuxt/kit";

const INLINE = /<script(?![^>]*\bsrc=)([^>]*)>([\s\S]*?)<\/script\s*>/gi;

/** *html* with the inline config script as a file of *assets*; `[html, files]`. */
export function externalConfig(html: string): [string, Record<string, string>] {
  const files: Record<string, string> = {};
  const out = html.replace(INLINE, (whole, attrs: string, body: string) => {
    if (!body.startsWith("window.__NUXT__=")) return whole;
    const name = `nuxt-config.${createHash("sha256").update(body).digest("hex").slice(0, 12)}.js`;
    files[name] = `${body}\n`;
    return `<script src="/_nuxt/${name}"></script>`;
  });
  return [out, files];
}

export default defineNuxtModule({
  meta: { name: "csp-shell" },
  setup(_options, nuxt) {
    nuxt.hook("nitro:init", (nitro) => {
      nitro.hooks.hook("prerender:generate", (route) => {
        if (typeof route.contents !== "string" || !route.fileName?.endsWith(".html")) return;
        const [html, files] = externalConfig(route.contents);
        route.contents = html;
        const dir = join(nitro.options.output.publicDir, "_nuxt");
        mkdirSync(dir, { recursive: true });
        for (const [name, body] of Object.entries(files)) writeFileSync(join(dir, name), body);
      });
    });
  },
});
