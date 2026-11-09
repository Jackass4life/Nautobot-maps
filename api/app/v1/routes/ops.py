"""Ported from nautobot_maps/api/ops.py (#604), its API under /api/v1. For monitoring and machines (#424, on FastAPI since #436): health, metrics,
the app's freshness for the top bar, MCP and the endpoint list."""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app import db
from app.clients import caching
from app.services import alerts_service as alerts
from app.services import apidocs_service as apidocs
from app.services import coordinates_service as coordinates
from app.services import mcp_service as mcp
from app.services import metrics_service as metrics
from app.services import wireless_service as wireless
from app.utils.legacy_settings import settings
from app.v1 import deps, sections
from app.v1.answers import ops as answers

logger = logging.getLogger(__name__)

# No router tag (#623): metrics and MCP are Monitoring, the rest belongs to other sections.
router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES)


@router.get("/metrics", name="ops.prometheus_metrics", tags=sections.tag("monitoring"))
def prometheus_metrics():
    """Prometheus metrics: sync health, open alerts, sites by level (#200)."""
    if not settings.METRICS_ENABLED:
        return deps.Json({"error": "Not found"}, status_code=404)
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
            body += metrics.coordinate_problems(
                coordinates.counts(coordinates.check(stored.get("alerts") or []))
            )
    except Exception as exc:
        logger.warning("Metrics: coordinate check unavailable: %s", exc)
    finally:
        if conn is not None:
            conn.close()
    return Response(body, media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get(
    "/api/v1/app-status",
    name="ops.api_app_status",
    response_model=answers.AppStatus,
    tags=sections.tag("shared"),
)
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
            "WIRELESS_SYNC_INTERVAL_SECONDS": settings.WIRELESS_SYNC_INTERVAL_SECONDS,
        },
        now=time.time(),
        wireless=tuple(
            (wireless.source(c), wireless.display_name(c)) for c in wireless.configured()
        ),
    )
    return deps.Json(body, headers={"Cache-Control": "no-store"})


@router.post("/mcp", name="ops.mcp_endpoint", tags=sections.tag("monitoring"))
def mcp_endpoint(body: Annotated[bytes, Depends(deps.limited_body(mcp.MAX_REQUEST_BYTES))]):
    """MCP server for AI assistants (#250): the alert board as tools.

    Off unless MCP_ENABLED=true.  Streamable HTTP, stateless, protocol
    2026-07-28 and the older initialize-based versions.  Tools run the
    matching API routes with the caller's sign-in and roles.
    """
    # A sync route: in a worker thread, where the tools' in-process calls run.
    if not settings.MCP_ENABLED:
        return deps.Json({"error": "Not found"}, status_code=404)
    answer, status = mcp.handle(body)
    if answer is None:
        return Response(status_code=status)
    return deps.Json(answer, status_code=status)


@router.get(
    "/api/v1/endpoints",
    name="ops.api_endpoints",
    response_model=answers.Endpoints,
    tags=sections.tag("documentation"),
)
def api_endpoints():
    """Every page and API endpoint as JSON: methods, parameters, body and role (#230)."""
    return deps.Json({"endpoints": apidocs.endpoints()})
