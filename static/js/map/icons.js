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

function makeIcon(color, pulse) {
  const cls = pulse ? ' class="marker-pulse"' : '';
  return L.divIcon({
    html: `<div${cls} style="width:24px;height:36px">${pinSvg(color)}</div>`,
    iconSize: [24, 36],
    iconAnchor: [12, 36],
    popupAnchor: [0, -36],
    className: "",
  });
}

// One pin per level, shared by every marker of that level.
const pinIcons = {};

export function pinIcon(level) {
  const known = level in LEVEL_COLORS ? level : "no_data";
  if (!pinIcons[known]) pinIcons[known] = makeIcon(LEVEL_COLORS[known], known === "critical");
  return pinIcons[known];
}

export function iconForLocation(loc) {
  return pinIcon(displayLevel(loc.id));
}

// Zoomed-out cluster bubble, coloured by the worst alert inside it (#234).
export function makeClusterIcon(count, alertLevel) {
  const color = LEVEL_COLORS[alertLevel] || LEVEL_COLORS.no_data;
  const cls = alertLevel === "critical" ? "marker-pulse" : "";
  return L.divIcon({
    html: `<div class="${cls}" style="width:40px;height:40px;background:${color};color:white;border:3px solid #fff;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:bold;box-shadow:0 0 8px rgba(0,0,0,.4)">${count}</div>`,
    iconSize: [40, 40],
    iconAnchor: [20, 20],
    className: "",
  });
}

// Several sites at the same coordinates: one pin with a count.
export function makeStackedIcon(count, alertLevel) {
  const color = LEVEL_COLORS[alertLevel] || LEVEL_COLORS.no_data;
  const cls = alertLevel === "critical" ? ' class="marker-pulse"' : '';
  return L.divIcon({
    html: `<div${cls} style="width:24px;height:36px;position:relative">${pinSvg(color)}<span class="colocated-badge">${count}</span></div>`,
    iconSize: [24, 36],
    iconAnchor: [12, 36],
    popupAnchor: [0, -36],
    className: "",
  });
}
