// `bun audit` for the frontend (#463, bun since #635), failing on high or
// critical findings except the advisories below.  What reaches production is the
// built pages and the web image's Nitro server (#672), which bundles only what it
// runs (.output/server/node_modules): no dev server or build tool runs there.  Each
// accepted advisory has no fixed release yet and says why it can't be reached
// here; remove it when a fix lands (Dependabot proposes it).  bun.lock is enough:
// no install needed.
import { execFileSync } from "node:child_process";

const ACCEPTED = {
  // #756: picomatch <= 4.0.7, ReDoS through repeated extglobs; no fixed release yet.
  // Only the build and dev tools use it (tinyglobby, fdir, micromatch, anymatch),
  // on glob patterns from our own config; the Nitro server bundles none of them.
  "GHSA-fc5q-39xc-vjpf":
    "picomatch is only in the build and dev tools, matching our own glob patterns",
};

let output;
try {
  output = execFileSync("bun", ["audit", "--json"], { encoding: "utf8" });
} catch (error) {
  // bun audit exits non-zero when it finds anything; its report is on stdout.
  output = error.stdout ?? "";
}

// The report maps each package to its advisories. Anything else (no report, a
// registry without audits, #610 review) audited nothing: that fails too, never
// "nothing found".
let report;
try {
  report = JSON.parse(output);
} catch {
  report = null;
}
const isReport =
  typeof report === "object" &&
  report !== null &&
  !Array.isArray(report) &&
  Object.values(report).every(Array.isArray);
if (!isReport) {
  console.log(`bun audit gave no report: ${String(output).slice(0, 500)}`);
  process.exit(1);
}

const advisories = new Map();
for (const [name, found] of Object.entries(report)) {
  for (const advisory of found) {
    advisories.set(advisory.url.split("/").pop(), {
      ...advisory,
      name,
      range: advisory.vulnerable_versions,
    });
  }
}

let failed = false;
for (const [id, advisory] of advisories) {
  const serious = advisory.severity === "high" || advisory.severity === "critical";
  const note = ACCEPTED[id] ? `accepted: ${ACCEPTED[id]}` : serious ? "FAILS" : "below high";
  console.log(
    `${advisory.severity.padEnd(8)} ${id} ${advisory.name} ${advisory.range}: ${advisory.title} (${note})`,
  );
  if (serious && !ACCEPTED[id]) failed = true;
}
for (const id of Object.keys(ACCEPTED)) {
  if (!advisories.has(id))
    console.log(`${id} is no longer found: remove it from ACCEPTED in scripts/audit.mjs`);
}
console.log(
  failed ? "High or critical advisories not accepted." : "No unaccepted high or critical advisory.",
);
process.exit(failed ? 1 : 0);
