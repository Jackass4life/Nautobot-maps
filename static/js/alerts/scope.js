// A board or wall for some sites only (#364): ?tenant= (repeatable) and ?q=
// on the page's link.  Applied where the board's data comes in, so its rows,
// tiles, filter menus, the wall's line and its sounds cover the same sites
// (the activity feed is the whole network's).
import { siteSearchText } from "./filters.js";
import { siteTenants } from "./rows.js";
import { ALARM_LEVELS, SEVERITY_ORDER } from "./state.js";

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

// The server's summary, for the sites in scope only.
export function scopeSummary(sites) {
  const summary = { total: sites.length, maintenance: 0 };
  SEVERITY_ORDER.forEach((level) => {
    summary[level] = 0;
  });
  sites.forEach((site) => {
    const level = site.alert_level || "no_data";
    summary[level] = (summary[level] || 0) + 1;
  });
  summary.non_ok = ALARM_LEVELS.reduce((sum, level) => sum + summary[level], 0);
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

// The link's other parameters on *path*: the Wall view and Operator view
// links keep the scope (?view= is theirs to set).
export function scopedHref(path, search) {
  const params = new URLSearchParams(search || "");
  params.delete("view");
  const kept = params.toString();
  if (!kept) return path;
  return `${path}${path.includes("?") ? "&" : "?"}${kept}`;
}
