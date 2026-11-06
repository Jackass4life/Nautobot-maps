// `npm audit` for the frontend (#463), failing on high or critical findings
// except the advisories below.  Only the built static files reach production:
// no Nuxt server, dev server or build tool runs there.  Each accepted
// advisory has no fixed release yet and says why it can't be reached here;
// remove it when a fix lands (Dependabot proposes it).
import { execFileSync } from "node:child_process";

const ACCEPTED = {};

let report;
try {
  report = JSON.parse(execFileSync("npm", ["audit", "--json"], { encoding: "utf8" }));
} catch (error) {
  // npm audit exits non-zero when it finds anything; its report is on stdout.
  report = JSON.parse(error.stdout);
}

// An error instead of a report (a registry without audits: ENOAUDIT, #610 review)
// audited nothing: that fails too, never "nothing found".
if (report.error || typeof report.vulnerabilities !== "object" || report.vulnerabilities === null) {
  console.log(`npm audit gave no report: ${JSON.stringify(report.error ?? report).slice(0, 500)}`);
  process.exit(1);
}

const advisories = new Map();
for (const finding of Object.values(report.vulnerabilities)) {
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
