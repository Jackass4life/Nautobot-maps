// What the alert board's modules share (#294).  main.js fills `els` and
// `config` from the page; nothing here touches the page when imported, so
// the tests can import every module in Node and give them stand-ins.

// The page's elements, by the names the modules use (filled by main.js).
export const els = {};

// Settings from the server (templates/alerts.html), and the wall view.
export const config = {
  nautobotUrl: "",
  // Wall-screen view, /alerts?view=wall (#243): alarms only, every site
  // expanded, no controls.
  wallView: false,
  // ?tenant= / ?q= on the link: these sites only, or null (#364).
  scope: null,
};

export const DEFAULT_SEVERITY_FILTER = "alarms";
// The operator's last Sort choice; the default puts the site with the
// newest down device first (#228).
export const SORT_KEY = "nautobot-maps-alert-sort";
export const DEFAULT_SORT = "newest";
// Levels that are an active alarm; No data is not an alert (#124).
export const ALARM_LEVELS = ["critical", "medium", "low"];
// Most severe first; the server sorts the same way (ALERT_LEVEL_ORDER, #124).
export const SEVERITY_ORDER = ["critical", "medium", "low", "no_data", "ok"];

export const state = {
  allAlerts: [],
  latestPayload: { checked_at: null, stale: false, summary: {}, alerts: [] },
  // When the board last loaded, or null before the first load.
  lastLoadedAt: null,
  // The summary tiles are the severity filter: Alarms (default), one level, or All sites.
  severityFilter: DEFAULT_SEVERITY_FILTER,
  // Sites the operator opened; with allSitesExpanded, the ones they closed.
  expandedSiteIds: new Set(),
  allSitesExpanded: false,
  // What a link's ?tenant= / ?q= shows, worked out once per load (#364).
  scopeLabel: "",
};

export function findSite(siteId) {
  return state.allAlerts.find((alert) => String(alert.id) === siteId);
}
