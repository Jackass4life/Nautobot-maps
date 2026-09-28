"""Web routes: pages, the JSON API and error handlers (#165).

A Flask blueprint registered by app.py; URLs are unchanged.
"""

import json
import logging
from datetime import UTC

import requests
from flask import Blueprint, jsonify, render_template, request
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from werkzeug.exceptions import HTTPException

from nautobot_maps import alerts, auth, caching, db, inventory, nautobot, settings, timeutil

logger = logging.getLogger(__name__)

bp = Blueprint("web", __name__)


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
    return render_template("index.html", nautobot_url=settings.NAUTOBOT_URL)


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
    if db.dialect():
        conn = None
        try:
            conn = db.get_conn()
            conn.execute("SELECT 1").fetchone()
            checks["database"] = "ok"
        except Exception as exc:
            logger.warning("Health check: database unavailable: %s", exc)
            checks["database"] = "unavailable"
        finally:
            if conn is not None:
                conn.close()
    healthy = all(value == "ok" for value in checks.values())
    return (
        jsonify({"status": "ok" if healthy else "unavailable", "checks": checks}),
        200 if healthy else 503,
    )


@bp.route("/alerts")
def alert_board():
    return render_template(
        "alerts.html",
        nautobot_url=settings.NAUTOBOT_URL,
        tier_definitions=alerts.ALERT_STATUS_TIER_DEFINITIONS,
    )


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
        logger.error("Unexpected error fetching locations: %s", exc)
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
        logger.error("Unexpected error fetching location detail: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@bp.route("/api/alerts")
def api_alerts():
    """Return alert-board summaries for all Nautobot locations."""
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
        logger.error("Unexpected error building alert board: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


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
        all_locations = inventory.get_locations()
    except RuntimeError as exc:
        return nautobot_service_unavailable("Location search unavailable", exc)
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
        logger.error("Could not list criticality overrides: %s", exc)
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
        logger.error("Could not set criticality override: %s", exc)
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
        logger.error("Could not delete criticality override: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Alert lifecycle / case tracking endpoints
# ---------------------------------------------------------------------------


@bp.route("/api/alert-history", methods=["GET"])
@auth.require_role("operator")
def api_alert_history():
    """Return historical alert instances with events and case numbers."""
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
            ORDER BY id DESC
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
        logger.error("Could not fetch alert history: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


# Upper bound on devices linked to one case in a single request.
MAX_CASE_DEVICES = 200


@bp.route("/api/alert-cases", methods=["POST"])
@auth.require_role("operator")
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
        logger.error("Could not add alert case: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Roles proxy endpoints
# ---------------------------------------------------------------------------


@bp.route("/api/roles", methods=["GET"])
def api_list_roles():
    """Return all roles from Nautobot (proxied from extras/roles/)."""
    try:
        roles = nautobot.fetch_all_pages("extras/roles/")
        return jsonify({"roles": roles})
    except RuntimeError as exc:
        return nautobot_service_unavailable("Roles listing unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.error("Unexpected error listing roles: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@bp.route("/api/roles", methods=["POST"])
@auth.require_role("admin")
def api_create_role():
    """Create a new role in Nautobot (proxied to extras/roles/).

    Expected JSON body follows the Nautobot Role schema, e.g.::

        {"name": "Core Router", "color": "aa1409", "content_types": [...]}
    """
    body = request.get_json(silent=True) or {}
    if not body.get("name"):
        return jsonify({"error": "name is required"}), 400
    try:
        created = nautobot.post("extras/roles/", body)
        caching.cache.delete_memoized(nautobot.fetch_all_pages)
        return jsonify(created), 201
    except RuntimeError as exc:
        return nautobot_service_unavailable("Role creation unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        try:
            detail = exc.response.json()
        except Exception:
            detail = "Could not parse Nautobot error response"
        return jsonify({"error": "Failed to communicate with Nautobot API", "detail": detail}), exc.response.status_code
    except Exception as exc:
        logger.error("Unexpected error creating role: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@bp.route("/api/roles/<role_id>", methods=["DELETE"])
@auth.require_role("admin")
def api_delete_role(role_id: str):
    """Delete a role from Nautobot by its UUID (proxied to extras/roles/<id>/)."""
    try:
        nautobot.delete(f"extras/roles/{role_id}/")
        caching.cache.clear()
        return jsonify({"status": "deleted", "id": role_id})
    except RuntimeError as exc:
        return nautobot_service_unavailable("Role deletion unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        if exc.response.status_code == 404:
            return jsonify({"error": "Role not found"}), 404
        return jsonify({"error": "Failed to communicate with Nautobot API"}), exc.response.status_code
    except Exception as exc:
        logger.error("Unexpected error deleting role: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# Location-type proxy endpoints
# ---------------------------------------------------------------------------


@bp.route("/api/location-types", methods=["GET"])
def api_list_location_types():
    """Return all location types from Nautobot (proxied from dcim/location-types/)."""
    try:
        location_types = nautobot.fetch_all_pages("dcim/location-types/")
        return jsonify({"location_types": location_types})
    except RuntimeError as exc:
        return nautobot_service_unavailable("Location type listing unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        return jsonify({"error": "Failed to communicate with Nautobot API"}), 502
    except Exception as exc:
        logger.error("Unexpected error listing location types: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@bp.route("/api/location-types", methods=["POST"])
@auth.require_role("admin")
def api_create_location_type():
    """Create a new location type in Nautobot (proxied to dcim/location-types/).

    Expected JSON body follows the Nautobot LocationType schema, e.g.::

        {"name": "Data Center", "slug": "data-center"}
    """
    body = request.get_json(silent=True) or {}
    if not body.get("name"):
        return jsonify({"error": "name is required"}), 400
    try:
        created = nautobot.post("dcim/location-types/", body)
        caching.cache.clear()
        return jsonify(created), 201
    except RuntimeError as exc:
        return nautobot_service_unavailable("Location type creation unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        try:
            detail = exc.response.json()
        except Exception:
            detail = "Could not parse Nautobot error response"
        return jsonify({"error": "Failed to communicate with Nautobot API", "detail": detail}), exc.response.status_code
    except Exception as exc:
        logger.error("Unexpected error creating location type: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@bp.route("/api/location-types/<lt_id>", methods=["DELETE"])
@auth.require_role("admin")
def api_delete_location_type(lt_id: str):
    """Delete a location type from Nautobot by its UUID (proxied to dcim/location-types/<id>/)."""
    try:
        nautobot.delete(f"dcim/location-types/{lt_id}/")
        caching.cache.clear()
        return jsonify({"status": "deleted", "id": lt_id})
    except RuntimeError as exc:
        return nautobot_service_unavailable("Location type deletion unavailable", exc)
    except requests.HTTPError as exc:
        logger.error("Nautobot API HTTP error: %s", exc)
        if exc.response.status_code == 404:
            return jsonify({"error": "Location type not found"}), 404
        return jsonify({"error": "Failed to communicate with Nautobot API"}), exc.response.status_code
    except Exception as exc:
        logger.error("Unexpected error deleting location type: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
