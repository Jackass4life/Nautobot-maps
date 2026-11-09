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
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app import session
from app.services import metrics_service
from app.v1 import sections

logger = logging.getLogger(__name__)

router = APIRouter(tags=sections.tag("monitoring"))
PROBE_SECONDS = 2
# Probes run here, so one stuck on a dead database can't hold a request thread.
_probes = ThreadPoolExecutor(max_workers=1, thread_name_prefix="health-probe")
# One probe at a time (#603, #601 reviews): a request while one runs shares it, within
# that probe's deadline; one stuck past it answers "unavailable" at once. Nothing queues.
_lock = threading.Lock()
_running: tuple[Future, float] | None = None


def _probe() -> int | None:
    """``SELECT 1``, then the inventory's sync age, as the current app's /healthz (#604):
    both on the probe's own connection (#608 review), never the app's pool, so a busy
    pool can't make a healthy database look down."""
    with session.probe_engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        # A fresh connection (NullPool), used once: dict rows for the metrics' query.
        driver = conn.connection.driver_connection
        driver.row_factory = dict_row
        return metrics_service.inventory_sync_age_seconds(driver)


def _current_probe() -> tuple[Future, float]:
    """The probe running now (shared), or a new one; with when it started."""
    global _running
    with _lock:
        if _running is None or _running[0].done():
            _running = (_probes.submit(_probe), time.monotonic())
        return _running


class HealthChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: Literal["ok"]
    database: Literal["ok", "unavailable"]


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "unavailable"]
    checks: HealthChecks
    inventory_sync_age_seconds: int | None = Field(
        None,
        description="Since the Nautobot inventory last synced; null when it never has.",
    )


@router.get(
    "/healthz",
    operation_id="getHealth",
    response_model=Health,
    responses={503: {"model": Health, "description": "The database doesn't answer"}},
)
def get_health() -> JSONResponse:
    checks = {"app": "ok", "database": "ok"}
    # Informational, never a failure: restarting can't fix a sync that fails upstream (#200).
    sync_age = None
    try:
        # The whole probe, connecting included, within PROBE_SECONDS of its start.
        probe, started = _current_probe()
        sync_age = probe.result(timeout=max(0.0, PROBE_SECONDS - (time.monotonic() - started)))
    except ProbeTimeout:
        logger.warning("Health check: database unavailable: no answer in %ss", PROBE_SECONDS)
        checks["database"] = "unavailable"
    except Exception as exc:  # noqa: BLE001 - any failure is "unavailable"; logged, not returned
        logger.warning("Health check: database unavailable: %s", type(exc).__name__)
        checks["database"] = "unavailable"
    healthy = all(value == "ok" for value in checks.values())
    body = Health.model_validate(
        {
            "status": "ok" if healthy else "unavailable",
            "checks": checks,
            "inventory_sync_age_seconds": sync_age,
        }
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if healthy else 503)


# The path before #594; until the switchover, for probes still pointing at it.
router.add_api_route(
    "/api/healthz", get_health, methods=["GET"], include_in_schema=False, name="healthz_alias"
)
