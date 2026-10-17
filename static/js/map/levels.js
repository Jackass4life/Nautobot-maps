// Alert levels on the map: one colour scale for pins, stacks and clusters.
import { locationAlerts } from "./state.js";

// The alert level only (#311). Status (Active, Planned) is a filter, not a colour.
export const LEVEL_COLORS = {
  critical: "#e74c3c",
  medium: "#ff8c00",
  low: "#eab308",
  maintenance: "#2563eb",
  ok: "#2ecc71",
  no_data: "#94a3b8",
};

// Alert levels that change a marker or show a banner, least to most severe.
// "ok" and "no_data" are not alerts (#124).
export const ALERT_RANK = { low: 1, medium: 2, critical: 3 };

// What a stack or cluster shows: its worst member (#234, #311).
export const DISPLAY_RANK = { no_data: 0, ok: 1, maintenance: 2, low: 3, medium: 4, critical: 5 };

// A level for its colour: an unknown one (or Object.prototype's keys) is No data.
export function knownLevel(level) {
  return Object.hasOwn(LEVEL_COLORS, level) ? level : "no_data";
}

// A location's level for its colour; without one it has no monitored devices.
export function displayLevel(locId) {
  const level = locationAlerts[locId];
  return level in DISPLAY_RANK ? level : "no_data";
}

// Worst level among *locIds*, for a stack or cluster.
export function worstAlertLevel(locIds) {
  return locIds.reduce((highest, id) => {
    const level = displayLevel(id);
    return DISPLAY_RANK[level] > DISPLAY_RANK[highest] ? level : highest;
  }, "no_data");
}
