// `npm audit` for the frontend (#463), failing on high or critical findings
// except the advisories below.  Only the built static files reach production:
// no Nuxt server, dev server or build tool runs there.  Each accepted
// advisory has no fixed release yet (or one too new to take) and says why it
// can't be reached here; remove it when the fix is in (Dependabot proposes it).
import { execFileSync } from "node:child_process";

const ACCEPTED = {
  // node-forge <= 1.4.0: RSA PKCS#1 v1.5 signature check (no fix released),
  // then PEM decoding, PKCS#12 MAC, TLS records and X.509 basicConstraints
  // (#551; fixed in 1.4.1/1.4.2 of 2026-10-27, too new to take yet).  Only
  // listhen uses it, for `nuxt dev --https`'s self-signed certificate:
  // development only, nothing is verified.
  "GHSA-86w9-cpqp-85rv": "dev server's self-signed certificate only",
  "GHSA-7wch-p9px-956x": "dev server's self-signed certificate only",
  "GHSA-jc66-7q78-782g": "dev server's self-signed certificate only",
  "GHSA-8hrf-8g6f-7xwp": "dev server's self-signed certificate only",
  "GHSA-hqq3-7jhf-c5jx": "dev server's self-signed certificate only",
  "GHSA-5x38-2vff-x55p": "dev server's self-signed certificate only",
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
