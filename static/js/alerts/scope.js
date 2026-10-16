// The board's and wall's scope (#364): the link's ?tenant= / ?q= (read in
// lib/scope.js) applied where the board's data comes in, so its rows, tiles,
// filter menus, the wall's line and its sounds cover the same sites (the
// activity feed is the whole network's, #369).
export { inScope, readScope, scopeLabel } from "../lib/scope.js";

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
