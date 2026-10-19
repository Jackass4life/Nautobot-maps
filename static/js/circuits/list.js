// The Circuit mapping page's list (#385): circuits without a termination,
// grouped by what their address found, each group folded with a count and
// the first N, "Show N more".  No page access at import.
import { hasAllWords, nautobotCircuitUrl, nautobotLocationUrl } from "../lib/format.js";
import { escHtml } from "../lib/html.js";

// Circuits shown per group, and per "Show more".
export const STEP = 100;

// Worst first: a choice to make, then nothing found, then ready to terminate.
export const GROUPS = [
  { id: "several", title: "Several sites", hint: "The address belongs to more than one site: choose where to terminate it." },
  { id: "none", title: "No site", hint: "The description isn't an address of any site: a different address, more text, or no address." },
  { id: "one", title: "One site", hint: "Ready: add the termination at this site." },
];

// Lower-cased text a search looks in, worked out once per circuit.
export function prepare(circuits) {
  return circuits.map((circuit) => ({
    ...circuit,
    text: [circuit.cid, circuit.provider, circuit.circuit_type, circuit.status, circuit.tenant, circuit.description,
      ...(circuit.sites || []).map((site) => site.name)].join(" ").toLowerCase(),
  }));
}

export function matches(circuit, words) {
  return hasAllWords(circuit.text, words);
}

function circuitLink(circuit, nautobotUrl) {
  return nautobotUrl && circuit.id
    ? `<a class="circuit-link" href="${escHtml(nautobotCircuitUrl(nautobotUrl, circuit.id))}" target="_blank" rel="noopener noreferrer">${escHtml(circuit.cid)}</a>`
    : escHtml(circuit.cid);
}

// A site opens in the map's panel; one not on the map (no coordinates) in Nautobot.
function siteLink(site, nautobotUrl) {
  if (site.on_map) return `<a class="circuit-site" href="/?location_id=${encodeURIComponent(site.id)}">${escHtml(site.name)}</a>`;
  return nautobotUrl
    ? `<a class="circuit-site" href="${escHtml(nautobotLocationUrl(nautobotUrl, site.id))}" target="_blank" rel="noopener noreferrer" title="Not on the map: no coordinates">${escHtml(site.name)}</a>`
    : escHtml(site.name);
}

export function circuitHtml(circuit, nautobotUrl = "") {
  const kind = [circuit.provider, circuit.circuit_type, circuit.commit_rate, circuit.tenant].filter(Boolean).map(escHtml).join(" · ");
  const sites = circuit.sites || [];
  return `
    <li class="maint-batch circuit-row">
      <div class="maint-batch-main">
        <div class="maint-batch-text">
          <div><strong>${circuitLink(circuit, nautobotUrl)}</strong>${circuit.status ? ` · ${escHtml(circuit.status)}` : ""}</div>
          ${kind ? `<div class="maint-batch-meta">${kind}</div>` : ""}
          <div class="maint-batch-meta">${circuit.description ? escHtml(circuit.description) : "No description"}</div>
        </div>
        <div class="circuit-sites">${sites.length ? sites.map((site) => siteLink(site, nautobotUrl)).join(", ") : "—"}</div>
      </div>
    </li>`;
}

// The three groups of *circuits* (prepared, already searched); *limits*
// {group: N shown}; *open* the ids of the groups unfolded.
export function groupsHtml(circuits, { limits = {}, open = new Set(), step = STEP, nautobotUrl = "" } = {}) {
  return GROUPS.map((group) => {
    const members = circuits.filter((circuit) => circuit.group === group.id);
    const limit = limits[group.id] || step;
    const shown = members.slice(0, limit);
    const rest = members.length - shown.length;
    const list = members.length
      ? `<ul class="maint-batches">${shown.map((circuit) => circuitHtml(circuit, nautobotUrl)).join("")}</ul>`
      : `<p class="maint-empty">None.</p>`;
    const more = rest > 0
      ? `<p class="avail-more">${rest} more <button class="avail-more-btn circuit-more" type="button" data-group="${group.id}">Show ${Math.min(step, rest)} more</button></p>` : "";
    const heading = `<h2 class="availability-heading">${group.title} <span class="avail-count">${members.length}</span></h2>`;
    return `<details class="table-shell maint-group" data-group="${group.id}"${open.has(group.id) ? " open" : ""}>`
      + `<summary class="maint-group-summary">${heading}</summary><p class="maint-scope-note">${group.hint}</p>${list}${more}</details>`;
  }).join("");
}

// "312 circuits without a termination: 12 several sites · 60 no site · 240 one site".
export function summaryText(summary, shown = null) {
  const total = summary.total || 0;
  if (!total) return "Every circuit has a termination at a location.";
  const parts = `${summary.several || 0} several sites · ${summary.none || 0} no site · ${summary.one || 0} one site`;
  const found = shown === null || shown === total ? "" : ` · ${shown} match the search`;
  return `${total} circuit${total === 1 ? "" : "s"} without a termination: ${parts}${found}`;
}
