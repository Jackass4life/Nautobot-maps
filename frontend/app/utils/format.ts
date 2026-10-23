// Times, search words and Nautobot links, as the old pages have them
// (static/js/lib/format.js).

// Today: the time only; otherwise the date too (the viewer's time zone).
export function formatFeedTime(value: string | null | undefined, now: Date = new Date()): string {
  const parsed = new Date(value ?? "");
  if (Number.isNaN(parsed.valueOf())) return "";
  const time = parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (parsed.toDateString() === now.toDateString()) return time;
  return `${parsed.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}

// A search's words, lower case: every one must be found (#342, #343).
export function searchWords(query: string | null | undefined): string[] {
  return (query || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
}

export function hasAllWords(text: string, words: string[]): boolean {
  return words.every((word) => text.includes(word));
}

// A site's page in Nautobot (#368).
export function nautobotLocationUrl(nautobotUrl: string, locationId: string): string {
  return `${nautobotUrl.replace(/\/+$/, "")}/dcim/locations/${encodeURIComponent(locationId)}/`;
}

// A circuit's page in Nautobot (#235, #385).
export function nautobotCircuitUrl(nautobotUrl: string, circuitId: string): string {
  return `${nautobotUrl.replace(/\/+$/, "")}/circuits/circuits/${encodeURIComponent(circuitId)}/`;
}
