// Times and durations as the pages show them (#294).
import { escHtml } from "./html.js";

export function formatDuration(seconds) {
  const total = Number(seconds || 0);
  if (!Number.isFinite(total) || total <= 0) return "—";
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  // "283 days", not "6787h 44m" (#311); hours stop mattering after two days.
  if (hours >= 48) return `${Math.floor(hours / 24)} days`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

// The browser's local date and time, escaped for HTML; "—" without a value.
export function formatTimestamp(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return escHtml(value);
  return escHtml(parsed.toLocaleString());
}

// "2026-09-29 13:05 +02:00": local time with its offset, so a ticket read
// in another time zone is still right (#227).
export function formatReportTime(value) {
  const parsed = new Date(value || "");
  if (!value || Number.isNaN(parsed.valueOf())) return "";
  const pad = (number) => String(number).padStart(2, "0");
  const offset = -parsed.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const zone = `${sign}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`;
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} `
    + `${pad(parsed.getHours())}:${pad(parsed.getMinutes())} ${zone}`;
}

// Today: the time only; otherwise the date too.
export function formatFeedTime(value, now = new Date()) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return "";
  const time = parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (parsed.toDateString() === now.toDateString()) return time;
  return `${parsed.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}

// "3:42", or "1:02:05" from an hour.
export function formatCountdown(totalSeconds) {
  const seconds = Math.max(0, Math.ceil(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = String(seconds % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

export function formatClock(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// A search's words, lower case: every one must be found (#342, #343).
export function searchWords(query) {
  return (query || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
}

// Whether lower-cased *text* has all of *words* (from searchWords).
export function hasAllWords(text, words) {
  return words.every((word) => text.includes(word));
}

// A site's page in Nautobot (the map's panel, Copy site, the coordinate list, #368).
export function nautobotLocationUrl(nautobotUrl, locationId) {
  return `${nautobotUrl.replace(/\/+$/, "")}/dcim/locations/${encodeURIComponent(locationId)}/`;
}

// A circuit's page in Nautobot (the map's panel #235, the circuit mapping #385).
export function nautobotCircuitUrl(nautobotUrl, circuitId) {
  return `${nautobotUrl.replace(/\/+$/, "")}/circuits/circuits/${encodeURIComponent(circuitId)}/`;
}
