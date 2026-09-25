# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Removed
- **Breaking:** SQLite support. PostgreSQL (`NAUTOBOT_MAPS_DATABASE_URL`) is the only persistence database; `NAUTOBOT_MAPS_DB` is ignored and logged as an error at startup. There is no data migration: move to PostgreSQL before upgrading (the bundled `docker-compose.yml` already uses it) (#153)

### Changed
- Tests run against PostgreSQL (one throwaway schema per test, `TEST_DATABASE_URL`); CI's `test` job and the demo stack use a PostgreSQL 16 service, and the dev container includes one. `_init_db` migrations only look at the current schema (#153)
- Alert board builds read devices, criticality overrides and alert history for all sites in a few queries, and share one write connection (replaced after a failure). With 2,000 sites a build opens 5 database connections instead of about 6,000; the board output is unchanged (#149)
- Alert board Refresh (`/api/alerts?refresh=1`) now runs an incremental sync of changes since the last sync instead of a full inventory reconcile (#135)

### Fixed
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
