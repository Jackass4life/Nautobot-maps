// "When" for a maintenance window (#343): now for some hours, or planned days
// picked on the calendar (#341) with times in 15-minute steps, in the
// browser's time zone.  The board's Maint. panel and the Maintenance page
// share it.  No page access at import.
import { addDays, createRangeCalendar } from "./calendar.js";
import { escHtml } from "./html.js";

// "Now, for …", in minutes.
export const DURATIONS = [60, 120, 240, 480];

// "2026-10-14" in the browser's time zone (the calendar's days are local here).
export function localDay(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

// "22:15"
function localTime(date) {
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

// The planned fields' first values: from the next full hour, for two hours.
export function defaultPlan(now) {
  const start = new Date(now);
  start.setMinutes(0, 0, 0);
  start.setHours(start.getHours() + 1);
  const end = new Date(start.getTime() + 2 * 3600 * 1000);
  return { startDay: localDay(start), startTime: localTime(start), endDay: localDay(end), endTime: localTime(end) };
}

export function timeOptionsHtml(selected) {
  const options = [];
  for (let minutes = 0; minutes < 24 * 60; minutes += 15) {
    const value = `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
    options.push(`<option value="${value}"${value === selected ? " selected" : ""}>${value}</option>`);
  }
  return options.join("");
}

// The fieldset; *plan* is defaultPlan()'s, *zone* the time zone's name.
export function whenFieldsetHtml(plan, zone) {
  const durations = DURATIONS
    .map((minutes) => `<option value="${minutes}"${minutes === 120 ? " selected" : ""}>${minutes / 60} hour${minutes === 60 ? "" : "s"}</option>`)
    .join("");
  return `
    <fieldset class="maint-fieldset when-fieldset">
      <legend>When</legend>
      <label class="maint-choice"><input type="radio" name="maint-when" value="now" checked /> Now, for
        <select class="maint-duration" aria-label="How long">${durations}</select></label>
      <label class="maint-choice"><input type="radio" name="maint-when" value="planned" /> Planned</label>
      <div class="maint-planned" hidden>
        <div class="calendar when-calendar"></div>
        <div class="when-times">
          <p class="when-days" aria-live="polite">${escHtml(whenSummary({ ...plan, when: "planned" }))}</p>
          <label>From <select class="when-start-time" aria-label="Start time">${timeOptionsHtml(plan.startTime)}</select></label>
          <label>To <select class="when-end-time" aria-label="End time">${timeOptionsHtml(plan.endTime)}</select></label>
          <p class="when-hint">Click the first and the last day (the same day twice for one day; a To before the From runs overnight). Times in ${escHtml(zone)}.</p>
        </div>
      </div>
    </fieldset>`;
}

// The window's local start and end; on one day, an end at or before the
// start is the next morning (22:00 to 02:00 is overnight work).
function plannedTimes(values) {
  const overnight = values.startDay && values.startDay === values.endDay && values.endTime <= values.startTime;
  const endDay = overnight ? addDays(values.endDay, 1) : values.endDay;
  return { start: new Date(`${values.startDay}T${values.startTime}`), end: new Date(`${endDay}T${values.endTime}`) };
}

// "Thu 15 Oct 22:00 → Fri 16 Oct 02:00 (4h)", or why it isn't a window.
export function whenSummary(values) {
  const { start, end } = plannedTimes(values);
  if (Number.isNaN(start.valueOf()) || Number.isNaN(end.valueOf())) return "Choose the days and times.";
  if (end <= start) return "To must be after From.";
  const at = (date) => `${date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })} ${localTime(date)}`;
  const minutes = Math.round((end - start) / 60000);
  const length = minutes >= 48 * 60 ? `${Math.floor(minutes / 1440)} days`
    : `${Math.floor(minutes / 60)}h${minutes % 60 ? ` ${minutes % 60}m` : ""}`;
  return `${at(start)} → ${at(end)} (${length})`;
}

// The request's time fields from what was chosen, or {error}.
// values: {when: "now"|"planned", duration, startDay, startTime, endDay, endTime}.
export function whenRequest(values) {
  if (values.when !== "planned") return { duration_minutes: Number(values.duration) };
  const { start, end } = plannedTimes(values);
  if (Number.isNaN(start.valueOf()) || Number.isNaN(end.valueOf())) return { error: "Choose the days and times." };
  if (end <= start) return { error: "To must be after From." };
  return { starts_at: start.toISOString(), ends_at: end.toISOString() };
}

// Wires the calendar in *fieldset* (from whenFieldsetHtml); returns read() for whenRequest().
export function initWhen(fieldset, plan, today) {
  const days = { startDay: plan.startDay, endDay: plan.endDay };
  const label = fieldset.querySelector(".when-days");
  const read = () => ({
    when: fieldset.querySelector('input[name="maint-when"]:checked')?.value,
    duration: fieldset.querySelector(".maint-duration")?.value,
    startDay: days.startDay,
    endDay: days.endDay,
    startTime: fieldset.querySelector(".when-start-time")?.value,
    endTime: fieldset.querySelector(".when-end-time")?.value,
  });
  const showSummary = () => {
    label.textContent = whenSummary(read());
  };
  fieldset.addEventListener("change", (event) => {
    if (event.target.matches(".when-start-time, .when-end-time")) showSummary();
  });
  createRangeCalendar(fieldset.querySelector(".when-calendar"), {
    from: days.startDay,
    to: days.endDay,
    min: today,
    // Planned up to a year ahead; a window itself lasts at most 14 days (the server says so).
    max: addDays(today, 365),
    today,
    onPick(from, to) {
      Object.assign(days, { startDay: from, endDay: to });
      showSummary();
    },
  });
  return read;
}
