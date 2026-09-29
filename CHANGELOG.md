# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Removed
- **Breaking:** the Nautobot pass-through API: `GET/POST /api/roles`, `DELETE /api/roles/<id>`, `GET/POST /api/location-types` and `DELETE /api/location-types/<id>`. The app never writes to Nautobot any more: Nautobot is the source of truth, and sites, devices, roles and location types are changed there. The UI never used these endpoints, and `NAUTOBOT_TOKEN` now only needs read permission (#188)
- **Breaking:** SQLite support. PostgreSQL (`NAUTOBOT_MAPS_DATABASE_URL`) is the only persistence database; `NAUTOBOT_MAPS_DB` is ignored and logged as an error at startup. There is no data migration: move to PostgreSQL before upgrading (the bundled `docker-compose.yml` already uses it) (#153)

### Changed
- **Breaking:** with `AUTH_MODE=disabled` (the default), changing criticality overrides now returns 403. Reads and adding cases are unchanged. Set `AUTH_MODE=header`, or `ALLOW_UNAUTHENTICATED_WRITES=true` to keep the old behaviour. New `AUTH_REQUIRE_VIEWER=true` (header mode) makes every page and API need the `viewer` role (#188)
- Dependencies: Flask 3.1.3, requests 2.34.2, python-dotenv 1.2.3 and redis 6.4.0 (which no longer pulls in PyJWT) fix 20 known vulnerabilities. `requirements.txt` / `requirements-dev.txt` are now hashed lock files compiled from `requirements.in` / `requirements-dev.in`; the image installs with `--require-hashes`. CI runs `pip-audit`, `npm audit`, a lock-file check and a Trivy scan of the image, and Dependabot proposes updates weekly (#186)
- Alert board: the line under a site shows only its Nautobot location path, e.g. `EMEA › DNK` (region › country code), instead of country, path and tenant group; the tenant keeps its own column. The path is now built for every row, with or without `ALERT_BOARD_SITE_LOCATION_TYPE`, and the site search matches it (#178)
- Alert board: the Action column is one line of small buttons (**+ Case**, **History**, **Map**); + Case opens the case form in a side panel, and Enter saves. Only one side panel is open at a time; Escape closes it and focus returns to the button that opened it (#179)
- The web routes and error handlers moved to `nautobot_maps/web.py` (a Flask blueprint) and authentication to `nautobot_maps/auth.py`. `app.py` is now a ~60-line entry point; splitting `app.py` is complete (last step). No behaviour change, URLs unchanged (#165)
- The alert logic moved from `app.py` to `nautobot_maps/alerts.py` and the background scheduler to `nautobot_maps/scheduler.py` (fifth step of splitting `app.py`); no behaviour change. Their log lines now show `nautobot_maps.alerts` / `nautobot_maps.scheduler` instead of `app` (#165)
- The inventory sync moved from `app.py` to `nautobot_maps/inventory.py`, and the time helpers to `nautobot_maps/timeutil.py` (fourth step of splitting `app.py`); no behaviour change. Tests now fail if a module touches the database when imported, or if the startup log loses its database lines (#165)
- The Nautobot and LibreNMS API clients and the response cache moved from `app.py` to `nautobot_maps/nautobot.py`, `nautobot_maps/librenms.py` and `nautobot_maps/caching.py` (third step of splitting `app.py`); no behaviour change (#165)
- PostgreSQL connections, schema and migrations moved from `app.py` to `nautobot_maps/db.py` (second step of splitting `app.py`); no behaviour change. Their two startup log lines now show `nautobot_maps.db` instead of `app` (#165)
- Settings read from the environment moved from `app.py` to `nautobot_maps/settings.py` (first step of splitting `app.py`); no behaviour change. A test guard fails any test that still sets a setting on `app` (#165)
- Alert levels: **Low** (at least one device down, 25% or fewer), **No data** (no monitored devices, or the level could not be computed; replaces Unknown and is not counted in `non_ok`), and every monitored device down is now **Critical**. Sites with no monitored devices or a single down access switch no longer show as OK. The board and map show the new levels; `/api/alerts` `summary` has `low` and `no_data` instead of `unknown` (#124)
- Tests run against PostgreSQL (one throwaway schema per test, `TEST_DATABASE_URL`); CI's `test` job and the demo stack use a PostgreSQL 16 service, and the dev container includes one. `_init_db` migrations only look at the current schema (#153)
- Alert board builds read devices, criticality overrides and alert history for all sites in a few queries, and share one write connection (replaced after a failure). With 2,000 sites a build opens 5 database connections instead of about 6,000; the board output is unchanged (#149)
- Alert board Refresh (`/api/alerts?refresh=1`) now runs an incremental sync of changes since the last sync instead of a full inventory reconcile (#135)

### Fixed
- PostgreSQL connections had no timeouts: a database that dropped packets made every request (and `/healthz`) hang until the operating system gave up. Connecting now gives up after `DB_CONNECT_TIMEOUT_SECONDS` (default 5; `/healthz` 2, inside Docker's probe timeout) and statements are cancelled after `DB_STATEMENT_TIMEOUT_SECONDS` (default 60; startup migrations exempt). Options already in the database URL are kept (#190)
- `AUTH_MODE=header` did not work in Docker: gunicorn listened on `127.0.0.1` inside the container, so nothing could reach it while the health check still passed. Gunicorn now always listens on `0.0.0.0:5000`, and identity headers are only trusted from `AUTH_TRUSTED_PROXIES` (default: localhost) and, if set, with `AUTH_PROXY_SECRET` in `X-Auth-Proxy-Secret`; headers from anywhere else are ignored and logged (#187)
- One transient upstream error (e.g. a 502 while Nautobot restarts) aborted a whole inventory sync. Nautobot and LibreNMS GET requests are now retried up to 3 times with backoff (1, 2, 4 s) on connection errors and 429/502/503/504, honouring `Retry-After` (capped at 30 s); writes are not retried. Connections are reused between pages instead of a new TLS handshake per request (#192)
- `LIBRENMS_VERIFY_SSL` could not take a CA bundle path (it was silently treated as `true`), so a LibreNMS signed by an internal CA only worked with verification off. It now accepts `true`, `false` or a path, like `NAUTOBOT_VERIFY_SSL`; both also accept `yes`/`no`/`1`/`0`, and a path that doesn't exist is logged as an error at startup (#191)
- The inventory sync could be served Nautobot pages from the response cache (up to `CACHE_TTL` old) instead of asking Nautobot, so a sync could miss recent changes; a sync now always reads Nautobot directly, and no longer stores its pages in Redis. Creating a role now also clears the cached role listings (#185)
- The inventory sync could be served Nautobot pages from the response cache (up to `CACHE_TTL` old) instead of asking Nautobot, so a sync could miss recent changes; a sync now always reads Nautobot directly, and no longer stores its pages in Redis. (#185)
- An alert started when the app first saw the device down, not when it went down: new alerts now start at the device's Nautobot `last_updated` when its Nautobot status makes it down and that is earlier; devices only LibreNMS reports down keep the time they were seen. Open alerts are moved earlier once on their next board build, and a start time never moves later (#166)
- App crashed at startup (`NameError: _build_alert_key`) when open alerts had to be migrated to the new alert key (#163): the migration runs while `app.py` is still loading and used a helper defined further down. The data was not changed (the migration's transaction was rolled back) (#169)
- Alert downtime restarted when a site's severity changed (e.g. Medium → Critical): alerts are now identified by site + device, so a severity change updates the open alert (recorded as an `updated` event) and its downtime keeps running. Open alerts are migrated once at startup (#163)
- `NAUTOBOT_API_VERSION` and `GUNICORN_WORKERS` / `GUNICORN_TIMEOUT` / `GUNICORN_BIND` in `.env` had no effect with `docker compose`; an empty `CACHE_TTL` or `GUNICORN_*` value now means the default instead of crashing at startup. A test now fails when a setting is missing from `docker-compose.yml` (#159)
- `INVENTORY_SYNC_INTERVAL_SECONDS` / `LIBRENMS_SYNC_INTERVAL_SECONDS` in `.env` had no effect with `docker compose`: `docker-compose.yml` did not pass them (#152)
- `ALERT_BOARD_EXCLUDED_*` settings in `.env` had no effect with `docker compose`: `docker-compose.yml` did not pass them to the container (#151)
- Alert board without a persistence database showed an unexplained empty table: `/api/alerts` now reports `persistence_configured`, the board explains which setting is missing, and a warning is logged at startup (#136)
- Docker demo could not start: `.dockerignore` excluded `demo/`, so the mock server was missing from the image; it is now mounted into the mock container (#131)
- `NAUTOBOT_MAPS_DB` was defined twice in `docker-compose.yml` (#131)
- CI no longer relies on a fixed `sleep 2` for the mock server to start (#131)
- Alert board Refresh button never triggered a sync (it sent `refresh=<timestamp>`), and a fresh database showed an empty board until the map page was opened: the first `/api/alerts` request now starts the initial sync in the background, responses report `sync_pending`, and the board re-polls until the sync finishes. Adding a case now shows it immediately and no longer triggers a full sync (#121)
- Location detail returned 502 on Nautobot 3.x without the BGP Models plugin: a 404 from `ipam/asns/` now falls back to the location's own `asn` field, while other upstream errors still fail (#126)
- Demo alert board showed every site as OK with 0 devices: mock Nautobot devices now include a `location` reference and a `primary_ip4`, and the demo stack enables SQLite persistence so the alert board has a snapshot to read (#119)
- Alert board Action column (case form, History, Open map) was clipped and unreachable at desktop widths; the table now scrolls horizontally with the Action column pinned, and fits without scrolling at 1440px and wider (#122)

### Added
- Alert board **Activity** panel: devices going down and back up, and site severity changes (e.g. `OK → Critical`), newest first, with Down / Up / Severity filters. It starts hidden below 1,700 px wide so the table keeps its room ("Show activity" in the toolbar); Hide/Show is remembered. `GET /api/alert-feed?limit=&since=&kinds=` serves it. Two new tables, created automatically: `site_alert_levels` and `site_level_changes`; a site's first build after upgrading is not logged as a change (#180)
- ESLint for `static/js` in CI's `lint` job, the pre-commit hook and the dev container, with versions pinned in `package-lock.json`; fixed its findings (unused assignments, a duplicate `L` global) (#164)
- Background scheduler: due syncs run and the alert board is rebuilt with no page open, so alert history is recorded around the clock. One thread per app process, a PostgreSQL lock lets only one work at a time; `BACKGROUND_SYNC_ENABLED=false` turns it off (#154)
- `ALERT_BOARD_SITE_LOCATION_TYPE` (e.g. `Site`): one alert-board row per location of that type, with devices from child locations (buildings, floors) rolled up into it; the row shows its path (`EMEA › DNK`) and down devices show where they are (`Bygning A › Etage 2`). The location cache stores `parent_id` (one automatic full resync after upgrading) (#158)
- Alert board updates itself: a normal `/api/alerts` request starts an inventory sync once one is due, and the board shows "Next update in m:ss" and reloads in the background at zero (`next_update_in_seconds` in the API) (#152)
- `ALERT_BOARD_EXCLUDED_DEVICE_STATUSES` ignores devices by status on the alert board (not counted as monitored or down, not listed), and `null` in the location/device status settings matches an empty status (#151)
- `ruff format` applied repo-wide (layout only) and checked in CI and the pre-commit hook; the formatting commit is listed in `.git-blame-ignore-revs` (#144)
- Dev container (`.devcontainer/`) with Python 3.11, Node, GitHub CLI and the dev tools preinstalled; documented in `CONTRIBUTING.md` (#145)
- `ruff` linting in CI (`lint` job), `pyproject.toml` configuration, pinned dev tools in `requirements-dev.txt`, and an optional pre-commit hook; fixed all existing findings (#143)
- README documents `docker-compose.override.yml` for local Docker settings; the file is git-ignored and excluded from the image (#141)
- The LibreNMS cache stores each device's polled IP (`overwrite_ip`, else `ip`), so the alert board's device-IP fallback also works for devices added to LibreNMS by hostname; existing databases gain the column automatically (#137)
- Alert board: attach one case number to several down devices at once (checkbox list, all selected by default); `POST /api/alert-cases` accepts `device_ids` and is all-or-nothing (#133)
- `GET /healthz` liveness endpoint and Docker `HEALTHCHECK`; the container now runs as a non-root user; new `docker-smoke` CI job builds the image and runs the demo stack (#131)
- Alert board summary tiles show an (i) glyph with the tier definition on hover and keyboard focus, sourced from one `ALERT_STATUS_TIER_DEFINITIONS` constant (#117)
- Alert board device rows show each device's IP address (Nautobot primary IP, falling back to the LibreNMS hostname when LibreNMS polls by IP) (#117)
- Initial public release
- Interactive OpenStreetMap visualization of Nautobot locations
- Color-coded markers by location status (Active, Planned, Other)
- Filtering by status, location type, parent, tenant, and tenant group
- Click-to-view location details with devices, ASNs, and tenant info
- Address and GPS coordinate search with 5 km proximity matching
- Grid-based marker clustering for large deployments
- Co-located site grouping with tabbed popups
- Server-side caching to reduce Nautobot API load
- Docker and Docker Compose deployment support
- Demo mode with mock data
- Support for Nautobot v2.x and v3.x APIs
- Comprehensive test suite (unit, integration, and live tests)
