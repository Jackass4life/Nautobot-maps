"""Liveness probe for container healthchecks; no database access."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["health"])


class HealthDto(BaseModel):
    status: str


@router.get("/healthz", operation_id="getHealth", response_model=HealthDto)
def get_health() -> HealthDto:
    return HealthDto(status="ok")
