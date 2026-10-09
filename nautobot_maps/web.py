"""Web routes: pages, the JSON API and error handlers (#165).

A Flask blueprint registered by app.py; URLs are unchanged.
"""

import json
import logging
from datetime import UTC
from urllib.parse import urlsplit

import requests
from flask import Blueprint, Response, current_app, jsonify, render_template, request, stream_with_context
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from werkzeug.exceptions import HTTPException

from nautobot_maps import (
    alerts,
    apidocs,
    auth,
    caching,
    db,
    export,
    inventory,
    maintenance,
    mcp,
    metrics,
    notify,
    settings,
    timeutil,
    tokens,
)

logger = logging.getLogger(__name__)

bp = Blueprint("web", __name__)
bp.before_app_request(auth.check_viewer)


def tile_source() -> str:
    """The CSP source for MAP_TILE_URL's host, with ``{s}`` subdomains as ``*``."""
    parsed = urlsplit(settings.MAP_TILE_URL)
    if not parsed.netloc:
        return ""  # a relative URL: same origin, covered by 'self'
    return f"{parsed.scheme}://{parsed.netloc.replace('{s}', '*')}"


def content_security_policy() -> str:
    """Only this app's own scripts run; images may also come from the tile server (#198).

    Inline styles stay allowed: Leaflet markers and popups render HTML with
    style attributes, and the error page has a <style> block.
    """
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            f"img-src 'self' data: {tile_source()}".strip(),
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


@bp.after_app_request
def security_headers(response):
    """Browser security headers on every response (#198); a header the
    response already has is left alone."""
    response.headers.setdefault("Content-Security-Policy", content_security_policy())
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


# ---------------------------------------------------------------------------
# Persistence (PostgreSQL)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


def wants_json():
    """Return True when the client prefers a JSON response."""
    return (
        request.path.startswith("/api/")
        or request.accept_mimetypes.best_match(["application/json", "text/html"]) == "application/json"
    )


@bp.app_errorhandler(404)
def page_not_found(exc):
    if wants_json():
        return jsonify({"error": "Not found"}), 404
    return (
        render_template(
            "error.html",
            error_code=404,
            error_title="Page Not Found",
            error_message="The page you are looking for does not exist. Check the URL or head back to the map.",
        ),
        404,
    )


@bp.app_errorhandler(405)
def method_not_allowed(exc):
    if wants_json():
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


@bp.app_errorhandler(500)
def internal_server_error(exc):
    if wants_json():
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


@bp.app_errorhandler(HTTPException)
def api_http_error(exc):
    if wants_json():
        status_code = exc.code or 500
        if status_code == 404:
            message = "Not found"
        elif status_code == 405:
            message = "Method not allowed"
        elif status_code == 500:
            message = "Internal server error"
        else:
            message = exc.description or "Request failed"
        return jsonify({"error": message}), status_code
    return exc


@bp.app_errorhandler(Exception)
def api_unhandled_error(exc):
    logger.error("Unhandled application error: %s", exc)
    if wants_json():
        return jsonify({"error": "Internal server error"}), 500
    return internal_server_error(exc)


def nautobot_service_unavailable(context: str, exc: Exception):
    logger.warning("%s: %s", context, exc)
    return jsonify({"error": "Nautobot service unavailable"}), 503


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route("/")
def index():
    """The map: Nautobot locations, address and GPS search, alert banners."""
    return render_template(
        "index.html",
        nautobot_url=settings.NAUTOBOT_URL,
        tile_url=settings.MAP_TILE_URL,
        tile_attribution=settings.MAP_TILE_ATTRIBUTION,
    )


@bp.route("/healthz")
def healthz():
    """Liveness probe for Docker, load balancers and monitoring.

    Makes no Nautobot or LibreNMS calls: an upstream outage must not mark this
    app unhealthy, because restarting it cannot fix the upstream.  When
    persistence is configured the database must answer ``SELECT 1``, since the
    alert board cannot be served without it.

    Returns 200 ``{"status": "ok", "checks": {...}}`` or 503 with
    ``"status": "unavailable"``.  Error details are logged, never returned.
    """
    checks = {"app": "ok"}
    # Informational, never a failure: restarting the app can't fix a sync
    # that fails upstream, but monitoring can alert on its age (#200).
    sync_age = None
    if db.dialect():
        conn = None
        try:
            # Answer within Docker's 5 s probe timeout even when the
            # database does not (#190).
            conn = db.get_conn(connect_timeout=2)
            conn.execute("SELECT 1").fetchone()
            checks["database"] = "ok"
            sync_age = metrics.inventory_sync_age_seconds(conn)
        except Exception as exc:
            logger.warning("Health check: database unavailable: %s", exc)
            checks["database"] = "unavailable"
        finally:
            if conn is not None:
                conn.close()
    healthy = all(value == "ok" for value in checks.values())
    return (
        jsonify(
            {
                "status": "ok" if healthy else "unavailable",
                "checks": checks,
                "inventory_sync_age_seconds": sync_age,
            }
        ),
        200 if healthy else 503,
    )


@bp.route("/metrics")
def prometheus_metrics():
    """Prometheus metrics: sync health, open alerts, sites by level (#200)."""
    if not settings.METRICS_ENABLED:
        return jsonify({"error": "Not found"}), 404
    conn = None
    try:
        if db.dialect():
            conn = db.get_conn()
        body = metrics.collect(conn)
    except Exception as exc:
        logger.warning("Metrics: database unavailable: %s", exc)
        body = metrics.collect(None)
    finally:
        if conn is not None:
            conn.close()
    return body, 200, {"Content-Type": "text/plain; version=0.0.4; charset=utf-8"}


@bp.route("/alerts")
def alert_board():
    """The alert board: every site's severity, down devices, cases and history.

    ``?view=wall`` is the wall-screen view (#243): alarms only, down devices
    listed, no controls.
    """
    return render_template(
        "alerts.html",
        nautobot_url=settings.NAUTOBOT_URL,
        tier_definitions=alerts.ALERT_STATUS_TIER_DEFINITIONS,
        wall_view=request.args.get("view") == "wall",
    )


@bp.route("/docs")
def api_docs():
    """This page: every page and API endpoint, with Try it (#230)."""
    return render_template("docs.html", groups=apidocs.grouped(current_app))


@bp.route("/mcp", methods=["POST"])
def mcp_endpoint():
    """MCP server for AI assistants (#250): the alert board as tools.

    Off unless MCP_ENABLED=true.  Streamable HTTP, stateless, protocol
    2026-07-28 and the older initialize-based versions.  Tools run the
    matching API routes with the caller's sign-in and roles.
    """
    if not settings.MCP_ENABLED:
        return jsonify({"error": "Not found"}), 404
    return mcp.handle()


@bp.route("/api/endpoints")
def api_endpoints():
    """Every page and API endpoint as JSON: methods, parameters, body and role (#230)."""
    return jsonify({"endpoints": apidocs.endpoints(current_app)})


@bp.route("/api/locations")
def api_locations():
    """Return all Nautobot locations that have GPS coordinates."""
    try:
        locations = inventory.get_locations()
        return jsonify({"locations": locations})
    except RuntimeError as exc:
        return nautobot_service_unavailable("Locations endpoint unavailable", exc)
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
        return nautobot_service_unavailable("Location detail endpoint unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.exception("Unexpected error fetching location detail: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@bp.route("/api/location-alerts")
def api_location_alerts():
    """Alert level of every map location with an alert, for the marker colours (#234)."""
    try:
        return jsonify(alerts.get_location_alert_levels())
    except Exception as exc:
        logger.exception("Could not compute location alert levels: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@bp.route("/api/alerts")
def api_alerts():
    """Return alert-board summaries for all Nautobot locations.

    Read from the persisted inventory snapshot; never calls Nautobot or
    LibreNMS inline.  Query parameters: ``refresh=1`` queues an incremental
    sync now, ``include_non_operational=1`` includes the excluded locations.
    A normal request also queues a sync when one is due.

    Response: ``alerts`` (one entry per site: ``alert_level``,
    ``alert_reason``, ``down_devices``, ``current_downtime_seconds``,
    ``historical_downtime_seconds``, ``active_cases``, ``latest_down_at``, ...),
    ``summary`` (a count per level plus ``total``, and ``non_ok`` = critical +
    medium + low), ``sync_pending`` (a sync is running; poll again),
    ``next_update_in_seconds`` (until the next sync is due; 0 = now, null =
    unknown or running) and ``persistence_configured`` (false: no database,
    so the board is always empty).
    """
    force_refresh = request.args.get("refresh", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "refresh",
    }
    include_non_operational = request.args.get(
        "include_non_operational",
        "",
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "include",
    }
    try:
        return jsonify(
            alerts.get_alert_board_data(
                force_refresh=force_refresh,
                include_non_operational=include_non_operational,
            )
        )
    except RuntimeError:
        return (
            jsonify({"error": "Alert board unavailable because Nautobot is not configured"}),
            503,
        )
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error while building alert board: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.exception("Unexpected error building alert board: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


GEOCODE_CACHE_SECONDS = 24 * 3600


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
    if not caching.cache.add("geocode-rate-limit", 1, timeout=1):
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
        return nautobot_service_unavailable("Location search unavailable", exc)
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


# ---------------------------------------------------------------------------
# Criticality override REST endpoints
# ---------------------------------------------------------------------------


@bp.route("/api/criticality-overrides", methods=["GET"])
@auth.require_role("operator")
def api_list_criticality_overrides():
    """Return all per-device criticality overrides stored in the DB.

    Returns 503 when the persistence DB is not configured.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        rows = conn.execute(
            "SELECT nautobot_device_id, is_critical, reason, updated_by, updated_at "
            "FROM device_criticality_override ORDER BY updated_at DESC"
        ).fetchall()
        return jsonify({"overrides": [db.row_to_dict(r) for r in rows]})
    except Exception as exc:
        logger.exception("Could not list criticality overrides: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


@bp.route("/api/criticality-overrides", methods=["POST"])
@auth.require_role("operator")
def api_set_criticality_override():
    """Create or update a per-device criticality override.

    Expected JSON body::

        {
            "nautobot_device_id": "<uuid>",
            "is_critical": true | false,
            "reason": "optional explanation",
            "updated_by": "operator-name"
        }

    Returns 503 when the DB is not configured.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    body = request.get_json(silent=True) or {}
    device_id = (body.get("nautobot_device_id") or "").strip()
    if not device_id:
        conn.close()
        return jsonify({"error": "nautobot_device_id is required"}), 400
    is_critical = bool(body.get("is_critical", True))
    reason = (body.get("reason") or "").strip()
    updated_by = (body.get("updated_by") or "").strip()
    if not updated_by:
        updated_by = auth.get_current_user().get("username", "")
    try:
        with db.transaction(conn):
            p0, p1, p2, p3 = db.placeholders(4).split(",")
            now_sql = db.sql_now()
            conn.execute(
                f"""
                INSERT INTO device_criticality_override
                    (nautobot_device_id, is_critical, reason, updated_by, updated_at)
                VALUES ({p0}, {p1}, {p2}, {p3}, {now_sql})
                ON CONFLICT(nautobot_device_id) DO UPDATE SET
                    is_critical = excluded.is_critical,
                    reason      = excluded.reason,
                    updated_by  = excluded.updated_by,
                    updated_at  = excluded.updated_at
                """,
                (device_id, int(is_critical), reason, updated_by),
            )
        return jsonify({"status": "ok", "nautobot_device_id": device_id, "is_critical": is_critical})
    except Exception as exc:
        logger.exception("Could not set criticality override: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


@bp.route("/api/criticality-overrides/<device_id>", methods=["DELETE"])
@auth.require_role("operator")
def api_delete_criticality_override(device_id: str):
    """Delete a per-device criticality override.

    Returns 404 if no override exists for the given device ID.
    Returns 503 when the DB is not configured.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        with db.transaction(conn):
            marker = db.placeholders(1)
            cur = conn.execute(
                f"DELETE FROM device_criticality_override WHERE nautobot_device_id = {marker}",
                (device_id,),
            )
        if cur.rowcount == 0:
            return jsonify({"error": "Override not found"}), 404
        return jsonify({"status": "deleted", "nautobot_device_id": device_id})
    except Exception as exc:
        logger.exception("Could not delete criticality override: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Alert lifecycle / case tracking endpoints
# ---------------------------------------------------------------------------


# Most entries /api/alert-feed returns at once.
MAX_FEED_ENTRIES = 500


@bp.route("/api/alert-feed")
def api_alert_feed():
    """What changed on the alert board, newest first (#180).

    Devices going down and back up, and site severity changes.  Query
    parameters: ``limit`` (default 100, at most 500), ``since`` (ISO-8601;
    only newer entries) and ``kinds`` (comma-separated ``down``, ``up``,
    ``severity``; default all).
    """
    try:
        limit = int(request.args.get("limit") or 100)
    except ValueError:
        return jsonify({"error": "limit must be a whole number"}), 400
    limit = max(1, min(limit, MAX_FEED_ENTRIES))
    since = (request.args.get("since") or "").strip() or None
    if since:
        parsed_since = timeutil.parse_iso_datetime(since)
        if parsed_since is None:
            return jsonify({"error": "since must be an ISO-8601 timestamp"}), 400
        if parsed_since.tzinfo is None:
            parsed_since = parsed_since.replace(tzinfo=UTC)
        since = parsed_since.astimezone(UTC).isoformat()
    kinds = [kind.strip() for kind in (request.args.get("kinds") or "").split(",") if kind.strip()]
    unknown = sorted(set(kinds) - set(alerts.FEED_KINDS))
    if unknown:
        return jsonify({"error": f"Unknown kinds: {', '.join(unknown)}"}), 400
    conn = db.get_conn()
    if conn is None:
        return jsonify({"events": [], "persistence_configured": False})
    try:
        events = alerts.read_alert_feed(conn, limit, since, kinds or alerts.FEED_KINDS)
        return jsonify({"events": events, "persistence_configured": True})
    except Exception as exc:
        logger.exception("Could not read the alert feed: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


@bp.route("/api/alert-history", methods=["GET"])
@auth.require_role("operator")
def api_alert_history():
    """Return historical alert instances with events and case numbers.

    Newest down first (by when the device went down), at most 500.  Query parameters filter them: ``site_id``,
    ``device_id``, and ``start_at`` / ``end_at``: only incidents created at
    or after / at or before this ISO-8601 time.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    site_id = (request.args.get("site_id") or "").strip()
    device_id = (request.args.get("device_id") or "").strip()
    start_at = (request.args.get("start_at") or "").strip()
    end_at = (request.args.get("end_at") or "").strip()
    try:
        if start_at:
            parsed_start_at = timeutil.parse_iso_datetime(start_at)
            if parsed_start_at is None:
                return jsonify({"error": "start_at must be an ISO-8601 timestamp"}), 400
            if parsed_start_at.tzinfo is None:
                parsed_start_at = parsed_start_at.replace(tzinfo=UTC)
            start_at = parsed_start_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if end_at:
            parsed_end_at = timeutil.parse_iso_datetime(end_at)
            if parsed_end_at is None:
                return jsonify({"error": "end_at must be an ISO-8601 timestamp"}), 400
            if parsed_end_at.tzinfo is None:
                parsed_end_at = parsed_end_at.replace(tzinfo=UTC)
            end_at = parsed_end_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        conditions = []
        params = []
        if site_id:
            conditions.append(f"site_id = {db.placeholders(1)}")
            params.append(site_id)
        if device_id:
            conditions.append(f"device_id = {db.placeholders(1)}")
            params.append(device_id)
        if start_at:
            conditions.append(f"created_at >= {db.placeholders(1)}")
            params.append(start_at)
        if end_at:
            conditions.append(f"created_at <= {db.placeholders(1)}")
            params.append(end_at)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"""
            SELECT id, alert_key, site_id, site_name, device_id, device_name, alert_level,
                   alert_reason, status, down_started_at, last_seen_down_at, resolved_at,
                   total_downtime_seconds, created_at, updated_at
            FROM alert_instances
            {where_clause}
            ORDER BY down_started_at DESC, id DESC
            LIMIT 500
            """,
            tuple(params),
        ).fetchall()
        instances = [db.row_to_dict(row) for row in rows]
        instance_ids = [row["id"] for row in instances if row.get("id") is not None]
        events_by_instance: dict[str, list[dict]] = {str(instance_id): [] for instance_id in instance_ids}
        cases_by_instance: dict[str, list[dict]] = {str(instance_id): [] for instance_id in instance_ids}
        if instance_ids:
            markers = db.placeholders(len(instance_ids))
            ev_rows = conn.execute(
                f"""
                SELECT alert_instance_id, event_type, event_at, alert_level, alert_reason, snapshot_json
                FROM alert_events
                WHERE alert_instance_id IN ({markers})
                ORDER BY alert_instance_id ASC, id ASC
                """,
                tuple(instance_ids),
            ).fetchall()
            case_rows = conn.execute(
                f"""
                SELECT alert_instance_id, case_number, created_by, created_at
                FROM alert_cases
                WHERE alert_instance_id IN ({markers})
                ORDER BY alert_instance_id ASC, id DESC
                """,
                tuple(instance_ids),
            ).fetchall()
            for ev_row in ev_rows:
                event = db.row_to_dict(ev_row)
                instance_id = str(event.pop("alert_instance_id"))
                try:
                    event["snapshot"] = json.loads(event.pop("snapshot_json", "{}") or "{}")
                except Exception:
                    event["snapshot"] = {}
                events_by_instance.setdefault(instance_id, []).append(event)
            for case_row in case_rows:
                case_data = db.row_to_dict(case_row)
                instance_id = str(case_data.pop("alert_instance_id"))
                cases_by_instance.setdefault(instance_id, []).append(case_data)
        for instance in instances:
            instance_id = str(instance.get("id"))
            instance["events"] = events_by_instance.get(instance_id, [])
            instance["cases"] = cases_by_instance.get(instance_id, [])
        return jsonify({"instances": instances})
    except Exception as exc:
        logger.exception("Could not fetch alert history: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


@bp.route("/api/maintenance", methods=["GET"])
def api_maintenance_list():
    """Maintenance windows (#283): active and upcoming, soonest first.

    Query parameters: ``site_id`` (one site) and ``all=1`` (also ended and
    cancelled ones).  Each has a ``state``: active, upcoming, ended or
    cancelled; ``device_id`` is empty for a whole site.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        windows = maintenance.list_windows(
            conn,
            site_id=(request.args.get("site_id") or "").strip(),
            include_past=(request.args.get("all") or "").strip().lower() in {"1", "true", "yes"},
        )
        return jsonify({"windows": windows})
    finally:
        conn.close()


@bp.route("/api/maintenance/devices", methods=["GET"])
def api_maintenance_devices():
    """The devices that can be put in maintenance at a site (#283).

    Query parameters: ``site_id`` (a row of the alert board) and
    ``include_non_operational=1`` (as on the board).  The devices the board
    counts there: with a primary IP, not of an excluded status, and those
    below the site when rows are rolled up to a location type.
    """
    site_id = (request.args.get("site_id") or "").strip()
    if not site_id:
        return jsonify({"error": "site_id is required"}), 400
    include_non_operational = (request.args.get("include_non_operational") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    return jsonify({"devices": alerts.site_monitored_devices(site_id, include_non_operational=include_non_operational)})


def _maintenance_changed() -> None:
    """The board and the map show maintenance: drop their cached copies."""
    caching.invalidate_alert_board()  # also the map's marker colours


@bp.route("/api/maintenance", methods=["POST"])
@auth.require_role("operator")
def api_maintenance_create():
    """Put a site, or some of its devices, in maintenance (#283).

    JSON body: ``site_id``, ``reason``, optional ``device_ids`` (leave it out
    for the whole site), optional ``starts_at`` (ISO-8601 with a time zone;
    default now), and ``ends_at`` or ``duration_minutes``.  At most 14 days.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        conn.close()
        return jsonify({"error": "Expected a JSON object"}), 400
    try:
        windows = maintenance.create(conn, body, auth.get_current_user().get("username") or "")
    except maintenance.WindowError as exc:
        return jsonify({"error": exc.message}), 400
    finally:
        conn.close()
    _maintenance_changed()
    return jsonify({"windows": windows}), 201


@bp.route("/api/maintenance/<int:window_id>/end", methods=["POST"])
@auth.require_role("operator")
def api_maintenance_end(window_id: int):
    """End a maintenance window now, or cancel an upcoming one (#283)."""
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        window = maintenance.end(conn, window_id, auth.get_current_user().get("username") or "")
    finally:
        conn.close()
    if window is None:
        return jsonify({"error": "No such maintenance window"}), 404
    _maintenance_changed()
    return jsonify({"window": window})


@bp.route("/api/tokens", methods=["GET"])
@auth.require_role("admin")
def api_tokens_list():
    """API tokens (#297): name, first characters, role, expiry, last use, state; never the secret."""
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        return jsonify({"tokens": tokens.list_tokens(conn)})
    finally:
        conn.close()


@bp.route("/api/tokens", methods=["POST"])
@auth.require_role("admin")
def api_tokens_create():
    """Create an API token (#297) for ``Authorization: Bearer <token>``.

    JSON body: ``name``, ``role`` (viewer, operator or admin) and optional
    ``expires_in_days``.  The answer's ``token`` is the secret: shown this
    once, store it now.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        conn.close()
        return jsonify({"error": "Expected a JSON object"}), 400
    try:
        token = tokens.create(conn, body, auth.get_current_user().get("username") or "")
    except tokens.TokenError as exc:
        return jsonify({"error": exc.message}), 400
    finally:
        conn.close()
    response = jsonify({"token": token})
    response.headers["Cache-Control"] = "no-store"
    return response, 201


@bp.route("/api/tokens/<int:token_id>/revoke", methods=["POST"])
@auth.require_role("admin")
def api_tokens_revoke(token_id: int):
    """Revoke an API token (#297): refused from the next request on."""
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        token = tokens.revoke(conn, token_id, auth.get_current_user().get("username") or "")
    finally:
        conn.close()
    if token is None:
        return jsonify({"error": "No such token"}), 404
    return jsonify({"token": token})


@bp.route("/api/notifications/test", methods=["POST"])
@auth.require_role("admin")
def api_notifications_test():
    """Send a test message on every configured notification channel now (#282).

    Returns ``{"results": {"webhook": "ok", "email": "error: ..."}}``; errors
    never include a URL or password.  400 when no channel is configured.
    """
    channels = notify.configured_channels()
    if not channels:
        if settings.NOTIFY_EMAIL_TO and not settings.SMTP_HOST:
            error = "NOTIFY_EMAIL_TO is set, but SMTP_HOST is not: email needs a mail server"
        else:
            error = (
                "No notification channel configured: set NOTIFY_WEBHOOK_URL, NOTIFY_TEAMS_WEBHOOK_URL, "
                "or NOTIFY_EMAIL_TO together with SMTP_HOST"
            )
        return jsonify({"error": error}), 400
    results = notify.send_test()
    status = 200 if all(value == "ok" for value in results.values()) else 502
    return jsonify({"results": results}), status


@bp.route("/api/alert-history.csv", methods=["GET"])
@auth.require_role("operator")
def api_alert_history_csv():
    """A site's alert history as a CSV download (#277).

    Query parameters: ``site_id`` (required), ``days`` (``7``, ``30``
    (default), ``90`` or ``all``) and ``view``: ``incidents`` (default, one row
    per time a device went down) or ``devices`` (one row per device: times
    down, total and longest downtime, last down, down now).  Includes every
    incident that was down at any point in the period.  Times are UTC.
    """
    site_id = (request.args.get("site_id") or "").strip()
    days = (request.args.get("days") or "30").strip().lower()
    view = (request.args.get("view") or "incidents").strip().lower()
    if not site_id:
        return jsonify({"error": "site_id is required"}), 400
    if view not in export.VIEWS:
        return jsonify({"error": f"view must be one of: {', '.join(export.VIEWS)}"}), 400
    now = timeutil.parse_iso_datetime(timeutil.iso_utc_now())
    try:
        since = export.period_start(days, now)
    except ValueError:
        return jsonify({"error": f"days must be one of: {', '.join(export.PERIOD_DAYS)}"}), 400
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    headers = {"Cache-Control": "no-store"}
    try:
        name = export.file_name(export.site_name(conn, site_id), view, days, now)
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
        if view == "devices":
            body = export.devices_csv(export.read_device_summary(conn, site_id, since, now))
            conn.close()
            return Response(body, mimetype="text/csv", headers=headers)
    except Exception as exc:
        conn.close()
        logger.exception("Could not export alert history: %s", exc)
        return jsonify({"error": "Internal server error"}), 500

    # Incidents are streamed: a site's whole history need not fit in memory.
    def lines():
        try:
            yield from export.incident_lines(export.iter_site_incidents(conn, site_id, since), now)
        except Exception as exc:
            # The status line has been sent; the download ends short.
            logger.exception("Alert history export failed while streaming: %s", exc)
        finally:
            conn.close()

    return Response(stream_with_context(lines()), mimetype="text/csv", headers=headers)


# Upper bound on devices linked to one case in a single request.
MAX_CASE_DEVICES = 200


@bp.route("/api/alert-cases", methods=["POST"])
@auth.require_role("operator", open_when_disabled=True)
def api_add_alert_case():
    """Attach a case number to the open alert of one or more devices at a site.

    JSON body: ``site_id``, ``case_number`` and either ``device_ids`` (list)
    or the single ``device_id``.  All-or-nothing: if any device has no open
    alert, nothing is written and the response is 404 with
    ``missing_device_ids``, so a case is never applied to only part of the
    selection.
    """
    body = request.get_json(silent=True) or {}
    site_id = (body.get("site_id") or "").strip() if isinstance(body.get("site_id"), str) else ""
    case_number = (body.get("case_number") or "").strip() if isinstance(body.get("case_number"), str) else ""
    raw_ids = body.get("device_ids")
    if raw_ids is None:
        raw_ids = [body.get("device_id")] if body.get("device_id") else []
    if not isinstance(raw_ids, list) or not all(isinstance(value, str) for value in raw_ids):
        return jsonify({"error": "device_ids must be a list of device ID strings"}), 400
    # De-duplicate while keeping the operator's order.
    device_ids = list(dict.fromkeys(value.strip() for value in raw_ids if value.strip()))
    if not site_id or not device_ids or not case_number:
        return jsonify({"error": "site_id, device_ids and case_number are required"}), 400
    if len(device_ids) > MAX_CASE_DEVICES:
        return jsonify({"error": f"At most {MAX_CASE_DEVICES} devices per request"}), 400

    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    created_by = (auth.get_current_user().get("username") or "").strip()
    try:
        with db.transaction(conn):
            instance_ids: dict[str, int] = {}
            for device_id in device_ids:
                p0, p1 = db.placeholders(2).split(",")
                row = conn.execute(
                    f"""
                    SELECT id
                    FROM alert_instances
                    WHERE site_id = {p0} AND device_id = {p1} AND status = 'open'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (site_id, device_id),
                ).fetchone()
                if row is not None:
                    instance_ids[device_id] = db.row_to_dict(row)["id"]
            missing = [device_id for device_id in device_ids if device_id not in instance_ids]
            if missing:
                return (
                    jsonify(
                        {
                            "error": "No active alert found for some devices; nothing was changed",
                            "missing_device_ids": missing,
                        }
                    ),
                    404,
                )
            for device_id in device_ids:
                p0, p1, p2 = db.placeholders(3).split(",")
                conn.execute(
                    f"""
                    INSERT INTO alert_cases (alert_instance_id, case_number, created_by, created_at)
                    VALUES ({p0}, {p1}, {p2}, {db.sql_now()})
                    ON CONFLICT(alert_instance_id, case_number) DO NOTHING
                    """,
                    (instance_ids[device_id], case_number, created_by),
                )
        # The board payload embeds case numbers; drop it so the new case shows.
        caching.invalidate_alert_board()
        result = {
            "status": "ok",
            "site_id": site_id,
            "case_number": case_number,
            "linked": [
                {"device_id": device_id, "alert_instance_id": instance_ids[device_id]} for device_id in device_ids
            ],
        }
        if len(device_ids) == 1:
            # Fields of the original single-device response, kept for API clients.
            result["device_id"] = device_ids[0]
            result["alert_instance_id"] = instance_ids[device_ids[0]]
        return jsonify(result)
    except Exception as exc:
        logger.exception("Could not add alert case: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()
