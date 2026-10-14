# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Availability for any period, picked on a calendar** (#341): **Period** opens a calendar (click the first and last day; months always have their real days) with **This month**, **Last month**, **Last 7 days** and **Last 30 days**; days in the future or older than the history kept can't be chosen. `/api/availability` and the CSV take `from` and `to` (`2026-10-03`, both days included, at most 366 days) as well as `month`; the CSV adds `period_from` and `period_to` as its last columns, and `period` in the JSON has `from`, `to` and `label`; a from–to that is a whole calendar month is reported as that month
- **Map: "In alarm (N)" list and "Alarms only"** (#334): the sidebar lists the sites in alarm, worst first and then newest down, with "2/8 down · since 14:05"; the first ten and "+N more"; a row flies to the site and opens it. **Alarms only** shows just those pins (remembered in the browser; Clear Filters turns it off). `/api/location-alerts` gives board sites a `since` (the newest open alert's start)
- **History growth in `/metrics`** (#298): `nautobot_maps_table_bytes{table}`, `nautobot_maps_table_rows_estimate{table}` and `nautobot_maps_history_oldest_timestamp_seconds`; the operations guide gives measured growth (≈ 1.2 KB per device incident) and how to choose `ALERT_HISTORY_RETENTION_DAYS`
- **Alertmanager and generic webhook sources** (#292): `POST /api/alertmanager` (the standard Alertmanager webhook) and `POST /api/device-status` (`{"device", "status": "down"|"up", ...}`) mark devices down, matched to Nautobot devices by name or IP like LibreNMS; operator token. Settings `ALERTMANAGER_DEVICE_LABELS`, `ALERTMANAGER_ALERTNAMES`, `WEBHOOK_ALERT_TTL_HOURS`. Schema version 19 (`webhook_device_alerts`)
- **Tenant status page** (#291): `/status/<tenant>` shows one tenant's sites as Operational, Degraded, Outage, Planned maintenance or No data, with since when. It shows no devices, IPs, cases or other tenants. Staff see it as they see the board. A customer gets a link with a `status` API token for their tenant (`token create … --role status --tenant "Acme Corp"`), which opens nothing else. Schema version 18 (`api_tokens.tenant`)
- **Configurable severity and an importance tier** (#293): `ALERT_MEDIUM_PERCENT` (default 25) and `ALERT_CRITICAL_PERCENT` (default 100); a site tier from a Nautobot custom field (`SITE_TIER_FIELD`, `SITE_TIERS`), shown by the site name and sorting important sites first within a level; `NOTIFY_MIN_LEVEL_BY_TIER` (e.g. `gold:low`). Schema version 17 (`nautobot_location_cache.tier`), one full resync
- **Wall view sound** (#290): "Sound on" (per screen, remembered) plays a chime and shows a browser notification once when a site becomes Critical or a site in alarm has nobody on it; not on every refresh
- **Case numbers link to their ticket** (#289) with `CASE_URL_TEMPLATE` (e.g. `https://itsm.example.com/ticket/{case}`): on the board, wall, + Case and History panels (new tab); Copy and notifications add the URL; the incidents CSV has a `case_urls` column
- **Acknowledge** an alarm without a case number (#288): one click in the row's ⋯ menu (or, with a note or for some devices, in the + Case panel); shown as "ACK olga · note", counted as handled on the wall view, ended with the alarm, recorded in the history; `POST /api/alert-acks`, `POST /api/alert-acks/remove`. Schema version 16
- **Availability report** (#287): `/availability` (in the navigation), `GET /api/availability` and `GET /api/availability.csv` give the availability per site and tenant for a month: downtime is the time any monitored device was down (`AVAILABILITY_BASIS=any_down`, default) or the time the site was at Critical (`critical`), maintenance windows excluded; a tenant is the average of its sites. See `docs/availability.md`
- **Likely cause** within a site (#285): with a core device down, the site's other down devices are marked "Likely caused by <core>", and the core device is listed first with a CORE label, on the board and the wall, in Copy, in notifications and in the history (panel and a `probable_cause` column in the incidents CSV). Schema version 14 (`alert_instances.probable_cause`)
- **Flapping** (#286): a device that goes up or down `FLAP_CHANGES` times (default 4; 0: off) within 30 minutes is one incident with a FLAPPING label (the count in its hover text), counts as down so its site's level doesn't bounce and nothing is re-notified, and is resolved at the time it came back up once it has been steady for 30 minutes. Blips under the alert delay count too. Schema version 13 (`device_states.flapping_since`)
- **Alert delay** (#286): with `ALERT_DELAY_SECONDS`, a device must be down that long before it alarms; shorter blips are not shown, counted, recorded or notified, and a real outage's incident starts when the device went down. Off by default. New `device_states` table (schema version 12), which also gives devices that only LibreNMS reports down their real start time
- **Data contract** with Nautobot and LibreNMS (#309): `docs/data-contract.md` lists, per upstream endpoint, the fields the app reads, their types, and what each becomes in its tables (generated from `nautobot_maps/contract.py`: `python -m nautobot_maps contract-docs`). Every sync checks what it fetched against it: a missing or changed field is logged once per sync by name, counted in `/metrics` (`nautobot_maps_contract_mismatches{source,endpoint,field}`) and shown by `GET /api/contract`. CI checks it against a real Nautobot. New `contract_checks` table (schema version 10); a sync that fetched nothing (incremental, no changes) keeps the previous result. The demo's mock Nautobot now returns `last_updated`, as Nautobot does
- **LibreNMS alert pushes** for near-real-time status (#284): `POST /api/librenms/alert` (operator role; for LibreNMS's API alert transport with an API token) with a LibreNMS `device_id` or `hostname` refreshes that device from the LibreNMS API and rebuilds the board within seconds, instead of after the next LibreNMS sync. An older LibreNMS answer never overwrites a newer one (push or sync); unknown devices are not an error; a failed rebuild is retried by the next scheduler tick. Schema versions 8 and 9 (`librenms_device_status.pushed_at`, `push_seq`). Setup in `docs/configuration.md`
- **API tokens** for scripts, LibreNMS and MCP clients (#297): `Authorization: Bearer nmt_…`, each with a name, a role and an optional expiry; they work with `AUTH_MODE=disabled` and `header`, from any address, and changes are recorded as `token:<name>`. Only a SHA-256 is stored; the token is shown once. Managed with `python -m nautobot_maps token create|list|revoke` or `GET/POST /api/tokens`, `POST /api/tokens/<id>/revoke` (admin role); a wrong, expired or revoked token gets 401. New `api_tokens` table (schema version 7). See `docs/authentication.md`
- **Maintenance windows** for a whole site or some of its devices, now or planned ahead (#283). A site in maintenance shows MAINTENANCE "until … · reason" instead of its level (new Maintenance tile, an "In maintenance" section at the bottom of the wall view, blue on the map); a device in maintenance is left out of its site's level. The alert history is frozen for what a window covers, and nothing is notified until it ends. API: `GET/POST /api/maintenance`, `POST /api/maintenance/<id>/end` (operator role); new `maintenance_windows` table (schema version 6); `/api/alerts` `summary` has `maintenance`; managed from the board with the **Maint.** button on a site's row (start now, plan ahead, end or cancel), or `GET /api/maintenance/devices` for the devices that can be put in maintenance
- **Notifications** when a site's alert level changes: a site reaching `NOTIFY_MIN_LEVEL` (default Critical) and dropping back below it, to a webhook (JSON, optionally HMAC-signed), Microsoft Teams (Workflows webhook) and/or email (SMTP). Optional tenant filter; a burst of changes goes out as one summary. Messages are queued in the same transaction as the level change (`notification_outbox`, schema version 5) and sent by the background scheduler at least once (each event has a stable `id` for dropping duplicates), with retries; bursts become summaries of at most 25 sites; `/metrics` shows pending and failed messages; `POST /api/notifications/test` (admin) sends a test. See `docs/notifications.md` (#282)

### Changed
- **A cleaner Availability page** (#342): four cards first (availability, downtime and incidents, sites below 99.9 %, the worst tenant), then the tenants and sites worst first with a bar beside each percentage and downtime as on the board ("2h 14m", "3 days"); sortable columns, search and a tenant filter; the worst 25 sites and 10 tenants with **Show more**; the tenant (i). `/api/availability` adds `summary` and `tenant_descriptions`
- **The alert board has a Tenants column again** (#340): the first tenant with its (i), and **+N** for the rest; it was a line under the site name since #311. Not on the wall view
- **Breaking:** alert history is kept for **two years** by default (`ALERT_HISTORY_RETENTION_DAYS` unset or empty = `730`; it used to keep everything). After upgrading, the first daily prune deletes resolved alerts and site level changes older than that, and the availability report refuses months before it. Set `ALERT_HISTORY_RETENTION_DAYS=0` before upgrading to keep everything (#298)
- **The map at scale** (#334): a site in alarm or maintenance is always its own pin, on top (critical above medium above low), never hidden in a cluster. Sites without an alarm are small dots, grouped into soft green or grey circles with Leaflet.markercluster 1.5.3 (vendored, MIT), which draws only what is in view. With 1,200 sites and 25 in alarm the first zoom shows 25 pins and about ten circles instead of 335 markers. The map is drawn once the alert levels are in, so an alarm is never first shown inside a cluster. The fixed 1°/2° grid, the 100-site threshold and the redraw on every zoom are gone
- **Breaking (status page behind an SSO proxy):** the tenant status page's script is now `/static/js/status/main.js`, which loads `/static/js/status/page.js`, `/static/js/lib/format.js` and `/static/js/lib/html.js`. A proxy rule that lets only `/static/js/status.js` past sign-in must list these instead, or customers see the page without its data (docs/status-page.md) (#294)
- **All page scripts are ES modules** (#294, part 2): `alerts.js`, `map.js` (1,360 lines), `docs.js`, `status.js` and `tooltip.js` are now `static/js/alerts/`, `map/`, `docs/`, `status/` and shared helpers in `static/js/lib/`, loaded with `<script type="module">`; the map and the board no longer carry copies of the same helpers. No change on the pages. The tests import the real modules in Node (`tests/js_modules.py`) instead of copying functions out of the files by text
- **The alert board is built once, not per request** (#295): the scheduler's board stays stored until the next sync replaces it, changes rebuild it in the background, and only one build runs at a time across workers (8 concurrent requests on a cold 500-site board: 1 build instead of 8). `/metrics` adds `nautobot_maps_board_builds_total`, `_board_build_seconds_total` and `_board_build_last_duration_seconds` by trigger. Schema version 20 (`board_build_stats`)
- **A simpler alert board** (#311): one summary line ("2 alarms · 1 critical · 1 medium") and four chips (Alarms, All sites, No data, Maintenance) instead of eight tiles; no Tenants or Reason columns (tenants are under the site name, the reason is the level badge's hover text); one visible action per row (**+ Case**), Copy, Maint. and History behind **⋯** (the History row under the down devices is gone); Sort moved into More filters, Reset shown only when a filter is in use, Collapse/Expand all removed, a short status line and an **↻** button; times over two days in days

- **A simpler map** (#311): markers are coloured by alert level only (green OK, yellow Low, orange Medium, red Critical, blue Maintenance, grey No data), the same in pins, stacks and clusters; before, orange and red each had two meanings (Planned / >25% down, search result / core down). The searched point is a ring. The status dropdown moved into Advanced filters (the All / Active / Planned buttons stay). The site panel has no Health summary tiles (the equipment chips show the same counts) and no Pinned button. `/api/location-alerts` also returns `ok` for locations with monitored devices

### Fixed
- **The map and the alert board showed different levels** for a site with unmonitored devices (#316): the map's marker colours and its site panel counted every device, the board only those with a primary IP and not of an excluded status (and the panel ignored maintenance, the alert delay and flapping). Both now take a site's level from its board row ("2 down · 7 monitored" in the panel too); the panel marks devices that don't count and says why. A site whose only down devices are within the alert delay is OK on the board, not "no data"
- A location's **country** was worked out on every sync but not stored, so with PostgreSQL the map's Country row, the alert board's country filter and MCP's search by country found nothing. It is stored now (schema version 11); the first sync after the upgrade is a full one that fills it in (#309)

## [0.1.0] - 2026-10-09

The first release: Nautobot sites on a map, and an alert board that shows which sites are down, since when, and who has a case on them. The story behind each feature is in the referenced issues and pull requests.

### Map
- Every Nautobot location with coordinates on an OpenStreetMap map, coloured by alert level as soon as it loads (#234); clustering for large inventories and co-located sites grouped in one marker.
- Filters by status, location type, parent, tenant and tenant group; search by address or `lat,lon` for sites within 5 km.
- Site panel with devices, ASNs, circuits (#235), and each tenant's Nautobot description behind an (i) (#263).
- Tiles and address search can point at internal services (`MAP_TILE_URL`, `GEOCODER_URL`, `GEOCODER_ENABLED`) (#197).

### Alert board
- One row per site with its level, worked out from the monitored devices (those with a primary IP): **Critical** (a core device down, or all of them), **Medium** (more than 25% down), **Low**, **No data**, **OK** (#124). Core devices come from role keywords (`CRITICAL_ROLE_KEYWORDS`, `CRITICALITY_RULES_FILE`) and per-device overrides.
- Opens on **Alarms**; the summary tiles filter by level; search, tenant and sort (newest down first by default) on one line, status, type and non-operational sites under **More filters** (#242, #228).
- Device status from Nautobot and, optionally, LibreNMS, including the IP LibreNMS polls when Nautobot has none (#137).
- **Since** column: a site is "in alarm 3h 40m", a device "down 25m" (#275, #279).
- One row per Site with buildings and floors rolled up (`ALERT_BOARD_SITE_LOCATION_TYPE`), the location path under the name (#158, #178, #269).
- Every tenant of a site, including those linked by Nautobot Relationships, with "+N more" (#238, #279).
- Hide sites by location type, status, tag or name, and ignore devices by status (`ALERT_BOARD_EXCLUDED_*`) (#151).
- Updates itself: a background scheduler syncs and records history with no page open, and an open board reloads when the next sync is due (#154, #152).
- **Activity** panel: devices down and up, and site level changes (#180).

### Wall view
- `/alerts?view=wall` (**Wall view** in the top bar) for a NOC screen: sites with alarms and their down devices, address and cases, no controls (#243, #252, #248, #254).
- At a glance who is on it: **NO CASE**, **"1 of 2 no case"** or **✓ INC-1234**; sites with a case on every device turn grey and move below a dashed line (#271).
- Shows when it last updated, turns red after 10 minutes without an update, and keeps the last board under a red banner when it can't load (#243).
- Browser-standard text size; size a big screen with the browser's zoom (#267).

### Cases, history and export
- **+ Case** attaches a case number to a site's down devices; it shows existing cases and won't add the same number twice (#133, #273).
- **Copy** puts a site and its down devices on the clipboard for an ITSM ticket (#227).
- Alert history per device with downtime and cases; an alert starts when the device went down in Nautobot, and a level change doesn't restart it (#163, #166).
- **History** panel and CSV export per site for the last 7, 30 or 90 days or everything: one row per incident or per device (#277).
- `ALERT_HISTORY_RETENTION_DAYS` deletes old resolved history (#194).

### Integrations
- **MCP server** at `/mcp` for AI assistants, off unless `MCP_ENABLED=true`; its tools use the same sign-in and roles as the web UI (#250).
- **API explorer** at `/docs` with every endpoint and **Try it** (#230).
- Nautobot 2.x and 3.x; the app only reads from Nautobot, so a read-only token is enough (#188).

### Sign-in and roles
- Optional sign-in through a reverse proxy or SSO gateway (`AUTH_MODE=header`) with viewer, operator and admin roles from groups; identity headers are trusted only from `AUTH_TRUSTED_PROXIES`, optionally with a shared secret (#187, #188).
- Without sign-in the board works, but changing criticality overrides is refused unless `ALLOW_UNAUTHENTICATED_WRITES=true` (#188).

### Operations
- Docker image (`ghcr.io/jackass4life/nautobot-maps`) running as a non-root user, with a health check, Debian security updates installed at build time, and PostgreSQL in `docker-compose.yml` (#131, #246, #153).
- Versioned database migrations, applied once before the app starts; a database newer than the release is refused (#201).
- `/healthz` and Prometheus `/metrics` (sync state, open alerts and sites by level) (#200).
- Logging with stack traces for unexpected errors, an access log, and `LOG_FORMAT=json` (#193).
- Database connect and statement timeouts (#190); retries with backoff for Nautobot and LibreNMS (#192).
- Browser security headers, including a Content-Security-Policy that runs only the app's own scripts (#198).
- Hashed dependency lock files, and CI with tests against PostgreSQL, ruff, ESLint, `pip-audit`, `npm audit`, CodeQL and a Trivy scan of the image (#186).
- Documentation in `docs/`: configuration, the alert board, running in production (released images, backup and restore, upgrades), sign-in, MCP (#256).

### Upgrading from an earlier `main`
Only for installations that ran unreleased code from `main`:
- SQLite is no longer supported: move to PostgreSQL (`NAUTOBOT_MAPS_DATABASE_URL`) first; `NAUTOBOT_MAPS_DB` is ignored (#153).
- The Nautobot pass-through API (`/api/roles`, `/api/location-types`) was removed (#188).
- With `AUTH_MODE=disabled`, changing criticality overrides returns 403 unless `ALLOW_UNAUTHENTICATED_WRITES=true` (#188).
- `FLASK_SECRET_KEY` is ignored (#199).
- Database migrations run automatically at startup; take a backup first.
