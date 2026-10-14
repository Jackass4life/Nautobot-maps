// A calendar for picking a range of days (#341): click the first day, then
// the last; ‹ › change the month.  Days are "2026-10-03" strings in UTC, so
// no time zone moves a day.  No page access at import; createRangeCalendar
// only touches the element it is given.
import { escHtml } from "./html.js";

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];
const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

export function parseDay(day) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day || "");
  if (!match) return null;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  // 2026-02-30 rolls over to March: not a day.
  return date.getUTCDate() === Number(match[3]) ? date : null;
}

export function formatDay(date) {
  return date.toISOString().slice(0, 10);
}

export function addDays(day, days) {
  const date = parseDay(day);
  date.setUTCDate(date.getUTCDate() + days);
  return formatDay(date);
}

// The same day *months* later, or the month's last day ("2026-01-31" + 1 → "2026-02-28").
export function addMonths(day, months) {
  const date = parseDay(day);
  const target = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + months, 1));
  const last = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(date.getUTCDate(), last));
  return formatDay(target);
}

export function monthStart(day) {
  return `${day.slice(0, 7)}-01`;
}

export function monthEnd(day) {
  return addDays(addMonths(monthStart(day), 1), -1);
}

// "3 – 17 Sep 2026", "28 Sep – 4 Oct 2026", "3 Oct 2026", "October 2026".
export function formatRange(from, to) {
  const a = parseDay(from);
  const b = parseDay(to);
  if (!a || !b) return "";
  const short = (date) => MONTH_NAMES[date.getUTCMonth()].slice(0, 3);
  if (from === monthStart(from) && to === monthEnd(from)) return `${MONTH_NAMES[a.getUTCMonth()]} ${a.getUTCFullYear()}`;
  if (from === to) return `${a.getUTCDate()} ${short(a)} ${a.getUTCFullYear()}`;
  if (a.getUTCFullYear() !== b.getUTCFullYear()) {
    return `${a.getUTCDate()} ${short(a)} ${a.getUTCFullYear()} – ${b.getUTCDate()} ${short(b)} ${b.getUTCFullYear()}`;
  }
  if (a.getUTCMonth() !== b.getUTCMonth()) return `${a.getUTCDate()} ${short(a)} – ${b.getUTCDate()} ${short(b)} ${b.getUTCFullYear()}`;
  return `${a.getUTCDate()} – ${b.getUTCDate()} ${short(b)} ${b.getUTCFullYear()}`;
}

// A quick choice's {month: "2026-10"} or {from, to}, with *today* "2026-10-14" (#341).
export function quickPeriod(choice, today) {
  switch (choice) {
    case "this-month": return { month: today.slice(0, 7) };
    case "last-month": return { month: addMonths(monthStart(today), -1).slice(0, 7) };
    case "last-7": return { from: addDays(today, -6), to: today };
    case "last-30": return { from: addDays(today, -29), to: today };
    default: return null;
  }
}

// Whether all of a quick choice's days are on or after *oldest* ("" for no limit).
export function quickPeriodAllowed(period, oldest) {
  if (!oldest) return true;
  return (period.month ? `${period.month}-01` : period.from) >= oldest;
}

// One month as a grid of day buttons, weeks from Monday.  *state*: {from, to}
// (the range so far; to may be empty), min, max ("" for no limit), focus
// (the day that takes Tab), today.
export function monthGridHtml(month, state) {
  const first = parseDay(monthStart(month));
  const lead = (first.getUTCDay() + 6) % 7;
  const last = monthEnd(month);
  const prevOk = !state.min || monthEnd(addMonths(monthStart(month), -1)) >= state.min;
  const nextOk = !state.max || addMonths(monthStart(month), 1) <= state.max;
  const title = `${MONTH_NAMES[first.getUTCMonth()]} ${first.getUTCFullYear()}`;
  const cells = [];
  for (let i = 0; i < lead; i += 1) cells.push('<span class="cal-blank"></span>');
  for (let day = monthStart(month); day <= last; day = addDays(day, 1)) {
    const disabled = (state.min && day < state.min) || (state.max && day > state.max);
    const end = state.to || state.from;
    const classes = ["cal-day"];
    if (state.from && day >= state.from && day <= end) classes.push("in-range");
    if (day === state.from) classes.push("range-start");
    if (day === end) classes.push("range-end");
    if (day === state.today) classes.push("is-today");
    const selected = classes.includes("range-start") || classes.includes("range-end");
    const label = formatRange(day, day);
    cells.push(`<button type="button" class="${classes.join(" ")}" data-day="${day}" tabindex="${day === state.focus ? 0 : -1}"`
      + ` aria-label="${escHtml(label)}${day === state.today ? " (today)" : ""}" aria-pressed="${selected}"${disabled ? " disabled" : ""}>`
      + `${Number(day.slice(8))}</button>`);
  }
  return `
    <div class="cal-head">
      <button type="button" class="cal-nav" data-step="-1" aria-label="Previous month"${prevOk ? "" : " disabled"}>‹</button>
      <span class="cal-title" aria-live="polite">${title}</span>
      <button type="button" class="cal-nav" data-step="1" aria-label="Next month"${nextOk ? "" : " disabled"}>›</button>
    </div>
    <div class="cal-grid" role="group" aria-label="${title}">
      ${WEEKDAYS.map((name) => `<span class="cal-weekday" aria-hidden="true">${name}</span>`).join("")}
      ${cells.join("")}
    </div>`;
}

// Clamp *day* to the calendar's limits.
function clampDay(day, { min, max }) {
  if (min && day < min) return min;
  if (max && day > max) return max;
  return day;
}

// A range calendar in *element*.  options: {from, to, min, max, today, onPick(from, to)}.
// The first click starts a range, the second ends it (either order) and calls onPick.
// Keys: arrows by day and week, PageUp/PageDown by month, Home/End the week.
export function createRangeCalendar(element, options) {
  const state = {
    from: options.from || "",
    to: options.to || "",
    min: options.min || "",
    max: options.max || "",
    today: options.today,
    focus: clampDay(options.to || options.today, options),
    picking: false,
  };
  let month = monthStart(state.focus);

  function render(focusDay = false) {
    element.innerHTML = monthGridHtml(month, state);
    if (focusDay) element.querySelector(`[data-day="${state.focus}"]`)?.focus();
  }

  function moveFocus(day) {
    state.focus = clampDay(day, state);
    month = monthStart(state.focus);
    render(true);
  }

  function pick(day) {
    if (!state.picking) {
      state.from = day;
      state.to = "";
      state.picking = true;
    } else {
      [state.from, state.to] = day < state.from ? [day, state.from] : [state.from, day];
      state.picking = false;
    }
    state.focus = day;
    render(true);
    if (!state.picking) options.onPick(state.from, state.to);
  }

  element.addEventListener("click", (event) => {
    const nav = event.target.closest(".cal-nav");
    if (nav) {
      month = addMonths(month, Number(nav.dataset.step));
      state.focus = clampDay(month, state);
      render();
      return;
    }
    const button = event.target.closest(".cal-day");
    if (button && !button.disabled) pick(button.dataset.day);
  });

  // While picking, the range follows the pointer to show what a click would choose.
  element.addEventListener("mouseover", (event) => {
    const button = event.target.closest(".cal-day");
    if (!state.picking || !button || button.disabled) return;
    const [a, b] = [state.from, button.dataset.day].sort();
    for (const day of element.querySelectorAll(".cal-day")) {
      day.classList.toggle("in-preview", day.dataset.day >= a && day.dataset.day <= b);
    }
  });

  element.addEventListener("keydown", (event) => {
    const button = event.target.closest(".cal-day");
    if (!button) return;
    const day = button.dataset.day;
    const weekday = (parseDay(day).getUTCDay() + 6) % 7;
    const moves = {
      ArrowLeft: () => addDays(day, -1),
      ArrowRight: () => addDays(day, 1),
      ArrowUp: () => addDays(day, -7),
      ArrowDown: () => addDays(day, 7),
      PageUp: () => addMonths(day, -1),
      PageDown: () => addMonths(day, 1),
      Home: () => addDays(day, -weekday),
      End: () => addDays(day, 6 - weekday),
    };
    if (!moves[event.key]) return;
    event.preventDefault();
    moveFocus(moves[event.key]());
  });

  render();
  return {
    focus: () => element.querySelector(`[data-day="${state.focus}"]`)?.focus(),
    // Show *from*–*to* (a quick choice), without calling onPick.
    set(from, to) {
      Object.assign(state, { from, to, picking: false, focus: clampDay(to, state) });
      month = monthStart(state.focus);
      render();
    },
  };
}
