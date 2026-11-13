"""Ported from nautobot_maps/api/circuits.py (#604), its API under /api/v1. Circuit mapping (#385; on FastAPI since #436): the page and its API."""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.auth import permissions
from app.db import aconnection
from app.frontend import controller as frontend
from app.services import circuits_service as circuits
from app.v1 import deps, sections
from app.v1.circuits import dtos
from app.v1.common import dtos as common

router = APIRouter(
    route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES, tags=sections.tag("circuits")
)


@router.get(
    "/circuits",
    dependencies=[deps.require_permission(permissions.Permission.PAGE_CIRCUITS)],
    name="circuits.circuits_page",
)
def circuits_page():
    """Circuit mapping (#385): circuits without a termination and their likely site.

    Built with Nuxt (#463); the page reads ``/api/circuit-mapping``.
    """
    return frontend.shell()


async def _circuit_mapping() -> dict:
    async with aconnection.connect() as conn:
        return await circuits.mapping(conn)


@router.get(
    "/api/v1/circuit-mapping",
    dependencies=[deps.require_permission(permissions.Permission.PAGE_CIRCUITS)],
    name="circuits.api_circuit_mapping",
    response_model=dtos.CircuitMapping,
    responses={503: {"model": common.Error, "description": "No database"}},
)
async def api_circuit_mapping():
    """Circuits without a termination at a location, Decommissioned ones left out (#385), with the sites their
    description's address points to: ``summary`` (``total``, ``several``,
    ``none``, ``one``) and ``circuits`` (``id``, ``cid``, ``provider``,
    ``circuit_type``, ``status``, ``tenant``, ``commit_rate``, ``description``,
    ``group``, ``sites``), several sites first, then none, then one."""
    return deps.Json(await _circuit_mapping())


@router.get(
    "/api/v1/circuit-mapping.csv",
    dependencies=[
        deps.require_permission(permissions.Permission.PAGE_CIRCUITS),
        deps.require_permission(permissions.Permission.EXPORT_CSV),
    ],
    name="circuits.api_circuit_mapping_csv",
)
async def api_circuit_mapping_csv():
    """The circuit mapping as CSV (#385): one row per circuit, sites joined with "; "."""
    data = await _circuit_mapping()
    return StreamingResponse(
        circuits.csv_lines(data),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="circuit-mapping.csv"'},
    )


# The same page in each role's workspace (#697).
deps.workspace_pages(router, "circuits", circuits_page, permissions.Permission.PAGE_CIRCUITS)
