// The board's sites with missing or wrong coordinates (#368): a line under
// the map's count, and the list behind its Check button.
import { escHtml } from "../lib/html.js";
import { getJson } from "../lib/http.js";
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
  if (!scope) return "";
  const params = new URLSearchParams();
  scope.tenants.forEach((name) => params.append("tenant", name));
  scope.texts.forEach((text) => params.append("q", text));
  return `?${params}`;
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
  const name = escHtml(item.name || item.site_id);
  if (!config.nautobotUrl) return name;
  const href = `${config.nautobotUrl}/dcim/locations/${encodeURIComponent(item.site_id)}/`;
  return `<a href="${escHtml(href)}" target="_blank" rel="noopener noreferrer" title="Fix it in Nautobot">${name} ↗</a>`;
}

// The list: worst first (the server's order), the first COORDINATE_LIST_LIMIT.
export function coordinateListHtml(sites) {
  const shown = sites.slice(0, COORDINATE_LIST_LIMIT);
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
