// "When" for a maintenance window (#343; static/js/lib/when.js): now for some
// hours, or planned days with times in 15-minute steps, in the browser's time zone.
import { addDays } from "./calendar";

// "Now, for …", in minutes.
export const DURATIONS = [60, 120, 240, 480];

export interface WhenValues {
  when: "now" | "planned";
  duration: number;
  startDay: string;
  startTime: string;
  endDay: string;
  endTime: string;
}

// "2026-10-14" in the browser's time zone.
export function localDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function localTime(date: Date): string {
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

// The planned fields' first values: from the next full hour, for two hours.
export function defaultPlan(now: Date): Pick<WhenValues, "startDay" | "startTime" | "endDay" | "endTime"> {
  const start = new Date(now);
  start.setMinutes(0, 0, 0);
  start.setHours(start.getHours() + 1);
  const end = new Date(start.getTime() + 2 * 3600 * 1000);
  return { startDay: localDay(start), startTime: localTime(start), endDay: localDay(end), endTime: localTime(end) };
}

export const TIME_OPTIONS: string[] = Array.from({ length: 96 }, (_, i) => {
  const minutes = i * 15;
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
});

// The window's local start and end; on one day, an end at or before the
// start is the next morning (22:00 to 02:00 is overnight work).
function plannedTimes(values: WhenValues): { start: Date; end: Date } {
  const overnight = values.startDay && values.startDay === values.endDay && values.endTime <= values.startTime;
  const endDay = overnight ? addDays(values.endDay, 1) : values.endDay;
  return { start: new Date(`${values.startDay}T${values.startTime}`), end: new Date(`${endDay}T${values.endTime}`) };
}

// "Thu 15 Oct 22:00 → Fri 16 Oct 02:00 (4h)", or why it isn't a window.
export function whenSummary(values: WhenValues): string {
  const { start, end } = plannedTimes(values);
  if (Number.isNaN(start.valueOf()) || Number.isNaN(end.valueOf())) return "Choose the days and times.";
  if (end <= start) return "To must be after From.";
  const at = (date: Date) =>
    `${date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })} ${localTime(date)}`;
  const minutes = Math.round((end.getTime() - start.getTime()) / 60000);
  const length =
    minutes >= 48 * 60 ? `${Math.floor(minutes / 1440)} days` : `${Math.floor(minutes / 60)}h${minutes % 60 ? ` ${minutes % 60}m` : ""}`;
  return `${at(start)} → ${at(end)} (${length})`;
}

export type WhenRequest = { duration_minutes: number } | { starts_at: string; ends_at: string } | { error: string };

// The request's time fields, or {error}.
export function whenRequest(values: WhenValues): WhenRequest {
  if (values.when !== "planned") return { duration_minutes: Number(values.duration) };
  const { start, end } = plannedTimes(values);
  if (Number.isNaN(start.valueOf()) || Number.isNaN(end.valueOf())) return { error: "Choose the days and times." };
  if (end <= start) return { error: "To must be after From." };
  return { starts_at: start.toISOString(), ends_at: end.toISOString() };
}
