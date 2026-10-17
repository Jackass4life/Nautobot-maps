// Marker icons (Leaflet divIcons).  Made on first use, not on import: Leaflet
// is a global from vendor/leaflet.js, and the tests import this without it.
import { LEVEL_COLORS, displayLevel } from "./levels.js";

const PIN_PATH = "M12 0C5.373 0 0 5.373 0 12c0 9 12 24 12 24S24 21 24 12C24 5.373 18.627 0 12 0z";

function pinSvg(color) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 36">
    <path d="${PIN_PATH}"
          fill="${color}" stroke="#fff" stroke-width="1.5"/>
    <circle cx="12" cy="12" r="4.5" fill="#fff"/>
  </svg>`;
}

// No style attributes: the CSP allows no inline styles (#370), so sizes and
// colours are classes in map.css (the pin's SVG fill is an attribute, allowed).
function knownLevel(level) {
  return level in LEVEL_COLORS ? level : "no_data";
}

function makeIcon(color, pulse) {
  return L.divIcon({
    html: `<div class="pin-icon${pulse ? " marker-pulse" : ""}">${pinSvg(color)}</div>`,
    iconSize: [24, 36],
    iconAnchor: [12, 36],
    popupAnchor: [0, -36],
    className: "",
  });
}

// One pin per level, shared by every marker of that level.
const pinIcons = {};

export function pinIcon(level) {
  const known = knownLevel(level);
  if (!pinIcons[known]) pinIcons[known] = makeIcon(LEVEL_COLORS[known], known === "critical");
  return pinIcons[known];
}

export function iconForLocation(loc) {
  return pinIcon(displayLevel(loc.id));
}

// A cluster of quiet sites (#334): green with an OK site in it, else grey.
// Quieter than a pin (smaller, softer) so the pins of sites in alarm stand
// out; a little bigger for more sites.  Leaflet makes the marker a button;
// the label says what is in it.
export function makeClusterIcon(count, alertLevel) {
  const size = count < 10 ? 24 : count < 100 ? 30 : 36;
  const label = `${count} location${count === 1 ? "" : "s"} without an alarm, zoom in`;
  const classes = `quiet-cluster quiet-size-${size} level-bg-${knownLevel(alertLevel)}`;
  return L.divIcon({
    html: `<div class="${classes}" role="img" aria-label="${label}">${count}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    className: "",
  });
}

// A quiet site (OK, No data) on its own: a dot, not a pin (#334); with a
// count for several sites at the same coordinates.
export function quietDotIcon(level, count = 1) {
  const size = count > 1 ? 18 : 12;
  return L.divIcon({
    html: `<div class="quiet-dot quiet-size-${size} level-bg-${knownLevel(level)}">${count > 1 ? count : ""}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    popupAnchor: [0, -size / 2],
    className: "",
  });
}

// Several sites at the same coordinates: one pin with a count.
export function makeStackedIcon(count, alertLevel) {
  const color = LEVEL_COLORS[alertLevel] || LEVEL_COLORS.no_data;
  const pulse = alertLevel === "critical" ? " marker-pulse" : "";
  return L.divIcon({
    html: `<div class="pin-icon pin-stacked${pulse}">${pinSvg(color)}<span class="colocated-badge">${count}</span></div>`,
    iconSize: [24, 36],
    iconAnchor: [12, 36],
    popupAnchor: [0, -36],
    className: "",
  });
}
