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
NAUTOBOT_API_VERSION = os.getenv("NAUTOBOT_API_VERSION", "").strip()
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))

# SSL verification: "true" (default) = verify, "false" = skip verification,
# or a file path to a custom CA bundle.
_ssl_env = os.getenv("NAUTOBOT_VERIFY_SSL", "true").strip()
if _ssl_env.lower() == "false":
    NAUTOBOT_VERIFY_SSL: bool | str = False
elif _ssl_env.lower() == "true":
    NAUTOBOT_VERIFY_SSL = True
else:
    # Treat the value as a path to a CA bundle / certificate file
    NAUTOBOT_VERIFY_SSL = _ssl_env

# Simple in-memory cache
_cache: dict = {}


def _nested_str(obj: dict | None, *keys: str) -> str:
    """Return the first non-empty value found in *obj* for the given keys.

    Nautobot 2.x uses ``name`` / ``label`` for nested objects; Nautobot 3.x
    returns a full model representation that uses ``display``.  Trying all
    three keys keeps the code compatible with both versions and with the
    mock fixtures used in unit/integration tests.
    """
    if not obj:
        return ""
    if isinstance(obj, str):
        return obj
    if not isinstance(obj, dict):
        return str(obj)
    for key in keys:
        val = obj.get(key)
        if val is not None and val != "":
            return str(val)
    return ""


def _build_id_name_map(endpoint: str) -> dict:
    """Fetch all objects from *endpoint* and return a ``{id: display_name}`` map.

    Used as a fallback when nested objects in Nautobot's response don't
    include a human-readable field (e.g. some Nautobot 3.x builds return
    brief nested objects with only ``id`` and ``url``).
    """
    try:
        items = fetch_all_pages(endpoint)
        result = {}
        for item in items:
            uid = item.get("id")
            if not uid:
                continue
            name = _nested_str(item, "name", "display", "label", "slug")
            if name:
                result[uid] = name
        return result
    except Exception as exc:
        logger.debug("Could not build name lookup for %s: %s", endpoint, exc)
        return {}


def _build_device_type_maps() -> tuple:
    """Return ``({device_type_id: manufacturer_name}, {device_type_id: model_name})``.

    In Nautobot 3.x the brief nested ``device_type`` object returned inside
    device list responses does **not** include ``manufacturer`` or ``model``
    fields — only ``id`` and ``url``.  Fetching all device types once lets us
    resolve both fields for any device without extra per-device API calls.

    The manufacturer sub-object inside a device-type listing may itself be a
    brief object (id+url only in Nautobot 3.0.x), so we also build a
    manufacturer UUID→name map and fall back to it when the inline name is
    missing.
    """
    try:
        mfr_map = _build_id_name_map("dcim/manufacturers/")
        items = fetch_all_pages("dcim/device-types/")
        dt_mfr: dict = {}
        dt_model: dict = {}
        for item in items:
            uid = item.get("id")
            if not uid:
                continue
            # model name
            model = item.get("model") or _nested_str(item, "display") or ""
            if model:
                dt_model[uid] = model
            # manufacturer name
            mfr_obj = item.get("manufacturer") or {}
            mfr_id = mfr_obj.get("id", "") if isinstance(mfr_obj, dict) else ""
            mfr_name = (
                _nested_str(mfr_obj, "name", "display")
                or mfr_map.get(mfr_id, "")
            )
            if mfr_name:
                dt_mfr[uid] = mfr_name
        return dt_mfr, dt_model
    except Exception as exc:
        logger.debug("Could not build device-type maps: %s", exc)
        return {}, {}


def _build_tenant_group_map() -> dict:
    """Return ``{tenant_id: tenant_group_name}``.

    Fetches all tenants and resolves each tenant's ``tenant_group`` field so
    that locations can expose the tenant group without extra per-location API
    calls.  A fallback name-map for tenant groups is built from the
    ``tenancy/tenant-groups/`` endpoint for Nautobot builds where the nested
    object is brief (id + url only).
    """
    try:
        tg_name_map = _build_id_name_map("tenancy/tenant-groups/")
        tenants = fetch_all_pages("tenancy/tenants/")
        tenant_group_map: dict = {}
        for tenant in tenants:
            tid = tenant.get("id")
            if not tid:
                continue
            tg_obj = tenant.get("tenant_group") or {}
            tg_id = tg_obj.get("id", "") if isinstance(tg_obj, dict) else ""
            tg_name = (
                _nested_str(tg_obj, "name", "display")
                or tg_name_map.get(tg_id, "")
            )
            if tg_name:
                tenant_group_map[tid] = tg_name
        return tenant_group_map
    except Exception as exc:
        logger.debug("Could not build tenant group map: %s", exc)
        return {}


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

    accept = "application/json"
    if NAUTOBOT_API_VERSION:
        accept += f"; version={NAUTOBOT_API_VERSION}"
    headers = {
        "Authorization": f"Token {NAUTOBOT_TOKEN}",
        "Content-Type": "application/json",
        "Accept": accept,
    }
    url = f"{NAUTOBOT_URL}/api/{endpoint.lstrip('/')}"
    response = requests.get(
        url, headers=headers, params=params, timeout=15, verify=NAUTOBOT_VERIFY_SSL
    )
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

    # Fallback lookup tables: cover Nautobot builds where brief nested objects
    # only contain ``id`` + ``url`` without a human-readable name/display field.
    tenant_map = _build_id_name_map("tenancy/tenants/")
    status_map = _build_id_name_map("extras/statuses/")
    lt_map = _build_id_name_map("dcim/location-types/")
    tenant_group_map = _build_tenant_group_map()

    # Build a location id → name map from the raw data for parent resolution.
    # Parents are locations themselves, and their nested objects may also be
    # brief in Nautobot 3.x.
    loc_name_map: dict = {}
    for loc in raw:
        uid = loc.get("id")
        if uid:
            name = _nested_str(loc, "name", "display")
            if name:
                loc_name_map[uid] = name

    if raw:
        logger.debug(
            "Nautobot location sample – tenant=%r  status=%r",
            raw[0].get("tenant"),
            raw[0].get("status"),
        )

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

        tenant_obj = loc.get("tenant") or {}
        tenant_id = tenant_obj.get("id", "") if isinstance(tenant_obj, dict) else ""
        tenant_name = (
            _nested_str(tenant_obj, "name", "display")
            or tenant_map.get(tenant_id, "")
        )

        status_obj = loc.get("status") or {}
        status_id = status_obj.get("id", "") if isinstance(status_obj, dict) else ""
        status_name = (
            _nested_str(status_obj, "label", "name", "display")
            or status_map.get(status_id, "")
        )

        lt_obj = loc.get("location_type") or {}
        lt_id = lt_obj.get("id", "") if isinstance(lt_obj, dict) else ""
        location_type_name = (
            _nested_str(lt_obj, "name", "display")
            or lt_map.get(lt_id, "")
        )

        parent_obj = loc.get("parent") or {}
        parent_id = parent_obj.get("id", "") if isinstance(parent_obj, dict) else ""
        parent_name = (
            _nested_str(parent_obj, "name", "display")
            or loc_name_map.get(parent_id, "")
        )

        tenant_group_name = tenant_group_map.get(tenant_id, "")

        locations.append(
            {
                "id": loc.get("id", ""),
                "name": loc.get("name", "Unknown"),
                "slug": loc.get("slug", ""),
                "status": status_name,
                "location_type": location_type_name,
                "parent": parent_name,
                "latitude": lat,
                "longitude": lon,
                "description": loc.get("description", ""),
                "physical_address": loc.get("physical_address", ""),
                "tenant": tenant_name,
                "tenant_id": tenant_id,
                "tenant_group": tenant_group_name,
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
    # Nautobot 3.x uses the "location" filter parameter (UUID accepted);
    # "location_id" was removed in 3.x and returns 400.
    try:
        devices_data = fetch_all_pages("dcim/devices/", {"location": location_id})

        # Fallback lookup: covers Nautobot builds where brief nested objects
        # only carry id+url without a human-readable name.
        # In Nautobot 3.x the brief device_type nested object inside device
        # list responses does NOT include manufacturer or model fields, so we
        # pre-fetch all device types to resolve device_type_id → model/manufacturer.
        dt_mfr_map, dt_model_map = _build_device_type_maps()
        mfr_map = _build_id_name_map("dcim/manufacturers/")
        role_map = _build_id_name_map("extras/roles/")
        tenant_map = _build_id_name_map("tenancy/tenants/")
        status_map = _build_id_name_map("extras/statuses/")

        devices = []
        for d in devices_data:
            dt = d.get("device_type") or {}
            dt_id = dt.get("id", "") if isinstance(dt, dict) else ""
            mfr_obj = dt.get("manufacturer") if isinstance(dt, dict) else None
            mfr_id = mfr_obj.get("id", "") if isinstance(mfr_obj, dict) else ""
            mfr_name = (
                _nested_str(mfr_obj, "name", "display")
                or mfr_map.get(mfr_id, "")
                or dt_mfr_map.get(dt_id, "")
            )

            ten_obj = d.get("tenant") or {}
            ten_id = ten_obj.get("id", "") if isinstance(ten_obj, dict) else ""
            ten_name = (
                _nested_str(ten_obj, "name", "display")
                or tenant_map.get(ten_id, "")
            )

            st_obj = d.get("status") or {}
            st_id = st_obj.get("id", "") if isinstance(st_obj, dict) else ""
            st_name = (
                _nested_str(st_obj, "label", "name", "display")
                or status_map.get(st_id, "")
            )

            devices.append(
                {
                    "id": d.get("id") or "",
                    "name": d.get("name") or "Unknown",
                    "device_type": (
                        _nested_str(d.get("device_type"), "model", "display")
                        or dt_model_map.get(dt_id, "")
                    ),
                    "manufacturer": mfr_name,
                    "role": (
                        _nested_str(d.get("role"), "name", "display")
                        or role_map.get(
                            d.get("role", {}).get("id", "") if isinstance(d.get("role"), dict) else "",
                            "",
                        )
                    ),
                    "status": st_name,
                    "platform": _nested_str(d.get("platform"), "name", "display"),
                    "serial": d.get("serial") or "",
                    "tenant": ten_name,
                }
            )
        detail["devices"] = devices
    except Exception as exc:
        logger.warning("Could not fetch devices for location %s: %s", location_id, exc)
        detail["devices"] = []

    # ASN(s) associated with this location via the ipam/asns endpoint
    try:
        asns_data = fetch_all_pages("ipam/asns/", {"location_id": location_id})
        detail["asns"] = [
            {
                "asn": a.get("asn"),
                "description": a.get("description", ""),
                "tenant": _nested_str(a.get("tenant"), "name", "display"),
            }
            for a in asns_data
        ]
    except Exception as exc:
        logger.warning("Could not fetch ASNs for location %s: %s", location_id, exc)
        detail["asns"] = []

    return detail


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


def _wants_json():
    """Return True when the client prefers a JSON response."""
    return (
        request.path.startswith("/api/")
        or request.accept_mimetypes.best_match(["application/json", "text/html"])
        == "application/json"
    )


@app.errorhandler(404)
def page_not_found(exc):
    if _wants_json():
        return jsonify({"error": "Not found"}), 404
    return (
        render_template(
            "error.html",
            error_code=404,
            error_title="Page Not Found",
            error_message="The page you are looking for does not exist. "
            "Check the URL or head back to the map.",
        ),
        404,
    )


@app.errorhandler(405)
def method_not_allowed(exc):
    if _wants_json():
        return jsonify({"error": "Method not allowed"}), 405
    return (
        render_template(
            "error.html",
            error_code=405,
            error_title="Method Not Allowed",
            error_message="The HTTP method used is not allowed for this URL.",
        ),
        405,
    )


@app.errorhandler(500)
def internal_server_error(exc):
    if _wants_json():
        return jsonify({"error": "Internal server error"}), 500
    return (
        render_template(
            "error.html",
            error_code=500,
            error_title="Internal Server Error",
            error_message="Something went wrong on our end. Please try again later.",
        ),
        500,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html", nautobot_url=NAUTOBOT_URL)


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
