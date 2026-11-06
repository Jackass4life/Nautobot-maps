// Days for picking a range (#341; static/js/lib/calendar.js): "2026-10-03"
// strings, worked out in UTC so no time zone moves a day.

export const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
export const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

export function parseDay(day: string | null | undefined): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day || "");
  if (!match) return null;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  // 2026-02-30 rolls over to March: not a day.
  return date.getUTCDate() === Number(match[3]) ? date : null;
}

export function formatDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function addDays(day: string, days: number): string {
  const date = parseDay(day)!;
  date.setUTCDate(date.getUTCDate() + days);
  return formatDay(date);
}

// The same day *months* later, or the month's last day ("2026-01-31" + 1 → "2026-02-28").
export function addMonths(day: string, months: number): string {
  const date = parseDay(day)!;
  const target = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + months, 1));
  const last = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(date.getUTCDate(), last));
  return formatDay(target);
}

export function monthStart(day: string): string {
  return `${day.slice(0, 7)}-01`;
}

export function monthEnd(day: string): string {
  return addDays(addMonths(monthStart(day), 1), -1);
}

// "3 – 17 Sep 2026", "28 Sep – 4 Oct 2026", "3 Oct 2026", "October 2026".
export function formatRange(from: string, to: string): string {
  const a = parseDay(from);
  const b = parseDay(to);
  if (!a || !b) return "";
  const short = (date: Date) => MONTH_NAMES[date.getUTCMonth()]!.slice(0, 3);
  if (from === monthStart(from) && to === monthEnd(from)) return `${MONTH_NAMES[a.getUTCMonth()]} ${a.getUTCFullYear()}`;
  if (from === to) return `${a.getUTCDate()} ${short(a)} ${a.getUTCFullYear()}`;
  if (a.getUTCFullYear() !== b.getUTCFullYear()) {
    return `${a.getUTCDate()} ${short(a)} ${a.getUTCFullYear()} – ${b.getUTCDate()} ${short(b)} ${b.getUTCFullYear()}`;
  }
  if (a.getUTCMonth() !== b.getUTCMonth()) return `${a.getUTCDate()} ${short(a)} – ${b.getUTCDate()} ${short(b)} ${b.getUTCFullYear()}`;
  return `${a.getUTCDate()} – ${b.getUTCDate()} ${short(b)} ${b.getUTCFullYear()}`;
}

export type QuickChoice = "this-month" | "last-month" | "last-7" | "last-30";
export const QUICK_CHOICES: { id: QuickChoice; label: string }[] = [
  { id: "this-month", label: "This month" },
  { id: "last-month", label: "Last month" },
  { id: "last-7", label: "Last 7 days" },
  { id: "last-30", label: "Last 30 days" },
];

// A quick choice's days, with *today* "2026-10-14" (#341).  A month runs to its last day.
export function quickPeriod(choice: QuickChoice, today: string): { from: string; to: string } {
  const lastMonth = addMonths(monthStart(today), -1);
  switch (choice) {
    case "this-month":
      return { from: monthStart(today), to: monthEnd(today) };
    case "last-month":
      return { from: lastMonth, to: monthEnd(lastMonth) };
    case "last-7":
      return { from: addDays(today, -6), to: today };
    case "last-30":
      return { from: addDays(today, -29), to: today };
  }
}

export function clampDay(day: string, { min, max }: { min?: string; max?: string }): string {
  if (min && day < min) return min;
  if (max && day > max) return max;
  return day;
}

export interface CalendarDay {
  day: string;
  date: number;
  label: string;
  disabled: boolean;
  inRange: boolean;
  start: boolean;
  end: boolean;
  today: boolean;
}

// One month as weeks from Monday: *lead* blanks, then its days.  *range*:
// {from, to} so far (to may be empty); *min*, *max*: "" for no limit.
export function monthGrid(
  month: string,
  { from = "", to = "", min = "", max = "", today = "" }: { from?: string; to?: string; min?: string; max?: string; today?: string },
): { title: string; lead: number; days: CalendarDay[]; prevOk: boolean; nextOk: boolean } {
  const first = parseDay(monthStart(month))!;
  const short = MONTH_NAMES[first.getUTCMonth()]!.slice(0, 3);
  const last = monthEnd(month);
  const end = to || from;
  const days: CalendarDay[] = [];
  for (let date = 1, day = formatDay(first); day <= last; date += 1, day = addDays(day, 1)) {
    days.push({
      day,
      date,
      label: `${date} ${short} ${first.getUTCFullYear()}${day === today ? " (today)" : ""}`,
      disabled: Boolean((min && day < min) || (max && day > max)),
      inRange: Boolean(from && day >= from && day <= end),
      start: day === from,
      end: day === end,
      today: day === today,
    });
  }
  return {
    title: `${MONTH_NAMES[first.getUTCMonth()]} ${first.getUTCFullYear()}`,
    lead: (first.getUTCDay() + 6) % 7,
    days,
    prevOk: !min || monthEnd(addMonths(monthStart(month), -1)) >= min,
    nextOk: !max || addMonths(monthStart(month), 1) <= max,
  };
}
