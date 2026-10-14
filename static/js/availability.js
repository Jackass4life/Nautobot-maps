// The availability page (#287) is rendered on the server.  This keeps the
// light/dark choice the other pages use, turns the two date fields into a
// button with a calendar and quick choices (#341), and makes the tables
// sortable and searchable, the worst first (#342).  An entry module, like */main.js.
import { initAvailabilityTable } from "./availability/table.js";
import { createRangeCalendar, formatRange, quickPeriod } from "./lib/calendar.js";
import { initThemeToggle } from "./lib/theme.js";
import { initInfoTips } from "./lib/tooltip.js";

initThemeToggle(document.getElementById("theme-toggle"));
initInfoTips();
for (const section of document.querySelectorAll("[data-avail-table]")) initAvailabilityTable(section);

function initPeriodPicker(form) {
  const fromInput = form.querySelector("#availability-from");
  const toInput = form.querySelector("#availability-to");
  const button = form.querySelector("#period-button");
  const popover = form.querySelector("#period-popover");
  const { today, oldestDay } = form.dataset;

  const showPeriod = () => {
    button.textContent = formatRange(fromInput.value, toInput.value) || "Choose a period";
  };
  showPeriod();
  form.querySelector(".period-inputs").hidden = true;
  button.hidden = false;

  function close(returnFocus) {
    popover.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (returnFocus) button.focus();
  }

  // Always from and to: the server reports a whole month as that month.
  function submit({ from, to }) {
    fromInput.value = from;
    toInput.value = to;
    showPeriod();
    close(false);
    form.requestSubmit();
  }

  const calendar = createRangeCalendar(form.querySelector("#period-calendar"), {
    from: fromInput.value,
    to: toInput.value,
    min: oldestDay,
    max: today,
    today,
    onPick: (from, to) => submit({ from, to }),
  });

  for (const preset of form.querySelectorAll(".period-preset")) {
    const period = quickPeriod(preset.dataset.preset, today);
    if (oldestDay && period.from < oldestDay) {
      preset.disabled = true;
      preset.title = "Older than the history kept";
    }
    preset.addEventListener("click", () => submit(period));
  }

  button.addEventListener("click", () => {
    if (!popover.hidden) {
      close(false);
      return;
    }
    calendar.set(fromInput.value, toInput.value);
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
  const field = form.querySelector(".period-field");
  document.addEventListener("click", (event) => {
    if (!popover.hidden && !event.composedPath().includes(field)) close(false);
  });
}

const form = document.getElementById("availability-form");
if (form) initPeriodPicker(form);
