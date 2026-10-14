# Availability

`/availability` (also **Availability** in the top navigation) shows the availability of every monitored site and tenant for a period: "what was the uptime?".

- **The period** is a month or any days you choose (#341). Click **Period** to open a calendar: click the first day, then the last (in either order; ‹ › change the month), or take a quick choice: **This month**, **Last month**, **Last 7 days**, **Last 30 days**. Days in the future, and days older than the history kept, can't be chosen. The keyboard works too: arrows move a day or a week, Page Up/Down a month, Enter picks, Escape closes.
- **The page answers first** (#342): four cards show the availability (the average of the sites), the total downtime and incidents, how many sites are below 99.9 % (and how many had none), and the worst tenant. Below them, the tenants and the sites, the worst first, each with a bar (98–100 %: green from 99.9 %, amber from 99 %, red below) and the downtime as on the board ("2h 14m", "3 days"). Click a column to sort by it; search for a site or tenant, or pick a tenant. The worst 25 sites and 10 tenants are shown, then **Show 25 more** or **Show all**, with how many of the rest had no downtime. A tenant with a description in Nautobot has an **(i)**, as on the board.
- **Availability** = 1 − downtime ÷ the period, in UTC. Both days are included; a period that hasn't ended runs up to now ("so far"). A period is at most 366 days.
- **Downtime** is set with `AVAILABILITY_BASIS`, or with **Downtime** on the page:
  - `any_down` (default): the time **any** monitored device of the site was down, from the incident history. Devices down at the same time count once.
  - `critical`: the time the site was at **Critical**, from its recorded level changes.
- **Maintenance windows are not downtime.** A site window removes its time from the site, and a device window from that device. A window ended early counts until it ended; a cancelled one doesn't count.
- **A tenant's availability** is the average of its sites' availability (each site counts the same), with the number of sites.
- Sites without monitored devices are left out, unless their history has incidents in the month. Below 99.9 % is shown in red.

The figures add up with the history: in `any_down`, a site's downtime is the incidents CSV's down periods for the month, overlaps counted once, minus maintenance.

## API and CSV

- `GET /api/availability?month=2026-10&basis=any_down`, or `?from=2026-10-03&to=2026-10-09` for any days (both included): the report as JSON (`sites`, `tenants`, `period` with `from`, `to` and `label`, `summary` with the cards' figures, and `tenant_descriptions`). A from–to that is a whole calendar month (the 1st to its last day) is that month; otherwise `month` is `null`.
- `GET /api/availability.csv?month=2026-10&view=sites` (or `view=tenants`; `from` and `to` work here too): for a spreadsheet. Downtime is in minutes. The last two columns, `period_from` and `period_to`, are the days covered; `month` is empty unless the period is a whole month.

Both are readable like the board: with `AUTH_REQUIRE_VIEWER=true`, the viewer role.

History older than `ALERT_HISTORY_RETENTION_DAYS` is deleted: a period that starts before that is refused, rather than shown with its deleted outages missing. With `critical`, a site's level before its first recorded change is the one that change came from; a site that has never changed level counts from when it was first seen.
