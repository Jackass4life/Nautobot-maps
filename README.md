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
| `NAUTOBOT_URL` | ✅ | — | Base URL of your Nautobot instance, e.g. `https://nautobot.example.com` |
| `NAUTOBOT_TOKEN` | ✅ | — | Nautobot API token |
| `NAUTOBOT_API_VERSION` | ❌ | *(server default)* | Pin a specific Nautobot REST API version (e.g. `2.0`, `3.0`). Leave empty to use the server's default. |
| `NAUTOBOT_VERIFY_SSL` | ❌ | `true` | SSL certificate verification: `true`, `false` (e.g. for self-signed certs), or a path to a custom CA bundle |
| `FLASK_SECRET_KEY` | ✅ | `change-me-to-a-random-string` | Flask session secret (change for production) |
| `CACHE_TTL` | ❌ | `300` | Seconds to cache Nautobot API responses |
| `CACHE_TYPE` | ❌ | `SimpleCache` | Flask-Caching backend. Use `RedisCache` in production with multiple workers |
| `CACHE_REDIS_URL` | ❌ | — | Redis connection URL (e.g. `redis://redis:6379/0`). Required when `CACHE_TYPE=RedisCache` |
| `FLASK_DEBUG` | ❌ | `false` | Set `true` to enable Flask debug mode |
| `FLASK_RUN_PORT` | ❌ | `5000` | Port for the development server (useful if 5000 is taken, e.g. by macOS AirPlay Receiver) |

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
| `GET` | `/api/locations` | All Nautobot locations with GPS coordinates |
| `GET` | `/api/locations/<id>/detail` | Devices and ASNs for a location |
| `GET` | `/api/search?q=<query>` | Locations within 5 km of an address or `lat,lon` |

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
