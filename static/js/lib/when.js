// "When" for a maintenance window (#343): now for some hours, or planned days
// picked on the calendar (#341) with times in 15-minute steps, in the
// browser's time zone.  The board's Maint. panel and the Maintenance page
// share it.  No page access at import.
import { addDays, createRangeCalendar, formatRange } from "./calendar.js";
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
          <p class="when-days">${escHtml(formatRange(plan.startDay, plan.endDay))}</p>
          <label>From <select class="when-start-time" aria-label="Start time">${timeOptionsHtml(plan.startTime)}</select></label>
          <label>To <select class="when-end-time" aria-label="End time">${timeOptionsHtml(plan.endTime)}</select></label>
          <p class="when-hint">Click the first and the last day (the same day twice for one day). Times in ${escHtml(zone)}.</p>
        </div>
      </div>
    </fieldset>`;
}

// The request's time fields from what was chosen, or {error}.
// values: {when: "now"|"planned", duration, startDay, startTime, endDay, endTime}.
export function whenRequest(values) {
  if (values.when !== "planned") return { duration_minutes: Number(values.duration) };
  const start = new Date(`${values.startDay}T${values.startTime}`);
  const end = new Date(`${values.endDay}T${values.endTime}`);
  if (Number.isNaN(start.valueOf()) || Number.isNaN(end.valueOf())) return { error: "Choose the days and times." };
  if (end <= start) return { error: "To must be after From." };
  return { starts_at: start.toISOString(), ends_at: end.toISOString() };
}

// Wires the calendar in *fieldset* (from whenFieldsetHtml); returns read() for whenRequest().
export function initWhen(fieldset, plan, today) {
  const days = { startDay: plan.startDay, endDay: plan.endDay };
  const label = fieldset.querySelector(".when-days");
  createRangeCalendar(fieldset.querySelector(".when-calendar"), {
    from: days.startDay,
    to: days.endDay,
    min: today,
    // Planned up to a year ahead; a window itself lasts at most 14 days (the server says so).
    max: addDays(today, 365),
    today,
    onPick(from, to) {
      Object.assign(days, { startDay: from, endDay: to });
      label.textContent = formatRange(from, to);
    },
  });
  return () => ({
    when: fieldset.querySelector('input[name="maint-when"]:checked')?.value,
    duration: fieldset.querySelector(".maint-duration")?.value,
    startDay: days.startDay,
    endDay: days.endDay,
    startTime: fieldset.querySelector(".when-start-time")?.value,
    endTime: fieldset.querySelector(".when-end-time")?.value,
  });
}
