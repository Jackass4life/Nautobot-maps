// The wall-screen view, /alerts?view=wall (#243): its status line, its error
// banner, and a chime and a notification for a new Critical (#290).
import { formatClock } from "../lib/format.js";
import { hasActiveAlarm, siteCaseState } from "./rows.js";
import { scopeLabel } from "./scope.js";
import { ALARM_LEVELS, config, els, state } from "./state.js";

// The wall reloads on its own every minute as well, so a screen nobody
// touches recovers from errors and an unknown next update.
export const WALL_RELOAD_MS = 60000;
// No successful load for this long: say so loudly, the data is old.
const WALL_STALE_MS = 10 * 60000;

// "Updated 14:05 · 3 sites with alarms"; flagged once no load has worked for a while.
export function renderWallStatus(now = Date.now()) {
  const { wallUpdatedEl } = els;
  if (!wallUpdatedEl) return;
  const { allAlerts, lastLoadedAt } = state;
  if (lastLoadedAt === null) {
    wallUpdatedEl.textContent = "Loading…";
    return;
  }
  const alarmSites = allAlerts.filter(hasActiveAlarm);
  const alarms = alarmSites.length;
  const withoutCase = alarmSites.filter((item) => siteCaseState(item).state !== "handled").length;
  const planned = allAlerts.filter((item) => item.alert_level === "maintenance").length;
  const stale = now - lastLoadedAt >= WALL_STALE_MS;
  // A screen for some sites says which, so it isn't read as the whole network (#364).
  const scope = config.scope ? `${scopeLabel(config.scope, allAlerts)} · ` : "";
  wallUpdatedEl.classList.toggle("wall-stale", stale);
  wallUpdatedEl.textContent = stale
    ? `${scope}NOT UPDATED since ${formatClock(lastLoadedAt)}`
    : `${scope}Updated ${formatClock(lastLoadedAt)} · ${alarms} site${alarms === 1 ? "" : "s"} with alarms · ${withoutCase} without case or ack`
      + (planned ? ` · ${planned} in maintenance` : "");
}

export function showWallError(message) {
  const { wallErrorEl } = els;
  if (!wallErrorEl) return;
  const since = state.lastLoadedAt === null ? "" : ` Showing the board from ${formatClock(state.lastLoadedAt)}.`;
  wallErrorEl.textContent = `Alert board not updating: ${message}.${since}`;
  wallErrorEl.hidden = false;
}

const WALL_SOUND_KEY = "nautobot-maps-wall-sound";
let wallSoundOn = false;
let audioContext = null;
// What was already announced (or there at the start): site id -> "critical" / "nobody".
let announced = null;

// The alarms that are new since *before* (#290): sites that became Critical,
// and sites in alarm with nobody on them (no case, no acknowledgement).
// Returns { events: [{ id, name, kind }], seen }; *before* null = the first
// load, which only records what is there.
export function newAlarms(before, sites) {
  const seen = new Map();
  const events = [];
  sites.forEach((site) => {
    const id = String(site.id || "");
    if (!id || !ALARM_LEVELS.includes(site.alert_level)) return;
    const kinds = [];
    if (site.alert_level === "critical") kinds.push("critical");
    if (siteCaseState(site).state === "none") kinds.push("nobody");
    const previous = before ? before.get(id) || new Set() : null;
    const now = new Set(kinds);
    seen.set(id, now);
    if (!previous) return;
    kinds.filter((kind) => !previous.has(kind)).forEach((kind) => events.push({ id, name: site.name || id, kind }));
  });
  return { events, seen };
}

export function alarmText(event) {
  return event.kind === "critical" ? `${event.name} is Critical` : `${event.name}: in alarm, nobody on it`;
}

// Two short tones from the browser itself: no sound file to load.
function playChime() {
  if (!audioContext) return;
  const start = audioContext.currentTime;
  [880, 660].forEach((frequency, index) => {
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(0.0001, start + index * 0.25);
    gain.gain.exponentialRampToValueAtTime(0.3, start + index * 0.25 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + index * 0.25 + 0.22);
    oscillator.connect(gain).connect(audioContext.destination);
    oscillator.start(start + index * 0.25);
    oscillator.stop(start + index * 0.25 + 0.24);
  });
}

// At most this many notifications open at once; the rest wait until one closes.
const MAX_OPEN_NOTIFICATIONS = 5;
const openNotifications = new Set();
const queuedNotifications = [];
// While the browser's question is unanswered, at most this many wait.
const MAX_QUEUED_NOTIFICATIONS = 50;

export function showQueuedNotifications() {
  const permission = window.Notification ? Notification.permission : "denied";
  if (permission === "default") {
    // Not answered yet: keep the newest waiting.
    queuedNotifications.splice(0, Math.max(0, queuedNotifications.length - MAX_QUEUED_NOTIFICATIONS));
    return;
  }
  if (permission !== "granted") {
    queuedNotifications.length = 0; // never shown here: don't keep them
    return;
  }
  while (openNotifications.size < MAX_OPEN_NOTIFICATIONS && queuedNotifications.length) {
    const event = queuedNotifications.shift();
    try {
      const note = new Notification("Nautobot Maps", { body: alarmText(event), tag: `${event.id}-${event.kind}` });
      openNotifications.add(note);
      note.addEventListener("close", () => {
        openNotifications.delete(note);
        showQueuedNotifications();
      });
    } catch (_err) {
      // Notifications unavailable here: the chime still played.
    }
  }
}

export function announceNewAlarms(sites) {
  const { events, seen } = newAlarms(announced, sites);
  announced = seen;
  if (!wallSoundOn || !events.length) return;
  playChime();
  queuedNotifications.push(...events);
  showQueuedNotifications();
}

export function setWallSound(on) {
  wallSoundOn = Boolean(on);
  if (!wallSoundOn) queuedNotifications.length = 0;
  const { wallSoundBtn } = els;
  if (!wallSoundBtn) return;
  wallSoundBtn.textContent = wallSoundOn ? "Sound on" : "Sound off";
  wallSoundBtn.setAttribute("aria-pressed", wallSoundOn ? "true" : "false");
  try {
    if (wallSoundOn) localStorage.setItem(WALL_SOUND_KEY, "on");
    else localStorage.removeItem(WALL_SOUND_KEY);
  } catch (_err) {
    // Not remembered; works for this page.
  }
}

function unlockAudio() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (AudioContextClass && !audioContext) audioContext = new AudioContextClass();
  audioContext?.resume();
}

// The Sound button, and the remembered choice.
export function initWallSound() {
  const { wallSoundBtn } = els;
  if (!wallSoundBtn) return;
  wallSoundBtn.addEventListener("click", () => {
    const on = !wallSoundOn;
    if (on) {
      // This click is what lets the browser play sound.
      unlockAudio();
      if (window.Notification && Notification.permission === "default") {
        // Show what came in while the browser was asking.
        Promise.resolve(Notification.requestPermission()).then(showQueuedNotifications, () => {});
      }
      playChime();
    }
    setWallSound(on);
  });
  let remembered;
  try {
    remembered = localStorage.getItem(WALL_SOUND_KEY) === "on";
  } catch (_err) {
    remembered = false;
  }
  if (remembered) {
    // Remembered, but a browser plays sound only after a click on this page:
    // the first click anywhere unlocks it.
    setWallSound(true);
    document.addEventListener("click", unlockAudio, { once: true });
  }
}
