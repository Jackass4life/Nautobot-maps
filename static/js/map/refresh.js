// Alert levels of every location, so markers are coloured without a click (#234).
import { applyLevelToOpenPanel } from "./inspector.js";
import { crossesClusterLine, redrawMarkers, refreshMarkerIcon } from "./markers.js";
import { renderAlarmList } from "./alarms.js";
import { detailCache, els, locationAlertInfo, locationAlerts, state } from "./state.js";

const ALERT_REFRESH_MS = 60 * 1000;
// Until the levels have loaded once, a location without one isn't known to
// have no data (#311).
let locationAlertsLoaded = false;

function setAlertLevelsNote(show) {
  if (els.alertLevelsNote) els.alertLevelsNote.hidden = !show;
}

export async function loadLocationAlerts() {
  let data;
  try {
    const resp = await fetch("/api/location-alerts");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    data = await resp.json();
  } catch (err) {
    // Markers keep their last colours; a click still loads the site's level.
    // Never loaded: grey would read as "no data", so say so (#311).
    console.warn("Could not load location alert levels:", err);
    if (!locationAlertsLoaded) setAlertLevelsNote(true);
    return;
  }
  const firstLoad = !state.levelsLoaded;
  locationAlertsLoaded = true;
  state.levelsLoaded = true;
  setAlertLevelsNote(false);
  const levels = data.levels || {};
  for (const id of Object.keys(locationAlertInfo)) delete locationAlertInfo[id];
  Object.assign(locationAlertInfo, levels);
  const changed = new Set();
  for (const id of Object.keys(locationAlerts)) {
    if (!levels[id]) {
      delete locationAlerts[id];
      changed.add(id);
    }
  }
  for (const [id, alert] of Object.entries(levels)) {
    if (locationAlerts[id] !== alert.level) {
      locationAlerts[id] = alert.level;
      changed.add(id);
    }
  }
  // Site details loaded earlier may now hold an old alert level.
  for (const id of detailCache.keys()) {
    if (id !== state.selectedLocationId) detailCache.delete(id);
  }
  // A site that started or stopped needing attention: one redraw for all.
  // With "Alarms only", any change may add or remove a site, and the first
  // levels turn it on.  (Not while search results are shown: redrawMarkers.)
  if ([...changed].some(crossesClusterLine) || (state.alarmsOnly && (changed.size || firstLoad))) redrawMarkers();
  else for (const id of changed) refreshMarkerIcon(id);
  // Times and counts change without a level change: the list always.
  renderAlarmList();
  applyLevelToOpenPanel(levels);
}

// Every minute while the tab is visible; *loadNow*: also now.
export function startLocationAlertRefresh({ loadNow = true } = {}) {
  if (loadNow) loadLocationAlerts();
  setInterval(() => {
    if (!document.hidden) loadLocationAlerts();
  }, ALERT_REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadLocationAlerts();
  });
}
