"""Monitoring, for machines (#424, #594; #624): Prometheus metrics and the MCP
server (ported from nautobot_maps/api/ops.py, #604), and the liveness probe.

Two routers: ``router`` as the other sections', and ``health_router`` for
/healthz, which answers without the sign-in check: a probe has no login."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from loguru import logger

from app.clients import caching
from app.core.legacy_settings import settings
from app.db import aconnection
from app.db import connection as db
from app.services import alerts_service as alerts
from app.services import coordinates_service as coordinates
from app.services import health_service as health
from app.services import mcp_service as mcp
from app.services import metrics_service as metrics
from app.v1 import deps, sections
from app.v1.monitoring import dtos

router = APIRouter(
    route_class=deps.AppRoute,
    dependencies=deps.ROUTER_DEPENDENCIES,
    tags=sections.tag("monitoring"),
)
health_router = APIRouter(tags=sections.tag("monitoring"))


@router.get("/metrics", name="ops.prometheus_metrics")
async def prometheus_metrics():
    """Prometheus metrics: sync health, open alerts, sites by level (#200)."""
    if not settings.METRICS_ENABLED:
        return deps.Json({"error": "Not found"}, status_code=404)
    try:
        if db.dialect():
            async with aconnection.connect() as conn:
                body = await metrics.collect(conn)
        else:
            body = await metrics.collect(None)
    except Exception as exc:
        logger.warning("Metrics: database unavailable: {}", exc)
        body = await metrics.collect(None)
    try:
        # Sites with missing or wrong coordinates (#368): from the board already
        # stored, never a build (a scrape must stay cheap); left out without one.
        stored = caching.get(alerts.BOARD_CACHE_KEY)
        if stored is not None:
            body += metrics.coordinate_problems(
                coordinates.counts(coordinates.check(stored.get("alerts") or []))
            )
    except Exception as exc:
        logger.warning("Metrics: coordinate check unavailable: {}", exc)
    return Response(body, media_type="text/plain; version=0.0.4; charset=utf-8")


@router.post("/mcp", name="ops.mcp_endpoint")
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


@health_router.get(
    "/healthz",
    operation_id="getHealth",
    response_model=dtos.Health,
    responses={503: {"model": dtos.Health, "description": "The database doesn't answer"}},
)
async def get_health() -> JSONResponse:
    body, healthy = await health.check()
    answer = dtos.Health.model_validate(body)
    return JSONResponse(answer.model_dump(mode="json"), status_code=200 if healthy else 503)


# The path before #594; until the switchover, for probes still pointing at it.
health_router.add_api_route(
    "/api/healthz", get_health, methods=["GET"], include_in_schema=False, name="healthz_alias"
)
