# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **API tokens** for scripts, LibreNMS and MCP clients (#297): `Authorization: Bearer nmt_…`, each with a name, a role and an optional expiry; they work with `AUTH_MODE=disabled` and `header`, from any address, and changes are recorded as `token:<name>`. Only a SHA-256 is stored; the token is shown once. Managed with `python -m nautobot_maps token create|list|revoke` or `GET/POST /api/tokens`, `POST /api/tokens/<id>/revoke` (admin role); a wrong, expired or revoked token gets 401. New `api_tokens` table (schema version 7). See `docs/authentication.md`
- **Maintenance windows** for a whole site or some of its devices, now or planned ahead (#283). A site in maintenance shows MAINTENANCE "until … · reason" instead of its level (new Maintenance tile, an "In maintenance" section at the bottom of the wall view, blue on the map); a device in maintenance is left out of its site's level. The alert history is frozen for what a window covers, and nothing is notified until it ends. API: `GET/POST /api/maintenance`, `POST /api/maintenance/<id>/end` (operator role); new `maintenance_windows` table (schema version 6); `/api/alerts` `summary` has `maintenance`; managed from the board with the **Maint.** button on a site's row (start now, plan ahead, end or cancel), or `GET /api/maintenance/devices` for the devices that can be put in maintenance
- **Notifications** when a site's alert level changes: a site reaching `NOTIFY_MIN_LEVEL` (default Critical) and dropping back below it, to a webhook (JSON, optionally HMAC-signed), Microsoft Teams (Workflows webhook) and/or email (SMTP). Optional tenant filter; a burst of changes goes out as one summary. Messages are queued in the same transaction as the level change (`notification_outbox`, schema version 5) and sent by the background scheduler at least once (each event has a stable `id` for dropping duplicates), with retries; bursts become summaries of at most 25 sites; `/metrics` shows pending and failed messages; `POST /api/notifications/test` (admin) sends a test. See `docs/notifications.md` (#282)

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
