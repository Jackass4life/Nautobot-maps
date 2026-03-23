# Nautobot Maps

A web application that displays Nautobot locations on an interactive OpenStreetMap, with device/ASN/tenant details on click and a 5 km proximity search.

## Features

- 🗺️ Interactive map showing all Nautobot locations that have GPS coordinates
- 📍 Color-coded markers by status (Active / Planned / Other)
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
- A running Nautobot instance (v2.x recommended) with an API token

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
| `NAUTOBOT_VERIFY_SSL` | ❌ | `true` | SSL certificate verification: `true`, `false` (e.g. for self-signed certs), or a path to a custom CA bundle |
| `FLASK_SECRET_KEY` | ✅ | `change-me-to-a-random-string` | Flask session secret (change for production) |
| `CACHE_TTL` | ❌ | `300` | Seconds to cache Nautobot API responses |
| `FLASK_DEBUG` | ❌ | `false` | Set `true` to enable Flask debug mode |
| `FLASK_RUN_PORT` | ❌ | `5000` | Port for the development server (useful if 5000 is taken, e.g. by macOS AirPlay Receiver) |

## Docker

```bash
# Build and run with Docker Compose
cp .env.example .env   # fill in your values
docker compose up --build
# → Open http://localhost:5000
```

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

## Notes on Nautobot Data

- Only locations with both `latitude` **and** `longitude` fields populated appear on the map.
- ASNs are fetched from the `ipam/asns` endpoint filtered by `location_id`; this requires Nautobot 2.x.
- The geocoding service used for address search is [Nominatim](https://nominatim.org/) (OpenStreetMap) — no API key required.
