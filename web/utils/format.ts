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

// The API's own message for a failed request ({"error": ...}), else "".
export function apiError(err: unknown): string {
  return (err as { data?: { error?: unknown } } | null)?.data?.error?.toString() || "";
}

// What went wrong with a request: the API's message, else the error's own.
export function errorMessage(err: unknown): string {
  return apiError(err) || (err instanceof Error ? err.message : String(err));
}

// A site's page in Nautobot (#368).
export function nautobotLocationUrl(nautobotUrl: string, locationId: string): string {
  return `${nautobotUrl.replace(/\/+$/, "")}/dcim/locations/${encodeURIComponent(locationId)}/`;
}

// A circuit's page in Nautobot (#235, #385).
export function nautobotCircuitUrl(nautobotUrl: string, circuitId: string): string {
  return `${nautobotUrl.replace(/\/+$/, "")}/circuits/circuits/${encodeURIComponent(circuitId)}/`;
}

// The switch and port an access point is on (#524): "Connected to
// hel0008-acc-sw02 · Gi1/0/14".
export function neighbourText(neighbour: { device: string; port?: string | null } | null | undefined): string {
  if (!neighbour?.device) return "";
  return [`Connected to ${neighbour.device}`, neighbour.port || ""].filter(Boolean).join(" · ");
}

// Down access points behind one switch (#524): "12 of 16 behind hel0008-acc-sw02".
export function behindText(behind: { device: string; down: number; total: number } | null | undefined): string {
  if (!behind?.device) return "";
  return `${behind.down.toLocaleString("en")} of ${behind.total.toLocaleString("en")} behind ${behind.device}`;
}
