// Planning maintenance on the Maintenance page (#343): find sites by name,
// path or tenant (or every site of a tenant at once).  The request and the
// device list are shared with the board (lib/maintenance-form.js).  No page access at import.
import { hasAllWords, searchWords } from "../lib/format.js";
import { escHtml } from "../lib/html.js";

// At most this many sites listed under the search; a tenant adds all of its own.
export const MAX_MATCHES = 8;

const byName = new Intl.Collator("en", { numeric: true, sensitivity: "base" });

// The sites as the picker searches them: sorted by name once, their text
// lower-cased once, not on every key.
export function prepareSites(sites) {
  return [...sites].sort((a, b) => byName.compare(a.name, b.name)).map((site) => ({
    ...site,
    lowerName: site.name.toLowerCase(),
    lowerTenants: (site.tenants || []).map((tenant) => [tenant, tenant.toLowerCase()]),
    text: [site.name, site.path, ...(site.tenants || [])].join(" ").toLowerCase(),
  }));
}

// Sites (from prepareSites) with every word in the name, path or a tenant, not
// yet chosen: those whose name has the phrase first, then by name; and the
// tenants whose name has it, with all their sites.
export function matchSites(sites, query, chosen = new Set()) {
  const words = searchWords(query);
  if (!words.length) return { sites: [], tenants: [], more: 0 };
  const phrase = words.join(" ");
  const named = [];
  const other = [];
  const byTenant = new Map();
  for (const site of sites) {
    if (chosen.has(site.id)) continue;
    if (hasAllWords(site.text, words)) (site.lowerName.includes(phrase) ? named : other).push(site);
    for (const [tenant, lower] of site.lowerTenants) {
      if (!lower.includes(phrase)) continue;
      if (!byTenant.has(tenant)) byTenant.set(tenant, []);
      byTenant.get(tenant).push(site.id);
    }
  }
  const hits = named.concat(other);
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
