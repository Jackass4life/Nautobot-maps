"""When a maintenance window is: now, or planned on the calendar (#343)."""

from tests.js_modules import run_js


def test_planned_days_and_times():
    run_js(
        """
import { defaultPlan, localDay, timeOptionsHtml, whenRequest } from "@js/lib/when.js";

// The next full hour, for two hours; across midnight the days follow.
let plan = defaultPlan(new Date("2026-10-14T13:20:00+02:00"));
check(JSON.stringify(plan) === '{"startDay":"2026-10-14","startTime":"14:00","endDay":"2026-10-14","endTime":"16:00"}', JSON.stringify(plan));
plan = defaultPlan(new Date("2026-10-14T22:59:00+02:00"));
check(plan.startTime === "23:00" && plan.endDay === "2026-10-15" && plan.endTime === "01:00", JSON.stringify(plan));
check(localDay(new Date("2026-10-14T23:30:00Z")) === "2026-10-15", "local, not UTC");

// 96 quarter hours.
const options = timeOptionsHtml("06:15");
check((options.match(/<option/g) || []).length === 96 && options.includes('<option value="06:15" selected>'), "times");

// Local time sent as UTC; the change to winter time (25 October 2026) counts.
const planned = { when: "planned", startDay: "2026-10-24", startTime: "22:00", endDay: "2026-10-25", endTime: "04:00" };
const body = whenRequest(planned);
check(body.starts_at === "2026-10-24T20:00:00.000Z" && body.ends_at === "2026-10-25T03:00:00.000Z", JSON.stringify(body));
check(JSON.stringify(whenRequest({ when: "now", duration: "240" })) === '{"duration_minutes":240}', "now");
check(whenRequest({ ...planned, startDay: "" }).error === "Choose the days and times.", "missing");
check(whenRequest({ ...planned, endDay: "2026-10-24", endTime: "22:00" }).error === "To must be after From.", "same time");
""",
        tz="Europe/Copenhagen",
    )
