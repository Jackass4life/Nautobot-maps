// The Maintenance page's list (#343): windows planned together are one
// batch, so a change across 30 sites is one line; groups Active now,
// Planned and Ended, each the first N with "Show more".  No page access at import.
import { tenantCell } from "../alerts/rows.js";
import { formatFeedTime, hasAllWords } from "../lib/format.js";
import { escHtml } from "../lib/html.js";

// *days*: how far back the Ended group reaches (the page's recent_days).
function groupsFor(days) {
  return [
    { id: "active", title: "Active now", empty: "Nothing in maintenance now." },
    { id: "upcoming", title: "Planned", empty: "Nothing planned." },
    { id: "past", title: `Ended in the last ${days} day${days === 1 ? "" : "s"}`, empty: "Nothing ended lately." },
  ];
}

const GROUP_ORDER = { active: 0, upcoming: 1, past: 2 };

const BADGES = { active: "ACTIVE", upcoming: "PLANNED", ended: "ENDED", cancelled: "CANCELLED" };

function groupOf(state) {
  return state === "active" || state === "upcoming" ? state : "past";
}

function siteLabel(window) {
  return window.site_name || window.site_id;
}

// Windows planned in one request (batch_id) are one batch; once over, the
// cancelled ones apart from those that ran.  A window from before batches is
// its own.  Each batch's tenants, sites and search text are worked out here
// once, not on every key.
// *keepOrder* (the history): as the server sent them, newest first.
export function batchesOf(windows, { keepOrder = false } = {}) {
  const batches = new Map();
  for (const window of windows) {
    const group = groupOf(window.state);
    const state = group === "past" ? window.state : group;
    const key = `${state}|${window.batch_id || `window-${window.id}`}`;
    if (!batches.has(key)) {
      batches.set(key, {
        key, group, state: window.state, windows: [],
        starts_at: window.starts_at, ends_at: window.ends_at, ended_at: window.ended_at,
        reason: window.reason || "", created_by: window.created_by || "",
      });
    }
    const batch = batches.get(key);
    batch.windows.push(window);
    // Ended at different times: the latest.
    if (window.ended_at && !(Date.parse(batch.ended_at) >= Date.parse(window.ended_at))) batch.ended_at = window.ended_at;
  }
  for (const batch of batches.values()) {
    batch.tenants = [...new Set(batch.windows.flatMap((window) => window.tenants || []))].sort();
    batch.sites = [...new Set(batch.windows.map(siteLabel))];
    batch.text = [batch.reason, batch.created_by, ...batch.tenants,
      ...batch.windows.flatMap((w) => [siteLabel(w), w.site_path, w.device_name || w.device_id])].join(" ").toLowerCase();
  }
  if (keepOrder) return [...batches.values()];
  // By group; then ending soonest first, planned soonest first, ended latest first.
  const time = (batch) => Date.parse(batch.group === "active" ? batch.ends_at
    : batch.group === "upcoming" ? batch.starts_at : batch.ended_at || batch.ends_at);
  return [...batches.values()].sort((a, b) => GROUP_ORDER[a.group] - GROUP_ORDER[b.group]
    || (a.group === "past" ? time(b) - time(a) : time(a) - time(b)));
}

// Search (*words* from searchWords): reason, person, sites, paths, devices and tenants; and one tenant.
export function batchMatches(batch, { words = [], tenant = "" }) {
  return (!tenant || batch.tenants.includes(tenant)) && hasAllWords(batch.text, words);
}

// "Aarhus", "Aarhus · sw1", "3 devices at Aarhus", "30 sites".
export function batchTitle(batch) {
  const { windows, sites } = batch;
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

// The history's (#353): when it ran, and whether it ended early.
// "22 Sep 22:00 – 23:00 · ended early", "cancelled · was planned 22 Sep 22:00",
// "since 22:00, until 02:00", "planned 24 Sep 22:00 – 02:00".
export function spanText(batch, now = new Date()) {
  const at = (value) => formatFeedTime(value, now);
  if (batch.state === "cancelled") return `cancelled · was planned ${at(batch.starts_at)}`;
  if (batch.group === "active") return `since ${at(batch.starts_at)}, until ${at(batch.ends_at)}`;
  if (batch.group === "upcoming") return `planned ${at(batch.starts_at)} – ${at(batch.ends_at)}`;
  const end = batch.ended_at || batch.ends_at;
  const early = batch.ended_at && Date.parse(batch.ended_at) < Date.parse(batch.ends_at) ? " · ended early" : "";
  return `${at(batch.starts_at)} – ${at(end)}${early}`;
}

// "End now", "Cancel", "End all 30", "Cancel all 30".
function endLabel(batch, count) {
  const verb = batch.group === "upcoming" ? "Cancel" : "End";
  if (count > 1) return `${verb} all ${count}`;
  return batch.group === "upcoming" ? "Cancel" : "End now";
}

function windowHtml(batch, window, descriptions, actions) {
  const what = window.device_id ? ` · ${escHtml(window.device_name || window.device_id)}` : "";
  const end = batch.group === "past" || !actions ? ""
    : `<button class="action-btn maint-end" type="button" data-ids="${window.id}">${endLabel(batch, 1)}</button>`;
  return `<li class="maint-batch-window"><span>${escHtml(siteLabel(window))}${what}</span>`
    + `<span class="maint-batch-tenants">${tenantCell({ tenants: window.tenants || [], tenant_descriptions: descriptions })}</span>${end}</li>`;
}

// One batch; *expanded*: its sites or devices listed, each with its own End;
// *actions* false (the history): no End or Cancel; *when* says its time.
export function batchHtml(batch, { descriptions = {}, expanded = false, now = new Date(), actions = true, when: whenOf = whenText } = {}) {
  const when = whenOf(batch, now);
  const { windows } = batch;
  const ids = windows.map((window) => window.id).join(",");
  const many = windows.length > 1;
  const names = batch.sites;
  const preview = many && names.length > 1
    ? `<div class="maint-batch-sites">${escHtml(names.slice(0, 3).join(", "))}${names.length > 3 ? ` +${names.length - 3}` : ""}</div>` : "";
  const end = batch.group === "past" || !actions ? ""
    : `<button class="action-btn maint-end" type="button" data-ids="${ids}">${endLabel(batch, windows.length)}</button>`;
  const toggle = many
    ? `<button class="action-btn maint-expand" type="button" aria-expanded="${expanded}">${expanded ? "Hide" : "Show"} ${windows.length}</button>` : "";
  const by = batch.created_by ? ` · by ${escHtml(batch.created_by)}` : "";
  const { tenants } = batch;
  return `
    <li class="maint-batch" data-key="${escHtml(batch.key)}">
      <div class="maint-batch-main">
        <span class="alert-badge alert-maintenance">${BADGES[batch.state] || "PLANNED"}</span>
        <div class="maint-batch-text">
          <div><strong>${escHtml(batchTitle(batch))}</strong> · ${escHtml(when)}</div>
          <div class="maint-batch-meta">${escHtml(batch.reason)}${by}</div>
          ${preview}
          ${tenants.length ? `<div class="maint-batch-tenants">${tenantCell({ tenants, tenant_descriptions: descriptions })}</div>` : ""}
        </div>
        <div class="maint-batch-actions">${toggle}${end}</div>
      </div>
      ${expanded ? `<ul class="maint-batch-windows">${windows.map((w) => windowHtml(batch, w, descriptions, actions)).join("")}</ul>` : ""}
    </li>`;
}

// The three groups; *limits* {group: N shown}, *expanded* a Set of batch keys.
// One group's parts: "N · M sites", its first *limit* batches, and "Show N more".
// *batch*: the options for batchHtml (the history's: read only, when it ran).
function groupParts(members, { id, empty, limit, step, expanded, descriptions, now, batch = {} }) {
  const shown = members.slice(0, limit);
  const rest = members.length - shown.length;
  const sites = new Set(members.flatMap((b) => b.windows.map((w) => w.site_id))).size;
  return {
    count: members.length ? ` <span class="avail-count">${members.length} · ${sites} site${sites === 1 ? "" : "s"}</span>` : "",
    list: members.length
      ? `<ul class="maint-batches">${shown.map((b) => batchHtml(b, { descriptions, expanded: expanded.has(b.key), now, ...batch })).join("")}</ul>`
      : `<p class="maint-empty">${empty}</p>`,
    more: rest > 0
      ? `<p class="avail-more">${rest} more <button class="avail-more-btn maint-more" type="button" data-group="${id}">Show ${Math.min(step, rest)} more</button></p>` : "",
  };
}

export function groupsHtml(batches, { limits, expanded, descriptions, now = new Date(), step = 25, pastOpen = false, days = 7 }) {
  return groupsFor(days).map((group) => {
    const members = batches.filter((batch) => batch.group === group.id);
    const { count, list, more } = groupParts(members, {
      id: group.id, empty: group.empty, limit: limits[group.id] || step, step, expanded, descriptions, now,
    });
    const heading = `<h2 class="availability-heading">${group.title}${count}</h2>`;
    // Ended work is history: folded unless asked for (*open*: the operator opened it).
    return group.id === "past"
      ? `<details class="table-shell maint-group" data-group="past"${pastOpen ? " open" : ""}><summary class="maint-group-summary">${heading}</summary>${list}${more}</details>`
      : `<section class="table-shell maint-group" data-group="${group.id}">${heading}${list}${more}</section>`;
  }).join("");
}

// The history (#353): every batch in the period (batchesOf keepOrder: newest
// first), read only, each saying when it ran.  *title*: "History · 1 – 30 Sep 2026".
export function historyHtml(batches, { limit, expanded, descriptions, now = new Date(), step = 25, title }) {
  const { count, list, more } = groupParts(batches, {
    id: "history", empty: "No maintenance in this period.", limit, step, expanded, descriptions, now,
    batch: { actions: false, when: spanText },
  });
  return `<section class="table-shell maint-group" data-group="history"><h2 class="availability-heading">${escHtml(title)}${count}</h2>${list}${more}</section>`;
}
