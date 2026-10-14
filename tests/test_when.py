"""When a maintenance window is: now, or planned on the calendar (#343)."""

from tests.js_modules import run_js


def test_planned_days_and_times():
    run_js(
        """
import { defaultPlan, localDay, timeOptionsHtml, whenFieldsetHtml, whenRequest, whenSummary } from "@js/lib/when.js";

// The next full hour, for two hours; across midnight the days follow.
let plan = defaultPlan(new Date("2026-10-14T13:20:00+02:00"));
check(JSON.stringify(plan) === '{"startDay":"2026-10-14","startTime":"14:00","endDay":"2026-10-14","endTime":"16:00"}', JSON.stringify(plan));
plan = defaultPlan(new Date("2026-10-14T22:59:00+02:00"));
check(plan.startTime === "23:00" && plan.endDay === "2026-10-15" && plan.endTime === "01:00", JSON.stringify(plan));
check(localDay(new Date("2026-10-14T23:30:00Z")) === "2026-10-15", "local, not UTC");

// The fieldset: a calendar and times from the next full hour, not typed dates (#343).
const html = whenFieldsetHtml(defaultPlan(new Date("2026-10-09T21:20:00+02:00")), "Europe/Copenhagen");
check(html.includes('class="calendar when-calendar"') && !html.includes("datetime-local"), "calendar");
check(html.includes('<option value="22:00" selected>22:00</option>') && html.includes('<option value="00:00" selected>00:00</option>'), "22:00 to 00:00");
check(html.includes(">Fri 9 Oct 22:00 → Sat 10 Oct 00:00 (2h)<") && html.includes("Times in Europe/Copenhagen"), "days and zone");
check(html.includes('<option value="120" selected>2 hours</option>') && html.includes('<option value="60">1 hour</option>'), "durations");

// 96 quarter hours.
const options = timeOptionsHtml("06:15");
check((options.match(/<option/g) || []).length === 96 && options.includes('<option value="06:15" selected>'), "times");

// Local time sent as UTC; the change to winter time (25 October 2026) counts.
const planned = { when: "planned", startDay: "2026-10-24", startTime: "22:00", endDay: "2026-10-25", endTime: "04:00" };
const body = whenRequest(planned);
check(body.starts_at === "2026-10-24T20:00:00.000Z" && body.ends_at === "2026-10-25T03:00:00.000Z", JSON.stringify(body));
check(JSON.stringify(whenRequest({ when: "now", duration: "240" })) === '{"duration_minutes":240}', "now");
check(whenRequest({ ...planned, startDay: "" }).error === "Choose the days and times.", "missing");
// One day, To at or before From: overnight, into the next morning.
const night = { when: "planned", startDay: "2026-10-15", startTime: "22:00", endDay: "2026-10-15", endTime: "02:00" };
check(whenRequest(night).ends_at === "2026-10-16T00:00:00.000Z", JSON.stringify(whenRequest(night)));
check(whenSummary(night) === "Thu 15 Oct 22:00 → Fri 16 Oct 02:00 (4h)", whenSummary(night));
check(whenSummary({ ...night, startTime: "08:00", endTime: "09:30" }) === "Thu 15 Oct 08:00 → Thu 15 Oct 09:30 (1h 30m)", "day");
check(whenSummary({ ...night, endDay: "2026-10-20", endTime: "06:00" }) === "Thu 15 Oct 22:00 → Tue 20 Oct 06:00 (4 days)", "days");
// Over several days the days are as chosen: To before From is a mistake.
check(whenRequest({ ...planned, endDay: "2026-10-23" }).error === "To must be after From.", "backwards");
check(whenSummary({ ...planned, startDay: "" }) === "Choose the days and times.", "missing");
""",
        tz="Europe/Copenhagen",
    )
