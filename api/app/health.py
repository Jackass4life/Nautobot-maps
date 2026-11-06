"""Liveness probe for Docker, load balancers and monitoring (#594): the current
app's ``/healthz``.

No Nautobot or LibreNMS calls: an upstream outage must not mark this app
unhealthy, because restarting it can't fix the upstream. The database must
answer ``SELECT 1`` (within 2 s, inside Docker's 5 s probe timeout), since the
board can't be served without it. Error details are logged, never returned.
"""

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as ProbeTimeout
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app import session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])
PROBE_SECONDS = 2
# Probes run here, so one stuck on a dead database can't hold a request thread.
_probes = ThreadPoolExecutor(max_workers=1, thread_name_prefix="health-probe")
# One probe at a time (#603, #601 reviews): a request while one runs shares it, within
# that probe's deadline; one stuck past it answers "unavailable" at once. Nothing queues.
_lock = threading.Lock()
_running: tuple[Future, float] | None = None


def _probe() -> None:
    with session.probe_engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def _current_probe() -> tuple[Future, float]:
    """The probe running now (shared), or a new one; with when it started."""
    global _running
    with _lock:
        if _running is None or _running[0].done():
            _running = (_probes.submit(_probe), time.monotonic())
        return _running


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
        # The whole probe, connecting included, within PROBE_SECONDS of its start.
        probe, started = _current_probe()
        probe.result(timeout=max(0.0, PROBE_SECONDS - (time.monotonic() - started)))
    except ProbeTimeout:
        logger.warning("Health check: database unavailable: no answer in %ss", PROBE_SECONDS)
        checks["database"] = "unavailable"
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
