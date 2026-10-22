"""For monitoring and machines (#424): health, metrics, the app's freshness for
the top bar, MCP and the endpoint list."""

import logging
import time

from flask import Blueprint, current_app, jsonify

from nautobot_maps import alerts, apidocs, caching, coordinates, db, mcp, metrics, settings

logger = logging.getLogger(__name__)

bp = Blueprint("ops", __name__)


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
    try:
        # Sites with missing or wrong coordinates (#368): from the board already
        # stored, never a build (a scrape must stay cheap); left out without one.
        stored = caching.get(alerts.BOARD_CACHE_KEY)
        if stored is not None:
            body += metrics.coordinate_problems(coordinates.counts(coordinates.check(stored.get("alerts") or [])))
    except Exception as exc:
        logger.warning("Metrics: coordinate check unavailable: %s", exc)
    finally:
        if conn is not None:
            conn.close()
    return body, 200, {"Content-Type": "text/plain; version=0.0.4; charset=utf-8"}


@bp.route("/api/app-status")
def api_app_status():
    """How fresh the app's data is, for the top bar on every page (#419).

    Read from the sync state the scheduler records: never calls Nautobot or
    LibreNMS, so every open tab polling once a minute adds nothing upstream.
    Response: ``state`` (``ok``, ``stale`` or ``down``), ``updated_at`` (the
    oldest source's last good sync) and ``sources`` (``name``, ``state``,
    ``message``, ``last_success``).
    """
    # No database configured: nothing synced, not an outage.
    rows = {} if not db.dialect() else None
    if db.dialect():
        conn = None
        try:
            conn = db.get_conn(connect_timeout=2)
            rows = metrics.sync_rows(conn)
        except Exception as exc:
            logger.warning("App status: database unavailable: %s", exc)
        finally:
            if conn is not None:
                conn.close()
    body = metrics.app_status(
        rows,
        librenms=bool(settings.LIBRENMS_URL and settings.LIBRENMS_API_TOKEN),
        intervals={
            "INVENTORY_SYNC_INTERVAL_SECONDS": settings.INVENTORY_SYNC_INTERVAL_SECONDS,
            "LIBRENMS_SYNC_INTERVAL_SECONDS": settings.LIBRENMS_SYNC_INTERVAL_SECONDS,
        },
        now=time.time(),
    )
    return jsonify(body), 200, {"Cache-Control": "no-store"}


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
