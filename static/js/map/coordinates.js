// The board's sites with missing or wrong coordinates (#368): a line under
// the map's count, and the list behind its Check button.
import { escHtml } from "../lib/html.js";
import { getJson } from "../lib/http.js";
import { inScope } from "../lib/scope.js";
import { openLocationById } from "./inspector.js";
import { config, els } from "./state.js";

// The list shows this many; the CSV has every one.
export const COORDINATE_LIST_LIMIT = 50;

// What a problem item matches by, for a map for some sites only (#367).
function asSite(item) {
  return { ...item, name: item.site, ancestor_path: item.path };
}

// "12 sites aren't on the map · 2 may be misplaced", or "" when all is well.
export function coordinateSummary(sites) {
  const missing = sites.filter((item) => item.problem === "missing").length;
  const misplaced = sites.length - missing;
  const parts = [];
  if (missing) parts.push(`${missing} site${missing === 1 ? "" : "s"} ${missing === 1 ? "isn't" : "aren't"} on the map`);
  if (misplaced) parts.push(`${misplaced} may be misplaced`);
  return parts.join(" · ");
}

function siteCell(item) {
  const name = escHtml(item.site || item.site_id);
  if (!config.nautobotUrl) return name;
  const href = `${config.nautobotUrl}/dcim/locations/${encodeURIComponent(item.site_id)}/`;
  return `<a href="${escHtml(href)}" target="_blank" rel="noopener noreferrer" title="Fix it in Nautobot">${name} ↗</a>`;
}

// The list: worst first (the server's order), the first COORDINATE_LIST_LIMIT.
export function coordinateListHtml(sites) {
  const shown = sites.slice(0, COORDINATE_LIST_LIMIT);
  const rows = shown.map((item) => {
    const where = item.latitude == null ? "" : ` (${item.latitude}, ${item.longitude})`;
    const show = item.latitude == null
      ? ""
      : ` <button class="coordinate-show" type="button" data-site-id="${escHtml(item.site_id)}">Show</button>`;
    const path = item.path ? `<span class="coordinate-path">${escHtml(item.path)}</span>` : "";
    return `<li><div class="coordinate-site">${siteCell(item)}${show}</div>${path}`
      + `<div class="coordinate-reason">${escHtml(item.reason)}${escHtml(where)}</div></li>`;
  });
  const more = sites.length > shown.length
    ? `<li class="coordinate-more">+${sites.length - shown.length} more in the CSV</li>`
    : "";
  return `<ul class="coordinate-items">${rows.join("")}${more}</ul>`
    + `<a class="coordinate-csv" href="/api/locations/coordinate-check.csv">Download CSV</a>`;
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
    const data = await getJson("/api/locations/coordinate-check");
    renderCoordinateCheck((data.sites || []).filter((item) => inScope(asSite(item), config.scope)));
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
    if (button) openLocationById(button.dataset.siteId, { focusInspector: true });
  });
}
