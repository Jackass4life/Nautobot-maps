# The alert board

`/alerts` shows every site's alert level, its down devices, how long they've been down, and the cases attached to them. The **Since** column says what each time means: a site is **in alarm 3h 40m** (since its first currently-down device went down; that may be one device), a device is **down 25m**. The wall view shows only the devices' times. It opens on **Alarms** (sites at Critical, Medium or Low); the tiles at the top switch to one level or **All sites**.

The board needs PostgreSQL (`NAUTOBOT_MAPS_DATABASE_URL`); without it the board stays empty, says so, and a warning is logged at startup. The map works either way.

## Alert levels

Each site gets one level, from its **monitored devices**: devices with a Nautobot primary IP (`primary_ip`, `primary_ip4` or `primary_ip6`), after exclusions. This keeps access points and other unmonitored devices off the board without removing them from the map.

| Level | When |
|---|---|
| **Critical** | A core device is down, or every monitored device is down |
| **Medium** | More than 25% of the monitored devices are down |
| **Low** | At least one monitored device is down, 25% or fewer |
| **No data** | No monitored devices, or the level could not be computed. Not an alarm |
| **OK** | Monitored devices, none down |

A device is **core** when its role matches a critical keyword, or when it's marked critical by an override (`/api/criticality-overrides`). The keywords are `CRITICAL_ROLE_KEYWORDS` (default `core,spine,distribution,router,gateway`), or per location type from `CRITICALITY_RULES_FILE` (see `criticality_rules.json`).

Down/up status comes from Nautobot's device status, and from LibreNMS when it's configured.

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

## Maintenance windows

Planned work is not an alarm. A **maintenance window** covers a whole site or some of its devices, from a start (now, or planned ahead) to an end, with a reason.

- **A site in maintenance** shows a blue **MAINTENANCE** badge with "until 14:00 · reason" instead of its level. It is not counted as an alarm (the **Maintenance** tile counts it), and on the wall view it is listed at the bottom under **In maintenance**. On the map it is blue.
- **A device in maintenance** is left out when the site's level is worked out; if it is down, its row says "MAINTENANCE until 14:00 · reason".
- While a window is active, the alert history is **frozen** for what it covers: nothing opens, and open alerts are neither updated nor closed.
- **Notifications:** none when a site goes into maintenance. When the window ends and the site is still Critical, the normal message is sent.

Windows are managed through the API (operator role): `POST /api/maintenance` with `site_id`, `reason`, optional `device_ids`, optional `starts_at`, and `ends_at` or `duration_minutes` (at most 14 days); `GET /api/maintenance` lists active and upcoming ones; `POST /api/maintenance/<id>/end` ends one now or cancels an upcoming one. Changes show on the board at once; a planned window starts showing within one board refresh of its start time.

## Tenants

The Tenants column lists the site's own tenant and those linked to it by a Nautobot Relationship (`SITE_TENANT_RELATIONSHIPS`). A tenant with a **description** in Nautobot gets an **(i)**: hover over it, or tab to it, to read the description. The map's site panel shows the same (i). Descriptions are read on every sync, so an edit in Nautobot shows up after the next one.

## Cases, Copy and history

- **+ Case** attaches a case/ticket number to the open alerts of a site's down devices. The panel shows the site's open cases and each device's case; devices without a case start ticked, those with one unticked. A new number is added next to an existing case, never instead of it, and if the number you type is already on the ticked devices the button says "Already on …". The case shows on the site and on each device until the alert closes.
- **Copy** puts the site and its down devices (name, IP, role, status, down since, cases, Nautobot link) on the clipboard as plain text, for an ITSM ticket.
- **History** lists past and open incidents of the site: when each device went down and came back, downtime, events and cases. **Export** at the top downloads them as CSV for the last 7, 30 or 90 days or everything: **Incidents CSV** (one row per time a device went down: device, role, IP, down and up time, minutes, status, level, reason, cases) or **Per-device CSV** (one row per device: times down, total and longest downtime in minutes, last down, down now). Times are UTC; an incident counts when it was down at any point in the period. Role and IP are the device's current ones. Same role as History (`operator` with sign-in on).
- **Activity** (right-hand panel) lists recent changes: devices down and back up, and site level changes.

With sign-in on, adding cases and history need the `operator` role ([authentication](authentication.md)).

## Automatic updates

An open board keeps itself up to date. Next to **Refresh** it shows **"Next update in m:ss"**: the time until the next sync is due (the sooner of `INVENTORY_SYNC_INTERVAL_SECONDS` and `LIBRENMS_SYNC_INTERVAL_SECONDS`). At zero the board reloads in the background and the new data appears when the sync finishes. **Refresh** syncs immediately.

A background scheduler also runs due syncs with **no page open**, so alert history is recorded around the clock, with start times accurate to about one sync interval. Every app process runs one scheduler thread, and a PostgreSQL lock lets only one work at a time, so more workers or containers don't mean more syncs. `BACKGROUND_SYNC_ENABLED=false` turns it off; syncs then only run when pages are loaded.

Syncs are incremental (only what changed in Nautobot since the last one); a daily full sync removes deleted objects. On a fresh database the first board request starts the first sync, and the board fills in by itself.

## Wall view

**Wall view** in the top navigation opens `/alerts?view=wall`, for a NOC screen nobody clicks on: sites with alarms only, each with its address, down devices and cases, with no buttons or filters. Text is the browser's standard size, as on the board; to make it bigger on a TV, use the browser's zoom (Ctrl/⌘ +), which the browser remembers for this site.

- **Who's on it:** next to each site's level, **NO CASE** (no down device has a case), **"1 of 2 no case"** (some do), or a blue **✓ INC-1234** when every down device has one. Sites with a case on every device turn grey and are sorted below a dashed line, so the bright rows at the top are the ones nobody has picked up. The top line counts them ("· 2 without case"). The site name is plain text here (no link).
- It reloads every minute and shows **"Updated hh:mm"**, which turns red ("NOT UPDATED since …") after 10 minutes without a successful update.
- When the board can't be loaded it shows a red banner and keeps the last board on screen, so a frozen screen never looks like "all OK".
- With `AUTH_REQUIRE_VIEWER=true`, the screen's browser needs to sign in through your proxy like any other viewer.
