// Days, ranges and "when" (#341, #343; on Nuxt since #463).
import { describe, expect, it } from "vitest";
import { addDays, addMonths, formatRange, monthEnd, monthGrid, parseDay, quickPeriod } from "~/utils/calendar";
import { defaultPlan, TIME_OPTIONS, whenRequest, whenSummary, type WhenValues } from "~/utils/when";

describe("days", () => {
  it("parses only real days", () => {
    expect(parseDay("2026-02-28")).not.toBeNull();
    expect(parseDay("2026-02-30")).toBeNull();
    expect(parseDay("26-1-1")).toBeNull();
  });

  it("adds days and months, a month's end", () => {
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addMonths("2026-01-31", 1)).toBe("2026-02-28");
    expect(monthEnd("2024-02-10")).toBe("2024-02-29");
  });

  it("says a range plainly", () => {
    expect(formatRange("2026-10-01", "2026-10-31")).toBe("October 2026");
    expect(formatRange("2026-10-03", "2026-10-03")).toBe("3 Oct 2026");
    expect(formatRange("2026-09-03", "2026-09-17")).toBe("3 – 17 Sep 2026");
    expect(formatRange("2026-09-28", "2026-10-04")).toBe("28 Sep – 4 Oct 2026");
    expect(formatRange("2026-12-28", "2027-01-04")).toBe("28 Dec 2026 – 4 Jan 2027");
  });

  it("quick choices", () => {
    expect(quickPeriod("this-month", "2026-10-14")).toEqual({ from: "2026-10-01", to: "2026-10-31" });
    expect(quickPeriod("last-month", "2026-10-14")).toEqual({ from: "2026-09-01", to: "2026-09-30" });
    expect(quickPeriod("last-7", "2026-10-14")).toEqual({ from: "2026-10-08", to: "2026-10-14" });
  });

  it("a month's grid: weeks from Monday, limits, the range", () => {
    const grid = monthGrid("2026-10-01", { from: "2026-10-05", to: "2026-10-07", min: "2026-10-02", max: "2026-10-20", today: "2026-10-14" });
    expect(grid.title).toBe("October 2026");
    expect(grid.lead).toBe(3); // 1 Oct 2026 is a Thursday
    expect(grid.days).toHaveLength(31);
    expect(grid.days[0]!.disabled).toBe(true);
    expect(grid.days.filter((d) => d.inRange).map((d) => d.date)).toEqual([5, 6, 7]);
    expect(grid.days[13]!.today).toBe(true);
    expect(grid.prevOk).toBe(false);
    expect(grid.nextOk).toBe(false);
  });
});

describe("when", () => {
  const plan = (extra: Partial<WhenValues>): WhenValues => ({
    when: "planned",
    duration: 120,
    startDay: "2026-10-15",
    startTime: "22:00",
    endDay: "2026-10-15",
    endTime: "02:00",
    ...extra,
  });

  it("15-minute steps; from the next full hour for two hours", () => {
    expect(TIME_OPTIONS).toHaveLength(96);
    expect(TIME_OPTIONS[1]).toBe("00:15");
    const first = defaultPlan(new Date(2026, 9, 14, 13, 40));
    expect(first).toMatchObject({ startTime: "14:00", endTime: "16:00", startDay: "2026-10-14" });
  });

  it("one day with To before From runs overnight", () => {
    expect(whenSummary(plan({}))).toBe("Thu 15 Oct 22:00 → Fri 16 Oct 02:00 (4h)");
    const request = whenRequest(plan({}));
    expect("starts_at" in request && new Date(request.ends_at).getTime() - new Date(request.starts_at).getTime()).toBe(4 * 3600 * 1000);
  });

  it("now for a while, or why it isn't a window", () => {
    expect(whenRequest(plan({ when: "now" }))).toEqual({ duration_minutes: 120 });
    expect(whenRequest(plan({ endDay: "2026-10-14", endTime: "10:00" }))).toEqual({ error: "To must be after From." });
    expect(whenRequest(plan({ startDay: "" }))).toEqual({ error: "Choose the days and times." });
  });
});
