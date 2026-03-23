import os
import time
import logging
from functools import wraps

import requests
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
from geopy.distance import geodesic
from geopy.geocoders import Nominatim

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-to-a-random-string")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NAUTOBOT_URL = os.getenv("NAUTOBOT_URL", "").rstrip("/")
NAUTOBOT_TOKEN = os.getenv("NAUTOBOT_TOKEN", "")
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))

# Simple in-memory cache
_cache: dict = {}


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


def nautobot_get(endpoint: str, params: dict | None = None) -> dict:
    """Perform a GET request against the Nautobot REST API."""
    if not NAUTOBOT_URL or not NAUTOBOT_TOKEN:
        raise RuntimeError(
            "NAUTOBOT_URL and NAUTOBOT_TOKEN must be set in environment variables."
        )
    cache_key = f"{endpoint}:{params}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    headers = {
        "Authorization": f"Token {NAUTOBOT_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json; version=1.3",
    }
    url = f"{NAUTOBOT_URL}/api/{endpoint.lstrip('/')}"
    response = requests.get(url, headers=headers, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    _cache_set(cache_key, data)
    return data


def fetch_all_pages(endpoint: str, params: dict | None = None) -> list:
    """Fetch all paginated results from a Nautobot API endpoint."""
    params = dict(params or {})
    params.setdefault("limit", 200)
    results = []
    offset = 0
    while True:
        params["offset"] = offset
        data = nautobot_get(endpoint, params)
        results.extend(data.get("results", []))
        if not data.get("next"):
            break
        offset += params["limit"]
    return results


def get_locations() -> list:
    """Fetch locations from Nautobot that have GPS coordinates."""
    raw = fetch_all_pages("dcim/locations/")
    locations = []
    for loc in raw:
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        if lat is None or lon is None:
            continue
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            continue

        tenant = loc.get("tenant") or {}
        location_type = loc.get("location_type") or {}
        parent = loc.get("parent") or {}

        locations.append(
            {
                "id": loc.get("id", ""),
                "name": loc.get("name", "Unknown"),
                "slug": loc.get("slug", ""),
                "status": (loc.get("status") or {}).get("label", ""),
                "location_type": location_type.get("name", ""),
                "parent": parent.get("name", ""),
                "latitude": lat,
                "longitude": lon,
                "description": loc.get("description", ""),
                "physical_address": loc.get("physical_address", ""),
                "tenant": tenant.get("name", ""),
                "tenant_id": tenant.get("id", ""),
                "asn": loc.get("asn"),
                "time_zone": loc.get("time_zone", ""),
                "url": loc.get("url", ""),
            }
        )
    return locations


def get_location_detail(location_id: str) -> dict:
    """Fetch detailed info (devices, prefixes, ASNs) for a single location."""
    detail: dict = {}

    # Devices at this location
    try:
        devices_data = fetch_all_pages("dcim/devices/", {"location_id": location_id})
        detail["devices"] = [
            {
                "id": d.get("id", ""),
                "name": d.get("name", "Unknown"),
                "device_type": (d.get("device_type") or {}).get("model", ""),
                "manufacturer": (
                    (d.get("device_type") or {}).get("manufacturer") or {}
                ).get("name", ""),
                "role": (d.get("role") or {}).get("name", ""),
                "status": (d.get("status") or {}).get("label", ""),
                "platform": (d.get("platform") or {}).get("name", ""),
                "serial": d.get("serial", ""),
                "tenant": (d.get("tenant") or {}).get("name", ""),
            }
            for d in devices_data
        ]
    except Exception as exc:
        logger.warning("Could not fetch devices for location %s: %s", location_id, exc)
        detail["devices"] = []

    # ASN(s) associated with this location via the routing/asns endpoint (Nautobot 2.x)
    try:
        asns_data = fetch_all_pages("ipam/asns/", {"location_id": location_id})
        detail["asns"] = [
            {
                "asn": a.get("asn"),
                "description": a.get("description", ""),
                "tenant": (a.get("tenant") or {}).get("name", ""),
            }
            for a in asns_data
        ]
    except Exception as exc:
        logger.warning("Could not fetch ASNs for location %s: %s", location_id, exc)
        detail["asns"] = []

    return detail


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/locations")
def api_locations():
    """Return all Nautobot locations that have GPS coordinates."""
    try:
        locations = get_locations()
        return jsonify({"locations": locations})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.error("Unexpected error fetching locations: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/locations/<location_id>/detail")
def api_location_detail(location_id: str):
    """Return devices and ASNs for a specific location."""
    try:
        detail = get_location_detail(location_id)
        return jsonify(detail)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.error("Unexpected error fetching location detail: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/search")
def api_search():
    """
    Geocode an address or parse GPS coordinates and return all Nautobot
    locations within 5 km, sorted by distance.

    Query parameters:
      q  – address string  OR  "lat,lon" coordinate pair
    """
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Missing query parameter 'q'"}), 400

    # Try to parse as raw GPS coordinates first
    lat = lon = None
    parts = query.split(",")
    if len(parts) == 2:
        try:
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
        except ValueError:
            lat = lon = None

    # Fall back to geocoding
    if lat is None or lon is None:
        try:
            geolocator = Nominatim(user_agent="nautobot-maps/1.0")
            location = geolocator.geocode(query, timeout=10)
            if location is None:
                return jsonify({"error": f"Address not found: {query}"}), 404
            lat = location.latitude
            lon = location.longitude
        except Exception as exc:
            logger.error("Geocoding error: %s", exc)
            return jsonify({"error": "Geocoding service unavailable"}), 503

    # Find locations within 5 km
    try:
        all_locations = get_locations()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        logger.error("Error fetching locations for search: %s", exc)
        return jsonify({"error": "Internal server error"}), 500

    search_point = (lat, lon)
    nearby = []
    for loc in all_locations:
        loc_point = (loc["latitude"], loc["longitude"])
        dist_km = geodesic(search_point, loc_point).kilometers
        if dist_km <= 5.0:
            nearby.append({**loc, "distance_km": round(dist_km, 3)})

    nearby.sort(key=lambda x: x["distance_km"])

    return jsonify(
        {
            "search_lat": lat,
            "search_lon": lon,
            "radius_km": 5,
            "count": len(nearby),
            "locations": nearby,
        }
    )


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    try:
        port = int(os.getenv("FLASK_RUN_PORT", 5000))
    except (ValueError, TypeError):
        port = 5000
    app.run(host="0.0.0.0", port=port, debug=debug)
