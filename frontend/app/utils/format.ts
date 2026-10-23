// Times as the old pages show them (static/js/lib/format.js).

// Today: the time only; otherwise the date too (the viewer's time zone).
export function formatFeedTime(value: string | null | undefined, now: Date = new Date()): string {
  const parsed = new Date(value ?? "");
  if (Number.isNaN(parsed.valueOf())) return "";
  const time = parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (parsed.toDateString() === now.toDateString()) return time;
  return `${parsed.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}
