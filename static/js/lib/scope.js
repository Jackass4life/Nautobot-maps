// A page for some sites only (#364, #367): ?tenant= (repeatable) and ?q= on
// its link, read the same way on the board, the wall and the map.

// Every tenant of the site: its own, then those linked by a Nautobot
// Relationship (#238).  Data cached before #238 only has `tenant`.
export function siteTenants(item) {
  if (Array.isArray(item.tenants) && item.tenants.length) return item.tenants;
  return item.tenant ? [item.tenant] : [];
}

// After "No site matches …" on the board, the wall and the map.
export const SCOPE_NO_MATCH_HINT = "Check the link's tenant= and q=.";

// The address shown for a site, and searched.
export function formatLocationAddress(item) {
  return item.physical_address || item.facility || "";
}

// What a filter box (and a link's ?q=) searches: name, path, address, country.
export function siteSearchText(item) {
  return [item.name, item.ancestor_path || item.parent, formatLocationAddress(item), item.country]
    .join(" ")
    .toLowerCase();
}

// {tenants, texts} from a query string, as typed, plus their lower case for
// matching; null when it has neither.  ?tenant= is any of them, ?q= every one
// of them (#366: the wall link can carry the board's own filter too).
export function readScope(search) {
  const params = new URLSearchParams(search || "");
  const clean = (name) => params.getAll(name).map((value) => value.trim()).filter(Boolean);
  const tenants = clean("tenant");
  const texts = clean("q");
  if (!tenants.length && !texts.length) return null;
  return {
    tenants,
    texts,
    lowerTenants: tenants.map((name) => name.toLowerCase()),
    lowerTexts: texts.map((text) => text.toLowerCase()),
  };
}

// Typed by hand into a link, so case doesn't matter.
export function inScope(site, scope) {
  if (!scope) return true;
  const { lowerTenants, lowerTexts } = scope;
  if (lowerTenants.length && !siteTenants(site).some((name) => lowerTenants.includes(name.toLowerCase()))) return false;
  if (!lowerTexts.length) return true;
  const text = siteSearchText(site);
  return lowerTexts.every((needle) => text.includes(needle));
}

// The scope as ?tenant= / ?q= again, for a request or link for the same sites.
export function scopeParams(scope) {
  const params = new URLSearchParams();
  scope?.tenants.forEach((name) => params.append("tenant", name));
  scope?.texts.forEach((text) => params.append("q", text));
  return params;
}

// Staff page links follow the tenant picks, keeping the link's text scope.
export function updateTenantLinks(tenants, scope) {
  const params = scopeParams(scope);
  if (tenants.length) {
    params.delete("tenant");
    tenants.forEach((name) => params.append("tenant", name));
  }
  for (const link of document.querySelectorAll(".app-nav-link")) {
    const href = link.getAttribute("href") || "";
    const path = href.split("?")[0];
    if (path !== "/" && path !== "/alerts") continue;
    // "view=wall" first, as the page writes it.
    const query = new URLSearchParams();
    if (new URLSearchParams(href.split("?")[1]).get("view") === "wall") query.set("view", "wall");
    params.forEach((value, key) => query.append(key, value));
    link.setAttribute("href", `${path}${query.size ? `?${query}` : ""}`);
  }
}

// "Acme Corp, Nordic Net · “DNK”": what the page shows, each tenant as
// Nautobot spells it when one of *sites* has it.
export function scopeLabel(scope, sites = []) {
  if (!scope) return "";
  const known = new Map(sites.flatMap(siteTenants).map((name) => [name.toLowerCase(), name]));
  const tenants = scope.tenants.map((name) => known.get(name.toLowerCase()) || name);
  return [tenants.join(", "), ...scope.texts.map((text) => `“${text}”`)].filter(Boolean).join(" · ");
}
