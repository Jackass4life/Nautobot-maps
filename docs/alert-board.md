# The alert board

`/alerts` shows every site's alert level, its down devices, how long they've been down, and the cases attached to them. The **Since** column says what each time means: a site is **in alarm 3h 40m** (since its first currently-down device went down; that may be one device), a device is **down 25m**. The wall view shows only the devices' times. Times over two days are shown in days.

The board shows what is needed to decide and act (#311):

- **One line at the top** says what is wrong ("2 alarms · 1 critical · 1 medium"; the (i) explains the levels). The chips next to it choose which sites are listed: **Alarms** (Critical, Medium or Low; the board opens here), **All sites**, **No data** and **Maintenance**, each with its count.
- **Each row:** the site, how many devices are down of how many monitored, its tenants under its name (the first, and **+N** for the rest), the path above it or its address; the level (hover it for the reason, e.g. "2/7 devices offline (29%)"); since when; the cases; and **+ Case**. The row's other actions (**Copy**, **Maint.** and **History**) are behind **⋯**.
- **Filters:** search and tenant; **More filters** has the sort, status, location type and non-operational sites. **Reset** appears when a filter is in use.

The board needs PostgreSQL (`NAUTOBOT_MAPS_DATABASE_URL`); without it the board stays empty, says so, and a warning is logged at startup. The map works either way.

## Alert levels

Each site gets one level, from its **monitored devices**: devices with a Nautobot primary IP (`primary_ip`, `primary_ip4` or `primary_ip6`), after exclusions. This keeps access points and other unmonitored devices off the board without removing them from the map.

| Level | When |
|---|---|
| **Critical** | A core device is down, or at least `ALERT_CRITICAL_PERCENT` of the monitored devices are down (default 100: all of them) |
| **Medium** | More than `ALERT_MEDIUM_PERCENT` of the monitored devices are down (default 25%) |
| **Low** | At least one monitored device is down, `ALERT_MEDIUM_PERCENT` or fewer |
| **No data** | No monitored devices, or the level could not be computed. Not an alarm |
| **OK** | Monitored devices, none down |

A device is **core** when its role matches a critical keyword, or when it's marked critical by an override (`/api/criticality-overrides`). The keywords are `CRITICAL_ROLE_KEYWORDS` (default `core,spine,distribution,router,gateway`), or per location type from `CRITICALITY_RULES_FILE` (see `criticality_rules.json`).

Down/up status comes from Nautobot's device status, and from LibreNMS when it's configured.

**The map shows the same level** (#316): a site's marker colour and its panel ("MEDIUM · 2 down · 7 monitored") come from its board row, so maintenance, the alert delay, flapping and roll-up apply there too. A mapped location that isn't a board row (below a rolled-up site, or hidden from the board) is scored from its own devices by the same rule. The panel still lists every device; one that doesn't count says why ("Not monitored: no primary IP", or an excluded status).

**At scale the alarms stay in front** (#334): a site in alarm or maintenance is always its own pin, never inside a cluster, with critical on top. Sites without an alarm (OK, No data) are small dots, grouped into soft numbered circles when zoomed out (Leaflet.markercluster, vendored; only what's in view is drawn). With 1,200 sites and 25 in alarm, the first zoom shows the 25 pins and about ten circles. A link to a site inside a circle (`/?location_id=…`) zooms in to it. The sidebar's **In alarm (N)** list names them, worst first and then newest down ("2/8 down · since 14:05"), the first ten and "+N more"; a row opens the site. It follows the sidebar's filters. **Alarms only** hides every other site, and is remembered in the browser; Clear Filters turns it off, and a link to a site shows that site.

## Hiding sites and devices

Non-operational locations are hidden by default: those whose type is in `ALERT_BOARD_EXCLUDED_LOCATION_TYPES` (default `graveyard,warehouse`), or whose status, tag or name is in the matching `ALERT_BOARD_EXCLUDED_LOCATION_*` setting. **More filters → Show non-operational sites** shows them anyway.

Devices can be ignored by status with `ALERT_BOARD_EXCLUDED_DEVICE_STATUSES`: they are not counted and not listed, so for example a Decommissioning device no longer makes its site Critical. In both status settings, `null` matches a missing or empty status:

```bash
ALERT_BOARD_EXCLUDED_LOCATION_STATUSES=null,decommissioning,planned
ALERT_BOARD_EXCLUDED_DEVICE_STATUSES=null,decommissioning,planned
```

## One row per Site

With a location hierarchy such as Region › Country › Site › Building › Floor, set the level the board should show:

```bash
ALERT_BOARD_SITE_LOCATION_TYPE=Site
```

Only locations of that type (case-insensitive) get a row. Levels above it are shown as a path, e.g. `EMEA › DNK`. Devices in child locations count towards their Site (device count, level, downtime), and a down device's row says where it is, e.g. `Bygning A › Etage 2`. Devices below an excluded location (e.g. a Decommissioning building) are left out. A location with devices but no Site above it keeps its own row and is logged once. Changing the setting moves open alerts from building/floor rows to their Site, which restarts their downtime once.

## Importance tier

Not every site matters the same (#293). With `SITE_TIER_FIELD` set to a Nautobot custom field on the location (e.g. `tier`, with values `gold`, `silver`, `bronze`), each site shows its tier by its name (the most important one accented). Among sites at the same level, the more important come first, on the board and the wall. `SITE_TIERS` lists the values, most important first (default `gold,silver,bronze`); other values count as no tier. `NOTIFY_MIN_LEVEL_BY_TIER` can make important sites notify sooner ([notifications](notifications.md)).

## Likely cause

When a **core** device at a site is down, the site's other down devices are probably down because of it (#285). The board and the wall list the core device first, marked **CORE**, and each other down device says "Likely caused by core-rt01". Copy, notifications ("Likely cause: core-rt01 down; 2 other devices probably behind it") and the history (panel and `probable_cause` in the incidents CSV) say the same. It is a guess from the roles, not from cabling: a device can be down for its own reason while its core is down too.

## Alert delay

With `ALERT_DELAY_SECONDS` (default `0`: off), a device must be down that long before it alarms. Until then it is not shown, not counted in the site's level, not recorded in the history and not notified; if it comes back sooner, it never happened. When it does alarm, its incident starts when it went down, so the downtime is right.

The app sees a device's state when the board is built (after every sync, or at once with [LibreNMS alert pushes](configuration.md#near-real-time-status-librenms-alert-pushes)), so the delay is counted in those steps. LibreNMS has its own alert delay: set one or the other, not both.

## Flapping

A device that goes up or down `FLAP_CHANGES` times (default `4`; `0`: off) within 30 minutes is **flapping**: it is unstable rather than dead, which usually means a link, power or a loop rather than the device itself. While it flaps:

- it is listed with a **FLAPPING** label (hover it for how many times), also when it is up for a moment;
- it stays **one incident** in the history and the CSV, and keeps its case;
- it counts as down, so the site's level doesn't bounce and nothing is notified again and again.

When it has been steady for 30 minutes, the incident closes at the time it came back up. Short blips under the [alert delay](#alert-delay) count too: a device that keeps blipping shows as flapping instead of staying hidden.

## Maintenance windows

Planned work is not an alarm. A **maintenance window** covers a whole site or some of its devices, from a start (now, or planned ahead) to an end, with a reason.

- **A site in maintenance** shows a blue **MAINTENANCE** badge with "until 14:00 · reason" instead of its level. It is not counted as an alarm (the **Maintenance** chip counts it), and on the wall view it is listed at the bottom under **In maintenance**. On the map it is blue.
- **A device in maintenance** is left out when the site's level is worked out; if it is down, its row says "MAINTENANCE until 14:00 · reason".
- While a window is active, the alert history is **frozen** for what it covers: nothing opens, and open alerts are neither updated nor closed.
- **Notifications:** none when a site goes into maintenance. When the window ends and the site is still Critical, the normal message is sent.

**The Maintenance page** (`/maintenance`, **Maintenance** in the top navigation, #343) is the place to plan work ahead, for many sites at once:

- **Active now**, **Planned** and **Ended in the last 7 days** (folded). Windows planned together are one line: "30 sites · until 16:00 · Core upgrade · by olga", with the first site names, the tenants and their (i), **Show 30** to list every site, and **End all 30** or **Cancel all 30** (a second click confirms). Search covers sites, devices, reasons, people and tenants; the tenant list picks one. Each group shows 25 and **Show 25 more**.
- **Plan maintenance**: find sites by name, path or tenant (Enter or a click adds one; **Add all 40 sites of Acme** adds a tenant's sites); the whole sites, or ticked devices when one site is chosen; **Now, for** 1–8 hours or **Planned**: click the first and last day on the calendar (the same day twice for one day) and choose the times in 15-minute steps, in your browser's time zone; and a reason. Up to 200 sites in one go, saved together.

**History** (#353), the second tab on the Maintenance page: every window that overlapped a period, whatever became of it (ended, ended early, cancelled, still running or planned), newest first, as batches. Choose the period with **Period** (the same calendar and quick choices as Availability; the last 30 days at first, a year at most), search, and pick a tenant. **Download CSV** gives one row per window for that period, site and tenant: the site, its path and tenants, the device, the state, the planned start and end, when it really ended, the minutes it ran, the reason, who planned it and when, who ended it, and the batch (times in UTC). **Maint. history** in a site's **⋯** menu on the board, and **Maintenance history** in the map's site panel, open that site's history ("Site: Aarhus ×"; × shows every site). The address keeps the period and site, so a history can be bookmarked or sent.

**On the board:** **Maint.** in a site's **⋯** menu (shown when it has monitored devices) opens its maintenance panel: the site's active and planned windows, each with **End now** (active) or **Cancel** (planned), and a form for a new one: the whole site or ticked devices, now for 1–8 hours or planned on the same calendar and times, and a reason. Anyone can open either; starting, ending and cancelling need the operator role.

**Through the API** (operator role): `POST /api/maintenance` with `site_id` (or `site_ids`, up to 200 sites in one transaction), `reason`, optional `device_ids` (one site), optional `starts_at`, and `ends_at` or `duration_minutes` (at most 14 days); `GET /api/maintenance` lists active and upcoming ones (`?all=true` also past ones, `?recent_days=7` also those ended in the last 7 days), each with `site_name`, `site_path`, `tenants`, `device_name` and `batch_id` (the same for the windows planned in one request); `GET /api/maintenance/sites` the sites maintenance can be planned for; `GET /api/maintenance/devices?site_id=` the devices that can be put in maintenance; `POST /api/maintenance/<id>/end` ends one now or cancels an upcoming one, and `POST /api/maintenance/end` with `{"ids": [...]}` several at once. `GET /api/maintenance/history?from=2026-09-01&to=2026-09-30` (optional `site_id`, `tenant`; default the last 30 days) lists every window that overlapped the period, and `/api/maintenance/history.csv` exports it (viewer role, as the board). Changes show on the board at once; a planned window starts showing within one board refresh of its start time.

## Tenants

The line under each site's name lists the site's own tenant and those linked to it by a Nautobot Relationship (`SITE_TENANT_RELATIONSHIPS`). A tenant with a **description** in Nautobot gets an **(i)**: hover over it, or tab to it, to read the description. The map's site panel and the wall view show the same (i) (#351). Descriptions are read on every sync, so an edit in Nautobot shows up after the next one.

## Cases, Copy and history

- **+ Case** attaches a case/ticket number to the open alerts of a site's down devices. The panel shows the site's open cases and each device's case; devices without a case start ticked, those with one unticked. A new number is added next to an existing case, never instead of it, and if the number you type is already on the ticked devices the button says "Already on …". The case shows on the site and on each device until the alert closes.
- **Links to tickets:** with `CASE_URL_TEMPLATE` (e.g. `https://itsm.example.com/ticket/{case}`), every case number opens its ticket in a new tab: on the board, the wall, the + Case and History panels; Copy and notifications add the URL, and the incidents CSV has a `case_urls` column.
- **Acknowledge** (in the row's **⋯** menu) says someone is on it without a case number: one click acknowledges every down device of the site. With a note, or for some devices only: **+ Case**, then "Or acknowledge without a case". Each device then shows "ACK olga · note", and on the wall view it counts as handled, like a case (**✓ ACK**). It ends with the alarm (a new outage is not acknowledged), **Remove ack** takes it back, and the history records both. API: `POST /api/alert-acks` (`site_id`, optional `device_ids`, `note`) and `POST /api/alert-acks/remove`; same role as cases.
- **Copy** (in the row's **⋯** menu) puts the site and its down devices (name, IP, role, status, down since, cases, Nautobot link) on the clipboard as plain text, for an ITSM ticket.
- **History** lists past and open incidents of the site: when each device went down and came back, downtime, events and cases. **Export** at the top downloads them as CSV for the last 7, 30 or 90 days or everything: **Incidents CSV** (one row per time a device went down: device, role, IP, down and up time, minutes, status, level, reason, cases) or **Per-device CSV** (one row per device: times down, total and longest downtime in minutes, last down, down now). Times are UTC; an incident counts when it was down at any point in the period. Role and IP are the device's current ones. Same role as History (`operator` with sign-in on).
- **Activity** (right-hand panel) lists recent changes: devices down and back up, and site level changes.

With sign-in on, adding cases and history need the `operator` role ([authentication](authentication.md)).

## Automatic updates

An open board keeps itself up to date. Next to the **↻** (update now) button it shows **"Next update in m:ss"**: the time until the next sync is due (the sooner of `INVENTORY_SYNC_INTERVAL_SECONDS` and `LIBRENMS_SYNC_INTERVAL_SECONDS`). At zero the board reloads in the background and the new data appears when the sync finishes. **↻** syncs immediately. When the data may be out of date, the status line says so in orange.

A background scheduler also runs due syncs with **no page open**, so alert history is recorded around the clock, with start times accurate to about one sync interval. Every app process runs one scheduler thread, and a PostgreSQL lock lets only one work at a time, so more workers or containers don't mean more syncs. `BACKGROUND_SYNC_ENABLED=false` turns it off; syncs then only run when pages are loaded.

Syncs are incremental (only what changed in Nautobot since the last one); a daily full sync removes deleted objects. On a fresh database the first board request starts the first sync, and the board fills in by itself.

## Wall view

**Wall view** in the top navigation opens `/alerts?view=wall`, for a NOC screen nobody clicks on: sites with alarms only, each with its address, down devices and cases, with no buttons or filters. Text is the browser's standard size, as on the board; to make it bigger on a TV, use the browser's zoom (Ctrl/⌘ +), which the browser remembers for this site.

- **Who's on it:** next to each site's level, **NO CASE** (no down device has a case or an acknowledgement), **"1 of 2 no case or ack"** (some do), or a blue **✓ INC-1234** (or **✓ ACK**) when every down device has one. Sites where every device has a case or an acknowledgement turn grey and are sorted below a dashed line, so the bright rows at the top are the ones nobody has picked up. The top line counts them ("· 2 without case or ack"). The site name is plain text here (no link).
- It reloads every minute and shows **"Updated hh:mm"**, which turns red ("NOT UPDATED since …") after 10 minutes without a successful update.
- When the board can't be loaded it shows a red banner and keeps the last board on screen, so a frozen screen never looks like "all OK".
- With `AUTH_REQUIRE_VIEWER=true`, the screen's browser needs to sign in through your proxy like any other viewer.

**A wall for some sites only** (#364): add `tenant=` and/or `q=` to the link, and the screen shows only those sites, e.g. a team that looks after two customers in Denmark:

```
/alerts?view=wall&tenant=Acme%20Corp&tenant=Nordic%20Net&q=DNK
```

- `tenant=` (repeat it for more than one) keeps the sites with any of those tenants: the site's own and those linked to it, as on the board's tenant filter; capitals don't matter.
- `q=` keeps the sites whose name, site path (`EMEA › DNK`), address or country contains it, as the board's filter box does; with several (`q=DNK&q=Aarhus`), every one must match.
- The top line starts with what it shows ("Acme Corp, Nordic Net · “DNK” · Updated 14:05 · …"), so a filtered screen isn't read as the whole network; its counts and its sound cover only those sites. Nothing in alarm there: "No active alarms for Acme Corp".
- The same link without `view=wall` opens the board for those sites, with "Only … · **Show all sites**" under the summary; the tiles count only those sites, and the board's own filters narrow further. **Operator view** on a filtered wall keeps the filter.
- **The map** takes the same link (#367): `/?tenant=Acme%20Corp&q=DNK` shows only those sites, and the alarm list, the count, the sidebar's filters, the first view and address search follow; "Only … · **Show all sites**" sits at the top of the sidebar. A link to a site (`location_id=`) outside them says so rather than open nothing. The **Map**, **Alert Board** and **Wall view** links in the top bar keep the filter, so a team moves between the three without losing it.
- **Copy wall link** on the board (#366), shown once the tenant menu, the filter box or the link narrows it: copies the wall's address for those sites, ready for the screen's browser. The tenant menu, when set, replaces the link's tenants; the filter box is added to the link's `q=`. The wall shows alarms whatever their Status or Type, so those two filters aren't in the link, and the button says so ("Copied (without Status)").
- A link that matches no site (a typo, a renamed tenant) says so in amber ("No site matches Acme Crop. Check the link's tenant= and q="), never the green all-clear.
- **Activity** follows the link too (#369): "Activity · Acme Corp" lists only those sites' changes, and its 100 entries are theirs, not the whole network's newest. The server filters it on the stored locations as they are now, so a site that changed tenant brings its past entries with it; `GET /api/alert-feed` takes the same `tenant=` and `q=`. The board's own filter box doesn't narrow it.
- The board's, wall's and map's own filtering happens in the browser on their usual data, so a filtered screen costs the server nothing extra.

**Sound** (top right of the wall view, #290): **Sound on** plays a short chime and shows a browser notification ("London HQ is Critical") once when a site becomes Critical, and once when a site in alarm has nobody on it (no case, no acknowledgement); not on every refresh, and not for what was already wrong when the page opened. It is remembered on that screen; after the page reloads, browsers play sound only after a first click anywhere on it. Notifications need the browser's permission, asked when you turn sound on.
