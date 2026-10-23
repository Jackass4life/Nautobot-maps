"""Circuit mapping (#385; on FastAPI since #436): the page and its API."""

import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from nautobot_maps import circuits, db, settings
from nautobot_maps.api import deps, pages

logger = logging.getLogger(__name__)

router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES)


@router.get("/circuits", name="circuits.circuits_page")
def circuits_page():
    """Circuit mapping (#385): circuits without a termination and their likely site; the data comes from the API."""
    return pages.page("circuits.html", nautobot_url=settings.NAUTOBOT_URL)


def _circuit_mapping() -> dict:
    conn = db.get_conn()
    if conn is None:
        raise deps.ApiError({"error": "Persistence DB not configured"}, 503)
    try:
        return circuits.mapping(conn)
    finally:
        conn.close()


@router.get("/api/circuit-mapping", name="circuits.api_circuit_mapping")
def api_circuit_mapping():
    """Circuits without a termination at a location, Decommissioned ones left out (#385), with the sites their
    description's address points to: ``summary`` (``total``, ``several``,
    ``none``, ``one``) and ``circuits`` (``id``, ``cid``, ``provider``,
    ``circuit_type``, ``status``, ``tenant``, ``commit_rate``, ``description``,
    ``group``, ``sites``), several sites first, then none, then one."""
    return deps.Json(_circuit_mapping())


@router.get("/api/circuit-mapping.csv", name="circuits.api_circuit_mapping_csv")
def api_circuit_mapping_csv():
    """The circuit mapping as CSV (#385): one row per circuit, sites joined with "; "."""
    data = _circuit_mapping()
    return StreamingResponse(
        circuits.csv_lines(data),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="circuit-mapping.csv"'},
    )
