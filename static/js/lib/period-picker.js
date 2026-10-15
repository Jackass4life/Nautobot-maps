// The period button (#341): a calendar and quick choices in a popover.
// Availability and the maintenance history (#353) share it; the markup is in
// their templates (.period-field with .period-button, .period-popover,
// .period-preset and .period-calendar).  No page access at import.
import { createRangeCalendar, formatRange, quickPeriod } from "./calendar.js";

// options: {from, to, min, max, today, onPick(from, to)}.
export function initPeriodPicker(field, options) {
  const button = field.querySelector(".period-button");
  const popover = field.querySelector(".period-popover");
  let current = { from: options.from, to: options.to };

  const show = (from, to) => {
    current = { from, to };
    button.textContent = formatRange(from, to) || "Choose a period";
  };
  show(options.from, options.to);
  button.hidden = false;

  function close(returnFocus) {
    popover.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (returnFocus) button.focus();
  }

  function pick({ from, to }) {
    show(from, to);
    close(false);
    options.onPick(from, to);
  }

  const calendar = createRangeCalendar(field.querySelector(".period-calendar"), {
    ...options,
    onPick: (from, to) => pick({ from, to }),
  });

  for (const preset of field.querySelectorAll(".period-preset")) {
    const period = quickPeriod(preset.dataset.preset, options.today);
    if (options.min && period.from < options.min) {
      preset.disabled = true;
      preset.title = "Older than the history kept";
    }
    preset.addEventListener("click", () => pick(period));
  }

  button.addEventListener("click", () => {
    if (!popover.hidden) {
      close(false);
      return;
    }
    calendar.set(current.from, current.to);
    popover.hidden = false;
    button.setAttribute("aria-expanded", "true");
    calendar.focus();
  });

  popover.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
    }
  });

  // A click outside closes it.  The path, not target.closest(): a day's click
  // redraws the calendar, so its button is no longer in the page by now.
  document.addEventListener("click", (event) => {
    if (!popover.hidden && !event.composedPath().includes(field)) close(false);
  });
}
