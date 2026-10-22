// The left panel folds away (#420): « on its edge hides it and the map takes
// the room; » brings it back, with the alarm count so a folded panel never
// hides that something is wrong.  Remembered in this browser; `[` toggles.
import { els, state } from "./state.js";

const PANEL_HIDDEN_KEY = "nautobot-maps-map-panel-hidden";

let hidden = false;
let alarmCount = null;

// "»", "» 23 in alarm" when folded; "«" when open.  *count*: the panel's
// alarm count, null until the alert levels are in.
export function panelToggleLabel(isHidden, count) {
  if (!isHidden) return { text: "«", label: "Hide the panel" };
  const alarms = count ? `${count.toLocaleString("en")} in alarm` : "";
  return { text: alarms ? `» ${alarms}` : "»", label: alarms ? `Show the panel: ${alarms}` : "Show the panel" };
}

function render() {
  const button = els.panelToggle;
  if (!button) return;
  const { text, label } = panelToggleLabel(hidden, alarmCount);
  button.textContent = text;
  button.setAttribute("aria-label", label);
  button.title = `${label} ([)`;
  button.setAttribute("aria-expanded", hidden ? "false" : "true");
}

// Called by the alarm list each time it is drawn.
export function setPanelAlarmCount(count) {
  alarmCount = count;
  render();
}

// The map's width changed: Leaflet measures again once the layout has settled
// (two frames), keeping the same centre.
function resizeMap() {
  const map = state.map;
  if (typeof map?.invalidateSize !== "function") return;
  window.requestAnimationFrame(() => window.requestAnimationFrame(() => map.invalidateSize({ animate: false })));
}

export function setPanelHidden(on, { remember = true } = {}) {
  hidden = Boolean(on);
  document.body.classList.toggle("panel-hidden", hidden);
  if (els.sidebar) {
    // Out of the tab order and the accessibility tree while folded.
    if (hidden && els.sidebar.contains(document.activeElement)) els.panelToggle?.focus();
    els.sidebar.inert = hidden;
  }
  render();
  resizeMap();
  if (!remember) return;
  try {
    if (hidden) localStorage.setItem(PANEL_HIDDEN_KEY, "1");
    else localStorage.removeItem(PANEL_HIDDEN_KEY);
  } catch (_err) {
    // Not remembered; works for this page.
  }
}

export function restorePanelHidden() {
  let stored;
  try {
    stored = localStorage.getItem(PANEL_HIDDEN_KEY) === "1";
  } catch (_err) {
    stored = false;
  }
  setPanelHidden(stored, { remember: false });
}

// `[` toggles it, unless typing (a field, or a key with a modifier).
export function isPanelShortcut(event) {
  if (event.key !== "[" || event.ctrlKey || event.metaKey || event.altKey) return false;
  const target = event.target;
  return !(target?.closest?.("input, textarea, select, [contenteditable]") || target?.isContentEditable);
}

export function initPanelToggle() {
  els.panelToggle.addEventListener("click", () => setPanelHidden(!hidden));
  document.addEventListener("keydown", (event) => {
    if (!isPanelShortcut(event)) return;
    event.preventDefault();
    setPanelHidden(!hidden);
  });
  restorePanelHidden();
}
