"""Ported from nautobot_maps/api/circuits.py (#604), its API under /api/v1. Circuit mapping (#385; on FastAPI since #436): the page and its API."""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.db import connection as db
from app.frontend import controller as frontend
from app.services import circuits_service as circuits
from app.v1 import deps, sections
from app.v1.circuits import dtos
from app.v1.common import dtos as common

router = APIRouter(
    route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES, tags=sections.tag("circuits")
)


@router.get("/circuits", name="circuits.circuits_page")
def circuits_page():
    """Circuit mapping (#385): circuits without a termination and their likely site.

    Built with Nuxt (#463); the page reads ``/api/circuit-mapping``.
    """
    return frontend.shell()


def _circuit_mapping() -> dict:
    conn = db.get_conn()
    if conn is None:
        raise deps.ApiError({"error": "Persistence DB not configured"}, 503)
    try:
        return circuits.mapping(conn)
    finally:
        conn.close()


@router.get(
    "/api/v1/circuit-mapping",
    name="circuits.api_circuit_mapping",
    response_model=dtos.CircuitMapping,
    responses={503: {"model": common.Error, "description": "No database"}},
)
def api_circuit_mapping():
    """Circuits without a termination at a location, Decommissioned ones left out (#385), with the sites their
    description's address points to: ``summary`` (``total``, ``several``,
    ``none``, ``one``) and ``circuits`` (``id``, ``cid``, ``provider``,
    ``circuit_type``, ``status``, ``tenant``, ``commit_rate``, ``description``,
    ``group``, ``sites``), several sites first, then none, then one."""
    return deps.Json(_circuit_mapping())


@router.get("/api/v1/circuit-mapping.csv", name="circuits.api_circuit_mapping_csv")
def api_circuit_mapping_csv():
    """The circuit mapping as CSV (#385): one row per circuit, sites joined with "; "."""
    data = _circuit_mapping()
    return StreamingResponse(
        circuits.csv_lines(data),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="circuit-mapping.csv"'},
    )
