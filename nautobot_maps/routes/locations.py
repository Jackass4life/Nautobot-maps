"""The map's API (#424): locations, a site's detail, alert levels, the
coordinate check and the address / GPS search."""

import logging
from urllib.parse import urlsplit

import requests
from flask import Blueprint, Response, jsonify, request
from geopy.distance import geodesic
from geopy.geocoders import Nominatim

from nautobot_maps import (
    alerts,
    caching,
    coordinates,
    inventory,
    scope,
    settings,
)
from nautobot_maps.routes import common

logger = logging.getLogger(__name__)

bp = Blueprint("locations", __name__)


@bp.route("/api/locations")
def api_locations():
    """Return all Nautobot locations that have GPS coordinates."""
    try:
        locations = inventory.get_locations()
        return jsonify({"locations": locations})
    except RuntimeError as exc:
        return common.nautobot_service_unavailable("Locations endpoint unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.exception("Unexpected error fetching locations: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@bp.route("/api/locations/<location_id>/detail")
def api_location_detail(location_id: str):
    """Return devices and ASNs for a specific location.

    Optional query parameter:
      location_type – the location type name (e.g. "Data Center", "Office").
        When provided, the criticality keyword set is resolved from the
        location-type-scoped rules configured via ``CRITICALITY_RULES_FILE``.
    """
    location_type = request.args.get("location_type", "").strip() or None
    try:
        detail = alerts.get_location_detail(location_id, location_type=location_type)
        return jsonify(detail)
    except RuntimeError as exc:
        return common.nautobot_service_unavailable("Location detail endpoint unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.exception("Unexpected error fetching location detail: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@bp.route("/api/location-alerts")
def api_location_alerts():
    """Alert level (critical, medium, low, ok, maintenance) of every map location with
    monitored devices, for the marker colours (#234, #311); a location missing has no data."""
    try:
        return jsonify(alerts.get_location_alert_levels())
    except Exception as exc:
        logger.exception("Could not compute location alert levels: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@bp.route("/api/locations/coordinate-check")
@bp.route("/api/locations/coordinate-check.csv")
def api_coordinate_check():
    """The alert board's sites with missing or wrong coordinates (#368), worst
    first: impossible, at 0,0, latitude and longitude swapped, none, then far
    from the rest of their region (a hint, #416).  JSON with counts, or CSV
    (one row per site)."""
    try:
        # From the stored board (no Nautobot call); a page's ?tenant= / ?q= narrow it.
        rows = alerts.board("request").get("alerts") or []
        problems = coordinates.check(rows, scope.read_scope(request.args))
    except Exception as exc:
        logger.exception("Coordinate check failed: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    if request.path.endswith(".csv"):
        return Response(
            "".join(coordinates.csv_lines(problems)),
            mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="coordinate-check.csv"'},
        )
    return jsonify({"counts": coordinates.counts(problems), "sites": problems})


GEOCODE_CACHE_SECONDS = 24 * 3600


# At most one request to the geocoder per this many seconds: the public
# Nominatim allows no more.  A constant so the tests can widen it.
GEOCODE_MIN_INTERVAL_SECONDS = 1


class GeocoderBusy(Exception):
    """Another address search asked the geocoder less than a second ago."""


class GeocoderUnavailable(Exception):
    """The geocoder could not be reached or returned an error."""


def geocode(query: str):
    """Geocode *query* with GEOCODER_URL (#197).

    Returns ``[lat, lon]``, or ``None`` when not found; raises GeocoderBusy or
    GeocoderUnavailable.
    Results are cached for a day, and the service is asked at most once per
    second across all workers: the public Nominatim allows no more.
    """
    cache_key = f"geocode:{query.strip().lower()}"
    cached = caching.get(cache_key)
    if cached is not None:
        return cached.get("point")
    # cache.add is atomic (SET NX in Redis): only one caller per second wins.
    if not caching.cache.add("geocode-rate-limit", 1, timeout=GEOCODE_MIN_INTERVAL_SECONDS):
        raise GeocoderBusy()
    parsed = urlsplit(settings.GEOCODER_URL)
    try:
        geolocator = Nominatim(
            user_agent=settings.GEOCODER_USER_AGENT,
            domain=(parsed.netloc + parsed.path) or settings.GEOCODER_URL,
            scheme=parsed.scheme or "https",
        )
        location = geolocator.geocode(query, timeout=10)
    except Exception as exc:
        logger.exception("Geocoding error: %s", exc)
        raise GeocoderUnavailable() from exc
    point = [location.latitude, location.longitude] if location is not None else None
    caching.set(cache_key, {"point": point}, timeout=GEOCODE_CACHE_SECONDS)
    return point


@bp.route("/api/search")
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
        if not settings.GEOCODER_ENABLED:
            return jsonify({"error": "Address search is turned off; enter coordinates as lat,lon"}), 400
        try:
            point = geocode(query)
        except GeocoderBusy:
            return jsonify({"error": "Address search is busy; try again in a second"}), 429
        except GeocoderUnavailable:
            return jsonify({"error": "Geocoding service unavailable"}), 503
        if point is None:
            return jsonify({"error": f"Address not found: {query}"}), 404
        lat, lon = point

    # Find locations within 5 km
    try:
        all_locations = inventory.get_locations()
    except RuntimeError as exc:
        return common.nautobot_service_unavailable("Location search unavailable", exc)
    except Exception as exc:
        logger.exception("Error fetching locations for search: %s", exc)
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
