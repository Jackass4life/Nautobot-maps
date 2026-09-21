# Nautobot Maps

A web application that displays Nautobot locations on an interactive OpenStreetMap, with device/ASN/tenant details on click and a 5 km proximity search.

## Features

- 🗺️ Interactive map showing all Nautobot locations that have GPS coordinates
- 📍 Color-coded markers by status (Active / Planned / Other)
- 🔍 **Filtering** locations by:
  - Status (Active, Planned, etc.)
  - Location Type
  - Parent Location (hierarchical)
  - Tenant
- ⚡ **Performance optimizations** for large environments:
  - Automatic marker clustering for 100+ locations
  - Grid-based clustering that adapts to zoom level
  - Canvas rendering for improved performance
  - Progressive loading indicators
- 🖱️ Click a marker to see a popup with:
  - Location name, type, status, tenant, time zone, and physical address
  - ASN(s) assigned to the location
  - Network equipment (devices) at the location with model, role, and status
- 🚨 Dedicated **Alert Board** page showing per-site alert severity with filters for site, tenant, location type, status, and severity, collapsible per-site device rows, and server-side filtering for primary-IP-backed devices
- 🔍 Search by **address** (geocoded via OpenStreetMap/Nominatim) **or GPS coordinates** (`lat,lon`)
  - Returns all Nautobot locations within **5 km** of the searched point, sorted by distance
  - Draws a 5 km radius circle on the map
- ⚡ Server-side response caching to reduce Nautobot API load

## Requirements

- Python 3.11+
- A running Nautobot instance (v2.x or v3.x) with an API token

## Quick Start

```bash
# 1. Clone and enter the repository
git clone https://github.com/Jackass4life/Nautobot-maps.git
cd Nautobot-maps

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# Edit .env and set NAUTOBOT_URL and NAUTOBOT_TOKEN

# 5. Run the development server
python app.py
# → Open http://localhost:5000
```

## Configuration (`.env`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `NAUTOBOT_URL` | ✅ | — | Base URL of your Nautobot instance, e.g. `https://nautobot.example.com` (validated at startup) |
| `NAUTOBOT_TOKEN` | ✅ | — | Nautobot API token |
| `NAUTOBOT_API_VERSION` | ❌ | *(server default)* | Pin a specific Nautobot REST API version (e.g. `2.0`, `3.0`). Leave empty to use the server's default. |
| `NAUTOBOT_VERIFY_SSL` | ❌ | `true` | SSL certificate verification: `true`, `false` (e.g. for self-signed certs), or a path to a custom CA bundle |
| `FLASK_SECRET_KEY` | ✅ | `change-me-to-a-random-string` | Flask session secret (change for production) |
| `CACHE_TTL` | ❌ | `300` | Seconds to cache Nautobot API responses |
| `CACHE_TYPE` | ❌ | `SimpleCache` | Flask-Caching backend. Use `RedisCache` in production with multiple workers |
| `CACHE_REDIS_URL` | ❌ | — | Redis connection URL (e.g. `redis://redis:6379/0`). Required when `CACHE_TYPE=RedisCache` |
| `NAUTOBOT_MAPS_DATABASE_URL` | ❌ | — | Preferred persistence DB URL (`postgresql://...`) for overrides, alert downtime history, and case tracking |
| `NAUTOBOT_MAPS_DB` | ❌ | — | SQLite fallback path when PostgreSQL URL is not configured |
| `INVENTORY_SYNC_INTERVAL_SECONDS` | ❌ | `CACHE_TTL` | Interval for background Nautobot inventory sync into the persistence database |
| `LIBRENMS_SYNC_INTERVAL_SECONDS` | ❌ | `CACHE_TTL` | Interval for background LibreNMS status refresh into the persistence database |
| `ALERT_BOARD_EXCLUDED_LOCATION_TYPES` | ❌ | `graveyard,warehouse` | Comma/semicolon-separated location types hidden from `/api/alerts` and the alert board by default |
| `ALERT_BOARD_EXCLUDED_LOCATION_STATUSES` | ❌ | — | Optional comma/semicolon-separated location statuses hidden from the alert board |
| `ALERT_BOARD_EXCLUDED_LOCATION_TAGS` | ❌ | — | Optional comma/semicolon-separated Nautobot tag names hidden from the alert board |
| `ALERT_BOARD_EXCLUDED_LOCATION_NAMES` | ❌ | — | Optional fallback comma/semicolon-separated location names hidden from the alert board |
| `AUTH_MODE` | ❌ | `disabled` | Authentication mode for admin API routes: `disabled` or `header` |
| `AUTH_HEADER_USER` | ❌ | `X-Forwarded-User` | Header-mode username header supplied by a trusted reverse proxy |
| `AUTH_HEADER_GROUPS` | ❌ | `X-Forwarded-Groups` | Header-mode group header supplied by a trusted reverse proxy |
| `AUTH_DEFAULT_ROLE` | ❌ | — | Optional fallback role (`viewer`, `operator`, or `admin`) for authenticated users with no matching group |
| `AUTH_VIEWER_GROUPS` | ❌ | — | Comma-separated SSO group names mapped to the `viewer` role |
| `AUTH_OPERATOR_GROUPS` | ❌ | — | Comma-separated SSO group names mapped to the `operator` role |
| `AUTH_ADMIN_GROUPS` | ❌ | — | Comma-separated SSO group names mapped to the `admin` role |
| `LIBRENMS_URL` | ❌ | — | Base URL of your LibreNMS instance used for optional status enrichment |
| `LIBRENMS_API_TOKEN` | ❌ | — | API token for LibreNMS requests |
| `LIBRENMS_VERIFY_SSL` | ❌ | `true` | LibreNMS TLS verification toggle: set `false`/`no`/`0` to skip certificate verification |
| `FLASK_DEBUG` | ❌ | `false` | Set `true` to enable Flask debug mode |
| `FLASK_RUN_PORT` | ❌ | `5000` | Port for the development server (useful if 5000 is taken, e.g. by macOS AirPlay Receiver) |

### LibreNMS integration settings

Set both `LIBRENMS_URL` and `LIBRENMS_API_TOKEN` to enable optional LibreNMS enrichment.
`LIBRENMS_VERIFY_SSL` defaults to `true`; set it to `false`/`no`/`0` only when you
explicitly accept the TLS trust tradeoff (for example, an internal CA not in the trust
store). Prefer using a trusted CA bundle (for example via `REQUESTS_CA_BUNDLE`) when possible.

## Docker

> **Important:** Always use `docker compose up` — **not** `docker compose build && docker compose start`.
> The `start` sub-command only restarts previously created containers and will
> fail with *"service … has no container to start"* on a fresh checkout.
> `docker compose up` handles building, creating, and starting in one step.

```bash
# 1. Configure environment variables
cp .env.example .env   # fill in NAUTOBOT_URL and NAUTOBOT_TOKEN

# 2. Build images and start containers
docker compose up --build -d
# → Open http://localhost:5000

# View logs
docker compose logs -f

# Stop and remove containers
docker compose down
```

## Demo (Mock Nautobot)

No Nautobot instance? Spin up a fully self-contained demo using the mock
server bundled in `demo/`:

```bash
# From the repository root – no .env required
docker compose -f demo/docker-compose.yml up --build
# → Open http://localhost:5000
```

The demo pre-loads **8 European locations** (two in Copenhagen, plus Stockholm,
Oslo, Amsterdam, Frankfurt, Paris, and London) with devices, ASNs, and tenants so you
can explore every feature immediately.  See [`demo/README.md`](demo/README.md)
for a full description of the seed data and suggested demo scenarios.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Map web UI |
| `GET` | `/alerts` | Alert board web UI |
| `GET` | `/api/alerts` | Alert summary from the persisted inventory snapshot (`?refresh=1` enqueues background sync, `?include_non_operational=1` includes excluded locations) |
| `GET` | `/api/locations` | All Nautobot locations with GPS coordinates |
| `GET` | `/api/locations/<id>/detail` | Devices and ASNs for a location |
| `GET` | `/api/search?q=<query>` | Locations within 5 km of an address or `lat,lon` |
| `GET` | `/api/criticality-overrides` | List stored device criticality overrides *(operator when auth enabled)* |
| `POST` | `/api/criticality-overrides` | Create/update a device criticality override *(operator when auth enabled)* |
| `DELETE` | `/api/criticality-overrides/<device_id>` | Delete a device criticality override *(operator when auth enabled)* |
| `GET` | `/api/alert-history` | Historical alert incidents/events/cases (filter by `site_id`, `device_id`, `start_at`, `end_at`) *(operator when auth enabled)* |
| `POST` | `/api/alert-cases` | Attach a case number to an active site/device alert *(operator when auth enabled)* |
| `GET` | `/api/roles` | List Nautobot roles |
| `POST` | `/api/roles` | Create a Nautobot role *(admin when auth enabled)* |
| `DELETE` | `/api/roles/<role_id>` | Delete a Nautobot role *(admin when auth enabled)* |
| `GET` | `/api/location-types` | List Nautobot location types |
| `POST` | `/api/location-types` | Create a Nautobot location type *(admin when auth enabled)* |
| `DELETE` | `/api/location-types/<lt_id>` | Delete a Nautobot location type *(admin when auth enabled)* |

## Optional Authentication / RBAC

By default, Nautobot Maps stays public and behaves exactly as before:

- `AUTH_MODE=disabled`
- map, alerts, and read-only APIs remain public
- administrative API routes continue to work without authentication

To protect only administrative actions, set `AUTH_MODE=header` and place the app
behind a trusted reverse proxy or SSO gateway that injects identity headers.
This works well with OIDC or SAML providers when the proxy handles the login
flow and forwards the authenticated username/groups to Nautobot Maps.

### Roles

- `viewer` — reserved for future read-only admin features
- `operator` — can manage `/api/criticality-overrides`
- `admin` — can also create/delete Nautobot roles and location types

### Example header-based SSO configuration

```dotenv
AUTH_MODE=header
AUTH_HEADER_USER=X-Forwarded-User
AUTH_HEADER_GROUPS=X-Forwarded-Groups
AUTH_OPERATOR_GROUPS=nautobot-operators
AUTH_ADMIN_GROUPS=nautobot-admins
```

Recommended deployment patterns:

1. **Public read-only mode**: leave `AUTH_MODE=disabled`.
2. **Protected admin mode**: enable `AUTH_MODE=header` behind an internal reverse proxy.
3. **Optional SSO mode**: connect your reverse proxy or auth gateway to OIDC/SAML and forward trusted user/group headers to this app.

## Alert lifecycle history and case tracking

When persistence is configured, `/api/alerts` now includes per-site downtime/case context (`current_downtime_seconds`, `historical_downtime_seconds`, `active_cases`, `down_devices`).  
For best durability and concurrency, use PostgreSQL via `NAUTOBOT_MAPS_DATABASE_URL`.

## Alert board filtering

`/api/alerts` and `/alerts` only count devices that have a Nautobot primary IP (`primary_ip`, `primary_ip4`, or `primary_ip6`). This keeps access points and other non-alerted devices off the board without removing them from the cached inventory used elsewhere.

Non-operational locations are hidden server-side by default when their location type matches `ALERT_BOARD_EXCLUDED_LOCATION_TYPES` (default: `graveyard,warehouse`). You can also exclude by location status, tag, or fallback name list with the related `ALERT_BOARD_EXCLUDED_LOCATION_*` settings. The UI keeps those locations hidden by default but can request the full dataset with the `include_non_operational=1` query parameter.

## Inventory-backed reads

When persistence is configured, Nautobot Maps keeps cached Nautobot locations/devices and LibreNMS device status in the database and prefers those tables as the primary read source for `/api/locations`, `/api/locations/<id>/detail`, and `/api/alerts`. A background sync refreshes Nautobot incrementally with `last_updated__gte=<last_successful_sync>` (device pages are requested with `depth=1` so `primary_ip4`/`primary_ip6` include inline address data), automatically falls back to a full reconcile when the cached extraction/schema version changes, advances the Nautobot watermark only when an incremental pull observes newer upstream `last_updated` values, and uses the sync start time as the fallback watermark only for full reconciles. LibreNMS status refreshes on its own interval, while request handlers continue serving the last persisted snapshot. On `/api/alerts`, `refresh=1|true|yes|refresh` only signals a background sync and never performs live upstream Nautobot/LibreNMS fetches inline.

## Running Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

The test suite includes:

- **Unit tests** (`tests/test_app.py`) — mock-based, run offline.
- **Mock-integration tests** (`tests/test_integration.py`) — start a local
  mock Nautobot server and exercise the full HTTP stack.
- **Live integration tests** (`tests/test_nautobot_live.py`) — skipped by
  default; set `NAUTOBOT_LIVE_URL` and `NAUTOBOT_LIVE_TOKEN` to run against a
  real Nautobot instance.  The `development/` directory contains a
  `docker-compose.yml` + `seed_nautobot.py` for a real Nautobot 3.x stack
  (see [`development/README.md`](development/README.md)).

## Nautobot Version Compatibility

The application is tested against **Nautobot 2.x and 3.x**:

| Feature | Nautobot 2.x | Nautobot 3.x |
|---|---|---|
| Location GPS coordinates | ✅ | ✅ |
| Device list (`dcim/devices/`) | ✅ | ✅ |
| ASN via `ipam/asns/` endpoint | ✅ (built-in) | ⚠️ BGP plugin only |
| ASN as integer field on Location | — | ✅ |
| Nested objects include `name`/`label` | ✅ | ⚠️ brief objects (id + url only) |

> **Nautobot 3.x note:** In Nautobot 3.x core the `ipam/asns/` endpoint is
> not available unless the BGP Models plugin is installed.  ASN numbers are
> stored as an integer field directly on each Location object and are fetched
> from there automatically.  Nested sub-objects in list responses may be
> *brief* (containing only `id` and `url`), so the application resolves
> human-readable names via dedicated lookup maps built from the relevant
> endpoints.

## Notes on Nautobot Data

- Only locations with both `latitude` **and** `longitude` fields populated appear on the map.
- The geocoding service used for address search is [Nominatim](https://nominatim.org/) (OpenStreetMap) — no API key required.

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the [Apache License 2.0](LICENSE).
