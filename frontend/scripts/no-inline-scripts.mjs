// After `nuxt generate` (#463): the CSP allows no inline script
// (script-src 'self'), so Nuxt's runtime config moves from the pages into a
// file under /_nuxt/, named by its content (cached for a year, like the rest).
// Any other inline script left fails the build.  JSON data blocks
// (type="application/json") are never run, so they stay.
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const PUBLIC = new URL("../.output/public/", import.meta.url).pathname;
const INLINE = /<script(?![^>]*\bsrc=)([^>]*)>([\s\S]*?)<\/script>/g;

let failed = false;
for (const name of readdirSync(PUBLIC).filter((file) => file.endsWith(".html"))) {
  const path = join(PUBLIC, name);
  const html = readFileSync(path, "utf8").replace(INLINE, (whole, attrs, body) => {
    if (/type="application\/json"/.test(attrs)) return whole;
    if (body.startsWith("window.__NUXT__=")) {
      const file = `nuxt-config.${createHash("sha256").update(body).digest("hex").slice(0, 12)}.js`;
      writeFileSync(join(PUBLIC, "_nuxt", file), `${body}\n`);
      return `<script src="/_nuxt/${file}"></script>`;
    }
    console.error(`${name}: an inline script the CSP would block: ${whole.slice(0, 120)}`);
    failed = true;
    return whole;
  });
  writeFileSync(path, html);
}
if (failed) process.exit(1);
console.log("No inline scripts left in the pages (CSP).");
