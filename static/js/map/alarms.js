// The sidebar's "In alarm (N)" list (#334): the sites that need action, worst
// first and then newest, the first ten and "+N more".  A row opens the site.
import { formatFeedTime } from "../lib/format.js";
import { escHtml } from "../lib/html.js";
import { els, locationAlertInfo, locationAlerts, state } from "./state.js";

const ALARM_RANK = { critical: 0, medium: 1, low: 2 };
const LEVEL_LABELS = { critical: "Critical", medium: "Medium", low: "Low" };
export const ALARM_LIST_SHOWN = 10;

// Whether the list shows every site, after "+N more".
let expanded = false;

export function isAlarm(level) {
  return level in ALARM_RANK;
}

// The sites of *locations* in alarm, worst first, then newest (since), then by
// name.  *info*: location id -> {level, since, down, monitored}.
export function alarmSites(locations, info) {
  const time = (entry) => Date.parse(entry.since || "") || null;
  return locations
    .filter((loc) => isAlarm(info[loc.id]?.level))
    .map((loc) => ({ loc, ...info[loc.id] }))
    .sort((a, b) => ALARM_RANK[a.level] - ALARM_RANK[b.level]
      || (time(b) ?? -Infinity) - (time(a) ?? -Infinity)
      || (a.loc.name || "").localeCompare(b.loc.name || ""));
}

// One row: the level's colour, the site, "2/8 down · since 14:05".
export function alarmItemHtml(entry, now = new Date()) {
  const { loc, level } = entry;
  const name = loc.name || loc.id;
  const since = entry.since ? formatFeedTime(entry.since, now) : "";
  const meta = [
    Number.isFinite(entry.monitored) ? `${entry.down || 0}/${entry.monitored} down` : "",
    since ? `since ${since}` : "",
  ].filter(Boolean).join(" · ");
  const label = [`${LEVEL_LABELS[level]}: ${name}`, meta].filter(Boolean).join(", ");
  return `<li><button class="alarm-item" type="button" data-location-id="${escHtml(loc.id)}" aria-label="${escHtml(label)}">`
    + `<span class="legend-dot dot-${level}" aria-hidden="true"></span>`
    + `<span class="alarm-name">${escHtml(name)}</span>`
    + `${meta ? `<span class="alarm-meta">${escHtml(meta)}</span>` : ""}</button></li>`;
}

export function toggleAlarmList() {
  expanded = !expanded;
  renderAlarmList();
}

export function renderAlarmList() {
  const { alarmList, alarmCount, alarmMore } = els;
  if (!alarmList) return;
  if (!state.levelsLoaded) {
    alarmCount.textContent = "";
    alarmList.innerHTML = '<li class="alarm-empty">Alert levels not loaded yet.</li>';
    alarmMore.hidden = true;
    return;
  }
  // locationAlerts can be newer than locationAlertInfo (a site panel's level).
  const info = Object.fromEntries(Object.entries(locationAlertInfo).map(([id, entry]) => [id, { ...entry, level: locationAlerts[id] }]));
  const sites = alarmSites(state.filteredLocations, info);
  alarmCount.textContent = `(${sites.length})`;
  if (!sites.length) {
    alarmList.innerHTML = '<li class="alarm-empty">No site in alarm.</li>';
    alarmMore.hidden = true;
    return;
  }
  const now = new Date();
  const shown = expanded ? sites : sites.slice(0, ALARM_LIST_SHOWN);
  alarmList.innerHTML = shown.map((entry) => alarmItemHtml(entry, now)).join("");
  const hidden = sites.length - ALARM_LIST_SHOWN;
  alarmMore.hidden = hidden <= 0;
  alarmMore.textContent = expanded ? "Show fewer" : `+${hidden} more`;
  alarmMore.setAttribute("aria-expanded", expanded ? "true" : "false");
}
