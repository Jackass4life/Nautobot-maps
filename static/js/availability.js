// The availability page (#287) is rendered on the server.  This keeps the
// light/dark choice the other pages use, and turns the two date fields into
// a button with a calendar and quick choices (#341).  An entry module, like */main.js.
import { createRangeCalendar, formatRange, monthEnd, quickPeriod, quickPeriodAllowed } from "./lib/calendar.js";
import { initThemeToggle } from "./lib/theme.js";

initThemeToggle(document.getElementById("theme-toggle"));

function initPeriodPicker(form) {
  const fromInput = form.querySelector("#availability-from");
  const toInput = form.querySelector("#availability-to");
  const monthInput = form.querySelector('input[name="month"]');
  const button = form.querySelector("#period-button");
  const popover = form.querySelector("#period-popover");
  const { today, oldestDay } = form.dataset;

  // A month report keeps sending month= (not from and to), so Show keeps it a month.
  const monthMode = Boolean(monthInput.value);
  monthInput.disabled = !monthMode;
  fromInput.disabled = toInput.disabled = monthMode;
  const showPeriod = (from, to) => {
    button.textContent = formatRange(from, to) || "Choose a period";
  };
  showPeriod(fromInput.value, toInput.value);
  form.querySelector(".period-inputs").hidden = true;
  button.hidden = false;

  function close(returnFocus) {
    popover.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (returnFocus) button.focus();
  }

  function submit(period) {
    monthInput.value = period.month || "";
    monthInput.disabled = !period.month;
    fromInput.value = period.from || `${period.month}-01`;
    toInput.value = period.to || monthEnd(`${period.month}-01`);
    // Either a month or from and to: the server refuses both.
    fromInput.disabled = toInput.disabled = Boolean(period.month);
    showPeriod(fromInput.value, toInput.value);
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
    if (!quickPeriodAllowed(period, oldestDay)) {
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

  // A click outside closes it.
  document.addEventListener("click", (event) => {
    if (!popover.hidden && !event.target.closest(".period-field")) close(false);
  });
}

const form = document.getElementById("availability-form");
if (form) initPeriodPicker(form);
