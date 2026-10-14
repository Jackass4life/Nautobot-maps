"""The range calendar for the availability period (#341)."""

from tests.js_modules import run_js


class TestCalendarDays:
    def test_days_months_and_labels(self):
        run_js("""
import { addDays, addMonths, formatRange, monthEnd, parseDay } from "@js/lib/calendar.js";

// Not every month has 31 days: the calendar never offers a day that isn't.
check(parseDay("2026-02-30") === null && parseDay("2026-9-1") === null && parseDay("") === null, "not days");
check(parseDay("2024-02-29") !== null && parseDay("2026-02-29") === null, "leap years");
check(monthEnd("2026-02-10") === "2026-02-28" && monthEnd("2024-02-01") === "2024-02-29", "February");
check(monthEnd("2026-09-01") === "2026-09-30" && monthEnd("2026-12-31") === "2026-12-31", "30 and 31");
check(addMonths("2026-01-31", 1) === "2026-02-28", addMonths("2026-01-31", 1));
check(addMonths("2026-03-31", -1) === "2026-02-28" && addMonths("2026-12-15", 1) === "2027-01-15", "across years");
check(addDays("2026-02-28", 1) === "2026-03-01" && addDays("2026-01-01", -1) === "2025-12-31", "across months");

check(formatRange("2026-10-01", "2026-10-31") === "October 2026", formatRange("2026-10-01", "2026-10-31"));
check(formatRange("2026-09-03", "2026-09-17") === "3 – 17 Sep 2026", formatRange("2026-09-03", "2026-09-17"));
check(formatRange("2026-09-28", "2026-10-04") === "28 Sep – 4 Oct 2026", formatRange("2026-09-28", "2026-10-04"));
check(formatRange("2025-12-20", "2026-01-05") === "20 Dec 2025 – 5 Jan 2026", formatRange("2025-12-20", "2026-01-05"));
check(formatRange("2026-10-03", "2026-10-03") === "3 Oct 2026", formatRange("2026-10-03", "2026-10-03"));
check(formatRange("", "2026-10-03") === "", "nothing to show");
""")

    def test_quick_choices(self):
        run_js("""
import { quickPeriod, quickPeriodAllowed } from "@js/lib/calendar.js";

const today = "2026-03-14";
check(quickPeriod("this-month", today).month === "2026-03", "this month");
check(quickPeriod("last-month", "2026-01-31").month === "2025-12", "last month across a year");
const week = quickPeriod("last-7", today);
check(week.from === "2026-03-08" && week.to === today, JSON.stringify(week));
const thirty = quickPeriod("last-30", today);
check(thirty.from === "2026-02-13" && thirty.to === today, JSON.stringify(thirty));
check(quickPeriod("nope", today) === null, "unknown");
// Older than the history kept: not offered.
check(quickPeriodAllowed({ month: "2026-02" }, "") && quickPeriodAllowed({ month: "2026-02" }, "2026-02-01"), "allowed");
check(!quickPeriodAllowed({ month: "2026-02" }, "2026-02-02") && !quickPeriodAllowed(thirty, "2026-02-20"), "refused");
""")


class TestCalendarGrid:
    def test_a_month_grid(self):
        run_js("""
import { monthGridHtml } from "@js/lib/calendar.js";

// October 2026 starts on a Thursday: three blanks before it (weeks from Monday).
const html = monthGridHtml("2026-10-01", {
  from: "2026-10-03", to: "2026-10-09", min: "2026-10-02", max: "2026-10-14", focus: "2026-10-09", today: "2026-10-14",
});
check((html.match(/cal-blank/g) || []).length === 3, "blanks");
check((html.match(/class="cal-day/g) || []).length === 31, "31 days");
check(html.includes(">October 2026<") && html.includes(">Mo<") && html.includes(">Su<"), "head");
const day = (d) => html.match(new RegExp(`<button[^>]*data-day="2026-10-${d}"[^>]*>`))[0];
check(day("01").includes(" disabled") && day("15").includes(" disabled") && !day("02").includes("disabled"), "limits");
check(day("03").includes("range-start") && day("03").includes('aria-pressed="true"'), day("03"));
check(day("05").includes("in-range") && !day("05").includes("range-start"), day("05"));
check(day("09").includes("range-end") && day("09").includes('tabindex="0"'), day("09"));
check(day("10").includes('tabindex="-1"') && !day("10").includes("in-range"), day("10"));
check(day("14").includes("is-today") && day("14").includes('aria-label="14 Oct 2026 (today)"'), day("14"));
// No month to go to before the oldest day, or after today.
check(html.includes('data-step="-1" aria-label="Previous month" disabled') && html.includes('data-step="1" aria-label="Next month" disabled'), "nav");
const sept = monthGridHtml("2026-09-01", { from: "", to: "", min: "", max: "", focus: "2026-09-01", today: "2026-10-14" });
check((sept.match(/class="cal-day/g) || []).length === 30 && !sept.includes(" disabled>"), "September: 30 days, no limits");
check(!sept.includes("in-range"), "no range yet");
""")

    def test_picking_a_range_with_the_mouse_and_keys(self):
        run_js("""
import { createRangeCalendar } from "@js/lib/calendar.js";

const handlers = {};
const root = element("calendar", {
  addEventListener: (type, fn) => { handlers[type] = fn; },
  querySelector: () => null,
  querySelectorAll: () => [],
});
const picks = [];
const calendar = createRangeCalendar(root, {
  from: "2026-10-01", to: "2026-10-14", min: "2026-09-01", max: "2026-10-14", today: "2026-10-14",
  onPick: (from, to) => picks.push(`${from}/${to}`),
});
check(root.innerHTML.includes(">October 2026<") && root.innerHTML.includes('data-day="2026-10-14" tabindex="0"'), "opens on the range's end");

const day = (value, disabled = false) => ({ target: { closest: (sel) => (sel === ".cal-day" ? { dataset: { day: value }, disabled } : null) } });
const nav = (step) => ({ target: { closest: (sel) => (sel === ".cal-nav" ? { dataset: { step: String(step) } } : null) } });

// Back a month, then the 20th and the 5th: either order makes a range.
handlers.click(nav(-1));
check(root.innerHTML.includes(">September 2026<"), "September");
handlers.click(day("2026-09-20"));
check(picks.length === 0 && /class="[^"]*range-start[^"]*" data-day="2026-09-20"/.test(root.innerHTML), "the first click starts a range");
check(!/class="[^"]*in-range[^"]*" data-day="2026-09-21"/.test(root.innerHTML), "only the first day so far");
handlers.click(day("2026-09-05"));
check(picks.join() === "2026-09-05/2026-09-20", picks.join());
// A disabled day does nothing.
handlers.click(day("2026-08-31", true));
check(picks.length === 1, "disabled");

// Keys: PageDown to the next month, End to Sunday; clamped to today.
let prevented = 0;
const key = (k, value) => handlers.keydown({ key: k, preventDefault: () => { prevented += 1; },
  target: { closest: (sel) => (sel === ".cal-day" ? { dataset: { day: value } } : null) } });
key("PageDown", "2026-09-30");
check(root.innerHTML.includes(">October 2026<") && root.innerHTML.includes('data-day="2026-10-14" tabindex="0"'), "clamped to today");
key("ArrowUp", "2026-10-14");
check(root.innerHTML.includes('data-day="2026-10-07" tabindex="0"'), "a week back");
key("Home", "2026-10-07");
check(root.innerHTML.includes('data-day="2026-10-05" tabindex="0"'), "Monday");
key("a", "2026-10-05");
check(prevented === 3, `only moves: ${prevented}`);

// A quick choice shows its days without a pick.
calendar.set("2026-09-01", "2026-09-30");
check(root.innerHTML.includes(">September 2026<") && picks.length === 1, "set");
""")
