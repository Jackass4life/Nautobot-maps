// The availability page (#287) is rendered on the server.  This keeps the
// light/dark choice the other pages use, turns the two date fields into a
// button with a calendar and quick choices (#341), and makes the tables
// sortable and searchable, the worst first (#342).  An entry module, like */main.js.
import { initAvailabilityTable } from "./availability/table.js";
import { initPeriodPicker } from "./lib/period-picker.js";
import { initThemeToggle } from "./lib/theme.js";
import { initInfoTips } from "./lib/tooltip.js";

initThemeToggle(document.getElementById("theme-toggle"));
initInfoTips();
for (const section of document.querySelectorAll("[data-avail-table]")) initAvailabilityTable(section);

// The two date fields give way to the period button (lib/period-picker.js);
// a pick fills them in and shows the report.  Always from and to: the server
// reports a whole month as that month.
const form = document.getElementById("availability-form");
if (form) {
  const fromInput = form.querySelector("#availability-from");
  const toInput = form.querySelector("#availability-to");
  const { today, oldestDay } = form.dataset;
  form.querySelector(".period-inputs").hidden = true;
  initPeriodPicker(form.querySelector(".period-field"), {
    from: fromInput.value,
    to: toInput.value,
    min: oldestDay,
    max: today,
    today,
    onPick(from, to) {
      fromInput.value = from;
      toInput.value = to;
      form.requestSubmit();
    },
  });
}
