# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- Alert board Refresh (`/api/alerts?refresh=1`) now runs an incremental sync of changes since the last sync instead of a full inventory reconcile (#135)

### Fixed
- Docker demo could not start: `.dockerignore` excluded `demo/`, so the mock server was missing from the image; it is now mounted into the mock container (#131)
- `NAUTOBOT_MAPS_DB` was defined twice in `docker-compose.yml` (#131)
- CI no longer relies on a fixed `sleep 2` for the mock server to start (#131)
- Alert board Refresh button never triggered a sync (it sent `refresh=<timestamp>`), and a fresh database showed an empty board until the map page was opened: the first `/api/alerts` request now starts the initial sync in the background, responses report `sync_pending`, and the board re-polls until the sync finishes. Adding a case now shows it immediately and no longer triggers a full sync (#121)
- Location detail returned 502 on Nautobot 3.x without the BGP Models plugin: a 404 from `ipam/asns/` now falls back to the location's own `asn` field, while other upstream errors still fail (#126)
- Demo alert board showed every site as OK with 0 devices: mock Nautobot devices now include a `location` reference and a `primary_ip4`, and the demo stack enables SQLite persistence so the alert board has a snapshot to read (#119)
- Alert board Action column (case form, History, Open map) was clipped and unreachable at desktop widths; the table now scrolls horizontally with the Action column pinned, and fits without scrolling at 1440px and wider (#122)

### Added
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
