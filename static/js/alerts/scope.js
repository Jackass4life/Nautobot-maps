// A board or wall for some sites only (#364): ?tenant= (repeatable) and ?q=
// on the page's link.  Applied where the data comes in, so the rows, the
// counts, the wall's line and its sounds all cover the same sites.
import { siteSearchText } from "./filters.js";
import { siteTenants } from "./rows.js";
import { SEVERITY_ORDER } from "./state.js";

// {tenants, text} from a query string, or null when it has neither.
export function readScope(search) {
  const params = new URLSearchParams(search || "");
  const tenants = params.getAll("tenant").map((value) => value.trim()).filter(Boolean);
  const text = (params.get("q") || "").trim();
  return tenants.length || text ? { tenants, text } : null;
}

// Typed by hand into a link, so case doesn't matter.
export function inScope(site, scope) {
  if (!scope) return true;
  const wanted = scope.tenants.map((name) => name.toLowerCase());
  if (wanted.length && !siteTenants(site).some((name) => wanted.includes(name.toLowerCase()))) return false;
  return !scope.text || siteSearchText(site).includes(scope.text.toLowerCase());
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
  summary.non_ok = (summary.critical || 0) + (summary.medium || 0) + (summary.low || 0);
  return summary;
}

// "Acme Corp, Nordic Net · “DNK”": what this page shows.
export function scopeLabel(scope) {
  if (!scope) return "";
  return [scope.tenants.join(", "), scope.text && `“${scope.text}”`].filter(Boolean).join(" · ");
}
