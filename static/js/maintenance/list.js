// The Maintenance page's list (#343): windows planned together are one
// batch, so a change across 30 sites is one line; groups Active now,
// Planned and Ended, each the first N with "Show more".  No page access at import.
import { tenantCell } from "../alerts/rows.js";
import { formatFeedTime } from "../lib/format.js";
import { escHtml } from "../lib/html.js";

export const GROUPS = [
  { id: "active", title: "Active now", empty: "Nothing in maintenance now." },
  { id: "upcoming", title: "Planned", empty: "Nothing planned." },
  { id: "past", title: "Ended in the last 7 days", empty: "Nothing ended lately." },
];

const BADGES = { active: "ACTIVE", upcoming: "PLANNED", ended: "ENDED", cancelled: "CANCELLED" };

function groupOf(state) {
  return state === "active" || state === "upcoming" ? state : "past";
}

// Windows created in one request (same created_at, times, reason and person) are one batch.
export function batchesOf(windows) {
  const batches = new Map();
  for (const window of windows) {
    const group = groupOf(window.state);
    const key = [group, window.created_at, window.starts_at, window.ends_at, window.reason, window.created_by].join("|");
    if (!batches.has(key)) {
      batches.set(key, {
        key, group, state: window.state, windows: [],
        starts_at: window.starts_at, ends_at: window.ends_at, ended_at: window.ended_at,
        reason: window.reason || "", created_by: window.created_by || "",
      });
    }
    batches.get(key).windows.push(window);
  }
  // Ending soonest first; planned soonest first; ended latest first.
  const time = (batch) => Date.parse(batch.group === "active" ? batch.ends_at
    : batch.group === "upcoming" ? batch.starts_at : batch.ended_at || batch.ends_at);
  return [...batches.values()].sort((a, b) => (a.group === "past" ? time(b) - time(a) : time(a) - time(b)));
}

export function batchTenants(batch) {
  return [...new Set(batch.windows.flatMap((window) => window.tenants || []))].sort();
}

function siteLabel(window) {
  return window.site_name || window.site_id;
}

// Search: reason, person, sites, paths, devices and tenants, every word; and one tenant.
export function batchMatches(batch, { query = "", tenant = "" }) {
  if (tenant && !batchTenants(batch).includes(tenant)) return false;
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const text = [batch.reason, batch.created_by, ...batchTenants(batch),
    ...batch.windows.flatMap((w) => [siteLabel(w), w.site_path, w.device_name || w.device_id])].join(" ").toLowerCase();
  return words.every((word) => text.includes(word));
}

// "Aarhus", "Aarhus · sw1", "3 devices at Aarhus", "30 sites".
export function batchTitle(batch) {
  const { windows } = batch;
  const sites = [...new Set(windows.map(siteLabel))];
  if (windows.length === 1) {
    const [window] = windows;
    return window.device_id ? `${siteLabel(window)} · ${window.device_name || window.device_id}` : siteLabel(window);
  }
  if (sites.length === 1) return `${windows.length} devices at ${sites[0]}`;
  return `${sites.length} sites`;
}

export function whenText(batch, now = new Date()) {
  if (batch.state === "cancelled") return "cancelled";
  if (batch.group === "past") return `ended ${formatFeedTime(batch.ended_at || batch.ends_at, now)}`;
  if (batch.group === "active") return `until ${formatFeedTime(batch.ends_at, now)}`;
  return `${formatFeedTime(batch.starts_at, now)} – ${formatFeedTime(batch.ends_at, now)}`;
}

function endLabel(batch, count) {
  const verb = batch.group === "upcoming" ? "Cancel" : "End";
  return count > 1 ? `${verb} all ${count}` : batch.group === "upcoming" ? "Cancel" : "End now";
}

function windowHtml(batch, window, descriptions) {
  const what = window.device_id ? ` · ${escHtml(window.device_name || window.device_id)}` : "";
  const end = batch.group === "past" ? ""
    : `<button class="action-btn maint-end" type="button" data-ids="${window.id}">${batch.group === "upcoming" ? "Cancel" : "End now"}</button>`;
  return `<li class="maint-batch-window"><span>${escHtml(siteLabel(window))}${what}</span>`
    + `<span class="maint-batch-tenants">${tenantCell({ tenants: window.tenants || [], tenant_descriptions: descriptions })}</span>${end}</li>`;
}

// One batch; *expanded*: its sites or devices listed, each with its own End.
export function batchHtml(batch, { descriptions = {}, expanded = false, now = new Date() } = {}) {
  const { windows } = batch;
  const ids = windows.map((window) => window.id).join(",");
  const many = windows.length > 1;
  const names = [...new Set(windows.map(siteLabel))];
  const preview = many && names.length > 1
    ? `<div class="maint-batch-sites">${escHtml(names.slice(0, 3).join(", "))}${names.length > 3 ? ` +${names.length - 3}` : ""}</div>` : "";
  const end = batch.group === "past" ? ""
    : `<button class="action-btn maint-end" type="button" data-ids="${ids}">${endLabel(batch, windows.length)}</button>`;
  const toggle = many
    ? `<button class="action-btn maint-expand" type="button" aria-expanded="${expanded}">${expanded ? "Hide" : "Show"} ${windows.length}</button>` : "";
  const by = batch.created_by ? ` · by ${escHtml(batch.created_by)}` : "";
  const tenants = batchTenants(batch);
  return `
    <li class="maint-batch" data-key="${escHtml(batch.key)}">
      <div class="maint-batch-main">
        <span class="alert-badge alert-maintenance">${BADGES[batch.state] || "PLANNED"}</span>
        <div class="maint-batch-text">
          <div><strong>${escHtml(batchTitle(batch))}</strong> · ${escHtml(whenText(batch, now))}</div>
          <div class="maint-batch-meta">${escHtml(batch.reason)}${by}</div>
          ${preview}
          ${tenants.length ? `<div class="maint-batch-tenants">${tenantCell({ tenants, tenant_descriptions: descriptions })}</div>` : ""}
        </div>
        <div class="maint-batch-actions">${toggle}${end}</div>
      </div>
      ${expanded ? `<ul class="maint-batch-windows">${windows.map((w) => windowHtml(batch, w, descriptions)).join("")}</ul>` : ""}
    </li>`;
}

// The three groups; *limits* {group: N shown}, *expanded* a Set of batch keys.
export function groupsHtml(batches, { limits, expanded, descriptions, now = new Date(), step = 25, pastOpen = false }) {
  return GROUPS.map((group) => {
    const members = batches.filter((batch) => batch.group === group.id);
    const limit = limits[group.id] || step;
    const shown = members.slice(0, limit);
    const rest = members.length - shown.length;
    const sites = new Set(members.flatMap((batch) => batch.windows.map((w) => w.site_id))).size;
    const count = members.length ? ` <span class="avail-count">${members.length} · ${sites} site${sites === 1 ? "" : "s"}</span>` : "";
    const list = members.length
      ? `<ul class="maint-batches">${shown.map((batch) => batchHtml(batch, { descriptions, expanded: expanded.has(batch.key), now })).join("")}</ul>`
      : `<p class="maint-empty">${group.empty}</p>`;
    const more = rest > 0
      ? `<p class="avail-more">${rest} more <button class="avail-more-btn maint-more" type="button" data-group="${group.id}">Show ${Math.min(step, rest)} more</button></p>` : "";
    const heading = `<h2 class="availability-heading">${group.title}${count}</h2>`;
    // Ended work is history: folded unless asked for (*open*: the operator opened it).
    return group.id === "past"
      ? `<details class="table-shell maint-group" data-group="past"${pastOpen ? " open" : ""}><summary class="maint-group-summary">${heading}</summary>${list}${more}</details>`
      : `<section class="table-shell maint-group" data-group="${group.id}">${heading}${list}${more}</section>`;
  }).join("");
}
