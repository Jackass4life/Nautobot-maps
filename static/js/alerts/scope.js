// A board or wall for some sites only (#364): ?tenant= (repeatable) and ?q=
// on the page's link.  Applied where the board's data comes in, so its rows,
// tiles, filter menus, the wall's line and its sounds cover the same sites
// (the activity feed is the whole network's).
import { siteSearchText } from "./filters.js";
import { siteTenants } from "./rows.js";

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

// The server's summary, for the sites in scope only: sites per level (the
// tiles add up the alarms themselves).
export function scopeSummary(sites) {
  const summary = { total: sites.length };
  sites.forEach((site) => {
    const level = site.alert_level || "no_data";
    summary[level] = (summary[level] || 0) + 1;
  });
  return summary;
}

// "Acme Corp, Nordic Net · “DNK”": what this page shows, each tenant as
// Nautobot spells it when one of *sites* has it.
export function scopeLabel(scope, sites = []) {
  if (!scope) return "";
  const known = new Map(sites.flatMap(siteTenants).map((name) => [name.toLowerCase(), name]));
  const tenants = scope.tenants.map((name) => known.get(name.toLowerCase()) || name);
  return [tenants.join(", "), scope.text && `“${scope.text}”`].filter(Boolean).join(" · ");
}
