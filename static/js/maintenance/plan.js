// Planning maintenance on the Maintenance page (#343): find sites by name,
// path or tenant (or every site of a tenant at once), then what, when and
// why; one request for all the sites.  No page access at import.
import { escHtml } from "../lib/html.js";
import { whenRequest } from "../lib/when.js";

// At most this many sites listed under the search; a tenant adds all of its own.
export const MAX_MATCHES = 8;
// The server's limit for one request (maintenance.MAX_SITES).
export const MAX_SITES = 200;

// Sites matching *query* (every word in the name, path or a tenant), not yet
// chosen, names first; and the tenants whose name has it, with their sites.
export function matchSites(sites, query, chosen = new Set()) {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return { sites: [], tenants: [], more: 0 };
  const open = sites.filter((site) => !chosen.has(site.id));
  const hits = open.filter((site) => {
    const text = [site.name, site.path, ...(site.tenants || [])].join(" ").toLowerCase();
    return words.every((word) => text.includes(word));
  });
  const phrase = words.join(" ");
  hits.sort((a, b) => Number(!a.name.toLowerCase().includes(phrase)) - Number(!b.name.toLowerCase().includes(phrase))
    || a.name.localeCompare(b.name));
  const byTenant = new Map();
  for (const site of open) {
    for (const tenant of site.tenants || []) {
      if (!tenant.toLowerCase().includes(phrase)) continue;
      if (!byTenant.has(tenant)) byTenant.set(tenant, []);
      byTenant.get(tenant).push(site.id);
    }
  }
  const tenants = [...byTenant].map(([name, ids]) => ({ name, ids }))
    .filter((tenant) => tenant.ids.length > 1).sort((a, b) => b.ids.length - a.ids.length).slice(0, 3);
  return { sites: hits.slice(0, MAX_MATCHES), tenants, more: Math.max(0, hits.length - MAX_MATCHES) };
}

export function matchesHtml(match) {
  const tenants = match.tenants.map((tenant) => `<li role="option" class="maint-match maint-match-tenant" data-ids="${escHtml(tenant.ids.join("\n"))}" tabindex="-1">`
    + `Add all ${tenant.ids.length} sites of <strong>${escHtml(tenant.name)}</strong></li>`);
  const sites = match.sites.map((site) => `<li role="option" class="maint-match" data-ids="${escHtml(site.id)}" tabindex="-1">`
    + `<strong>${escHtml(site.name)}</strong>${site.path ? ` <span class="maint-match-path">${escHtml(site.path)}</span>` : ""}`
    + `${site.maintenance ? ' <span class="maint-match-path">· in maintenance now</span>' : ""}</li>`);
  const more = match.more ? [`<li class="maint-match-more" aria-disabled="true">${match.more} more: type more of the name</li>`] : [];
  return [...tenants, ...sites, ...more].join("") || '<li class="maint-match-more" aria-disabled="true">No site matches.</li>';
}

// The chosen sites as chips; with many, the first 12 and "+N".
export function chosenHtml(ids, byId, showAll = false) {
  const shown = showAll ? ids : ids.slice(0, 12);
  const chips = shown.map((id) => `<li class="maint-chip"><span>${escHtml(byId.get(id)?.name || id)}</span>`
    + `<button type="button" class="maint-chip-remove" data-id="${escHtml(id)}" aria-label="Remove ${escHtml(byId.get(id)?.name || id)}">×</button></li>`);
  const rest = ids.length - shown.length;
  if (rest > 0) chips.push(`<li><button type="button" class="avail-more-btn maint-chips-all">+${rest} more</button></li>`);
  if (ids.length > 1) chips.push('<li><button type="button" class="avail-more-btn maint-chips-clear">Clear</button></li>');
  return chips.join("");
}

// The request, or {error}.  values: {siteIds, scope, deviceIds, reason, ...when}.
export function planRequest(values) {
  if (!values.siteIds.length) return { error: "Choose at least one site." };
  if (values.siteIds.length > MAX_SITES) return { error: `At most ${MAX_SITES} sites at once.` };
  const reason = (values.reason || "").trim();
  if (!reason) return { error: "Enter a reason." };
  const body = { reason };
  if (values.scope === "devices") {
    if (values.siteIds.length !== 1) return { error: "Choose devices for one site at a time." };
    if (!values.deviceIds.length) return { error: "Tick at least one device, or choose Whole sites." };
    body.site_id = values.siteIds[0];
    body.device_ids = values.deviceIds;
  } else {
    body.site_ids = values.siteIds;
  }
  const when = whenRequest(values);
  return when.error ? when : { ...body, ...when };
}

// One site's monitored devices to tick (as in the board's Maint. panel).
export function devicesHtml(devices) {
  if (!devices.length) return '<p class="maint-empty">No monitored devices.</p>';
  return devices.map((device) => `
    <label class="case-device-option">
      <input type="checkbox" class="maint-device" value="${escHtml(device.id)}" />
      <span>${escHtml(device.name || device.id)}</span>
      <span class="maint-device-meta">${[device.location_path, device.role, device.status].filter(Boolean).map(escHtml).join(" · ")}</span>
    </label>`).join("");
}
