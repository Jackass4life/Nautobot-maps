// The board's sites with missing or wrong coordinates (#368): a line under
// the map's count, and the list behind its Check button.
import { nautobotLocationUrl } from "../lib/format.js";
import { escHtml } from "../lib/html.js";
import { getJson } from "../lib/http.js";
import { scopeParams } from "../lib/scope.js";
import { showError } from "../lib/toast.js";
import { openLocationById } from "./inspector.js";
import { config, els } from "./state.js";

// The list shows this many; the CSV has every one.
export const COORDINATE_LIST_LIMIT = 50;
// A screen left open picks up fixes made in Nautobot.
export const COORDINATE_REFRESH_MS = 15 * 60 * 1000;

// The page's ?tenant= / ?q= (#367), for the check and its CSV: a map for
// some sites only lists theirs.
export function coordinateQuery(scope) {
  const params = scopeParams(scope).toString();
  return params ? `?${params}` : "";
}

const plural = (n, one, many) => `${n} site${n === 1 ? "" : "s"} ${n === 1 ? one : many}`;
const WRONG = new Set(["invalid", "zero", "swapped"]);

// "12 sites aren't on the map · 2 have wrong coordinates", or "" when all is
// well.  "Far" is a hint, not an error (#416): said only when nothing is wrong.
export function coordinateSummary(sites) {
  const missing = sites.filter((item) => item.problem === "missing").length;
  const wrong = sites.filter((item) => WRONG.has(item.problem)).length;
  const far = sites.length - missing - wrong;
  const parts = [];
  if (missing) parts.push(plural(missing, "isn't on the map", "aren't on the map"));
  if (wrong) parts.push(missing ? `${wrong} ${wrong === 1 ? "has" : "have"} wrong coordinates` : plural(wrong, "has wrong coordinates", "have wrong coordinates"));
  if (!parts.length && far) parts.push(plural(far, "is far from the others", "are far from the others"));
  return parts.join(" · ");
}

function siteCell(item) {
  const name = escHtml(item.name || item.site_id);
  if (!config.nautobotUrl) return name;
  const href = nautobotLocationUrl(config.nautobotUrl, item.site_id);
  return `<a href="${escHtml(href)}" target="_blank" rel="noopener noreferrer" title="Fix it in Nautobot">${name} ↗</a>`;
}

// The list: worst first (the server's order), the first COORDINATE_LIST_LIMIT;
// the "far" hints last, under their own quiet heading (#416).
export function coordinateListHtml(sites) {
  const shown = sites.slice(0, COORDINATE_LIST_LIMIT);
  const firstFar = shown.findIndex((item) => item.problem === "far");
  const rows = shown.map((item) => {
    const where = item.latitude == null ? "" : ` (${item.latitude}, ${item.longitude})`;
    // Show only for a site on the map: none with no or impossible coordinates.
    const show = item.latitude == null || item.problem === "invalid"
      ? ""
      : ` <button class="coordinate-show" type="button" data-site-id="${escHtml(item.site_id)}">Show</button>`;
    const path = item.ancestor_path ? `<span class="coordinate-path">${escHtml(item.ancestor_path)}</span>` : "";
    return `<li><div class="coordinate-site">${siteCell(item)}${show}</div>${path}`
      + `<div class="coordinate-reason">${escHtml(item.reason)}${escHtml(where)}</div></li>`;
  });
  if (firstFar >= 0) {
    rows.splice(firstFar, 0, '<li class="coordinate-group">Far from the other sites in their region: worth a check</li>');
  }
  const more = sites.length > shown.length
    ? `<li class="coordinate-more">+${sites.length - shown.length} more in the CSV</li>`
    : "";
  const csv = `/api/locations/coordinate-check.csv${coordinateQuery(config.scope)}`;
  return `<ul class="coordinate-items">${rows.join("")}${more}</ul>`
    + `<a class="coordinate-csv" href="${escHtml(csv)}">Download CSV</a>`;
}

export function renderCoordinateCheck(sites) {
  const { coordinateNote, coordinateText, coordinateList } = els;
  if (!coordinateNote) return;
  const text = coordinateSummary(sites);
  coordinateNote.hidden = !text;
  if (!text) return;
  coordinateText.textContent = text;
  coordinateList.innerHTML = coordinateListHtml(sites);
}

// Once the locations are in; a failure leaves the line hidden (the map works).
export async function loadCoordinateCheck() {
  try {
    const data = await getJson(`/api/locations/coordinate-check${coordinateQuery(config.scope)}`);
    renderCoordinateCheck(data.sites || []);
  } catch (err) {
    console.warn("Coordinate check unavailable:", err);
  }
}

// The Check button opens the list; Show opens a misplaced site on the map.
export function initCoordinateCheck() {
  const { coordinateToggle, coordinateList } = els;
  if (!coordinateToggle || !coordinateList) return;
  coordinateToggle.addEventListener("click", () => {
    const open = coordinateList.hidden;
    coordinateList.hidden = !open;
    coordinateToggle.setAttribute("aria-expanded", open ? "true" : "false");
  });
  coordinateList.addEventListener("click", (event) => {
    const button = event.target.closest(".coordinate-show");
    // Not drawn now: a sidebar filter or Alarms only hides it.
    if (button && !openLocationById(button.dataset.siteId, { focusInspector: true })) {
      showError("That site isn't shown on the map now: clear the filters (or Alarms only) to see it.");
    }
  });
}
