# Availability

`/availability` (also **Availability** in the top navigation) shows the availability of every monitored site and tenant for a month: "what was the uptime?".

- **Availability** = 1 − downtime ÷ the period. The period is the month (UTC); for the current month it runs up to now ("so far").
- **Downtime** is set with `AVAILABILITY_BASIS`, or with **Downtime** on the page:
  - `any_down` (default): the time **any** monitored device of the site was down, from the incident history. Devices down at the same time count once.
  - `critical`: the time the site was at **Critical**, from its recorded level changes.
- **Maintenance windows are not downtime.** A site window removes its time from the site, and a device window from that device. A window ended early counts until it ended; a cancelled one doesn't count.
- **A tenant's availability** is the average of its sites' availability (each site counts the same), with the number of sites.
- Sites without monitored devices are left out, unless their history has incidents in the month. Below 99.9 % is shown in red.

The figures add up with the history: in `any_down`, a site's downtime is the incidents CSV's down periods for the month, overlaps counted once, minus maintenance.

## API and CSV

- `GET /api/availability?month=2026-10&basis=any_down`: the report as JSON (`sites`, `tenants`, `period`).
- `GET /api/availability.csv?month=2026-10&view=sites` (or `view=tenants`): for a spreadsheet. Downtime is in minutes.

Both are readable like the board: with `AUTH_REQUIRE_VIEWER=true`, the viewer role.

History older than `ALERT_HISTORY_RETENTION_DAYS` is deleted, so months before that can't be reported.
