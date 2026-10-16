// A page for some sites only (#364, #367): ?tenant= (repeatable) and ?q= on
// its link, read the same way on the board, the wall and the map.

// Every tenant of the site: its own, then those linked by a Nautobot
// Relationship (#238).  Data cached before #238 only has `tenant`.
export function siteTenants(item) {
  if (Array.isArray(item.tenants) && item.tenants.length) return item.tenants;
  return item.tenant ? [item.tenant] : [];
}

// What a filter box (and a link's ?q=) searches: name, path, address, country.
export function siteSearchText(item) {
  const address = item.physical_address || item.facility || "";
  return [item.name, item.ancestor_path || item.parent, address, item.country].join(" ").toLowerCase();
}

// {tenants, text} from a query string, as typed, plus their lower case for
// matching; null when it has neither.
export function readScope(search) {
  const params = new URLSearchParams(search || "");
  const tenants = params.getAll("tenant").map((value) => value.trim()).filter(Boolean);
  const text = (params.get("q") || "").trim();
  if (!tenants.length && !text) return null;
  return { tenants, text, lowerTenants: tenants.map((name) => name.toLowerCase()), lowerText: text.toLowerCase() };
}

// Typed by hand into a link, so case doesn't matter.
export function inScope(site, scope) {
  if (!scope) return true;
  const { lowerTenants, lowerText } = scope;
  if (lowerTenants.length && !siteTenants(site).some((name) => lowerTenants.includes(name.toLowerCase()))) return false;
  return !lowerText || siteSearchText(site).includes(lowerText);
}

// "Acme Corp, Nordic Net · “DNK”": what the page shows, each tenant as
// Nautobot spells it when one of *sites* has it.
export function scopeLabel(scope, sites = []) {
  if (!scope) return "";
  const known = new Map(sites.flatMap(siteTenants).map((name) => [name.toLowerCase(), name]));
  const tenants = scope.tenants.map((name) => known.get(name.toLowerCase()) || name);
  return [tenants.join(", "), scope.text && `“${scope.text}”`].filter(Boolean).join(" · ");
}

// The link's ?tenant= / ?q= as a query string ("" without them), for links
// to the same sites on another page.
export function scopeQuery(scope) {
  if (!scope) return "";
  const params = new URLSearchParams();
  scope.tenants.forEach((name) => params.append("tenant", name));
  if (scope.text) params.set("q", scope.text);
  return params.toString();
}
