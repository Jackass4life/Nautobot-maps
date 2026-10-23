// `npm audit` for the frontend (#463), failing on high or critical findings
// except the advisories below.  Only the built static files reach production:
// no Nuxt server, dev server or build tool runs there.  Each accepted
// advisory has no fixed release yet and says why it can't be reached here;
// remove it when a fix lands (Dependabot proposes it).
import { execFileSync } from "node:child_process";

const ACCEPTED = {
  // braces <= 3.0.3 (no fix released): stack exhaustion on deeply nested
  // brace patterns.  Used by the build's file globbing (micromatch,
  // fast-glob), on patterns from our own config, never from a user.
  "GHSA-vfj7-8cjw-p6xm": "build-time globbing of our own patterns",
  // node-forge <= 1.4.0 (no fix released): RSA PKCS#1 v1.5 signature check.
  // Only listhen uses it, for `nuxt dev --https`'s self-signed certificate:
  // development only, nothing is verified.
  "GHSA-86w9-cpqp-85rv": "dev server's self-signed certificate only",
};

let report;
try {
  report = JSON.parse(execFileSync("npm", ["audit", "--json"], { encoding: "utf8" }));
} catch (error) {
  // npm audit exits non-zero when it finds anything; its report is on stdout.
  report = JSON.parse(error.stdout);
}

const advisories = new Map();
for (const finding of Object.values(report.vulnerabilities ?? {})) {
  for (const via of finding.via) {
    if (typeof via === "object") advisories.set(via.url.split("/").pop(), via);
  }
}

let failed = false;
for (const [id, advisory] of advisories) {
  const serious = advisory.severity === "high" || advisory.severity === "critical";
  const note = ACCEPTED[id] ? `accepted: ${ACCEPTED[id]}` : serious ? "FAILS" : "below high";
  console.log(`${advisory.severity.padEnd(8)} ${id} ${advisory.name} ${advisory.range}: ${advisory.title} (${note})`);
  if (serious && !ACCEPTED[id]) failed = true;
}
for (const id of Object.keys(ACCEPTED)) {
  if (!advisories.has(id)) console.log(`${id} is no longer found: remove it from ACCEPTED in scripts/audit.mjs`);
}
console.log(failed ? "High or critical advisories not accepted." : "No unaccepted high or critical advisory.");
process.exit(failed ? 1 : 0);
