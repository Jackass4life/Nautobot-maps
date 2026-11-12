// After a build (#594, #672): the CSP allows no inline script (script-src 'self').
// modules/csp-shell.ts moves Nuxt's runtime config out of each page as it is
// rendered; any inline script left fails the build. JSON data blocks
// (type="application/json") are never run, so they may stay.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const PUBLIC = new URL("../.output/public/", import.meta.url).pathname;
const INLINE = /<script(?![^>]*\bsrc=)([^>]*)>([\s\S]*?)<\/script\s*>/gi;

let failed = false;
let pages = 0;
for (const name of readdirSync(PUBLIC, { recursive: true })) {
  if (!String(name).endsWith(".html")) continue;
  pages += 1;
  const html = readFileSync(join(PUBLIC, String(name)), "utf8");
  for (const [whole, attrs] of html.matchAll(INLINE)) {
    if (/type="application\/json"/i.test(attrs)) continue;
    console.error(`${name}: an inline script the CSP would block: ${whole.slice(0, 120)}`);
    failed = true;
  }
}
if (!pages) {
  console.error("No pages in .output/public: build first.");
  process.exit(1);
}
if (failed) process.exit(1);
console.log(`No inline scripts in the ${pages} pages (CSP).`);
