"""Liveness probe for Docker, load balancers and monitoring (#594): the current
app's ``/healthz``.

No Nautobot or LibreNMS calls: an upstream outage must not mark this app
unhealthy, because restarting it can't fix the upstream. The database must
answer ``SELECT 1`` (within 2 s, inside Docker's 5 s probe timeout), since the
board can't be served without it. Error details are logged, never returned.
"""

import logging
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app import session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class HealthChecksDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: Literal["ok"]
    database: Literal["ok", "unavailable"]


class HealthDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "unavailable"]
    checks: HealthChecksDto
    inventory_sync_age_seconds: int | None = Field(
        None, description="Since the Nautobot inventory last synced (step 5 of #587: always null)."
    )


@router.get(
    "/healthz",
    operation_id="getHealth",
    response_model=HealthDto,
    responses={503: {"model": HealthDto, "description": "The database doesn't answer"}},
)
def get_health() -> JSONResponse:
    checks = {"app": "ok", "database": "ok"}
    try:
        with session.probe_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any failure is "unavailable"; logged, not returned
        logger.warning("Health check: database unavailable: %s", type(exc).__name__)
        checks["database"] = "unavailable"
    healthy = all(value == "ok" for value in checks.values())
    body = HealthDto.model_validate(
        {"status": "ok" if healthy else "unavailable", "checks": checks}
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if healthy else 503)


# The path before #594; until the switchover, for probes still pointing at it.
router.add_api_route("/api/healthz", get_health, methods=["GET"], include_in_schema=False)
