"""A tenant's status page and its API (#291; on FastAPI since #436)."""

import logging

from fastapi import APIRouter

from nautobot_maps import alerts, auth, db, inventory, status, statuslinks
from nautobot_maps.api import deps, frontend
from nautobot_maps.api.answers import common
from nautobot_maps.api.answers import status as answers

logger = logging.getLogger(__name__)

router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES, tags=["Status page"])

UNAVAILABLE = {"error": "Status unavailable, try again shortly"}
# A link that opens nothing, a tenant's name included (#500): the same answer
# as a tenant without sites, so a guess learns nothing.
NOT_FOUND = {"error": "No sites for this tenant"}


def _tenant_for(link_id: str) -> str | None:
    """The tenant *link_id* opens (#500); raises when the database can't be read."""
    if not statuslinks.ID_PATTERN.match(link_id):
        return None
    conn = db.get_conn()
    if conn is None:
        raise RuntimeError("no database")
    try:
        return statuslinks.tenant_for(conn, link_id)
    finally:
        conn.close()


@router.get("/status/{link_id:path}", name="status.status_page")
def status_page(link_id: str):
    """A tenant's status page (#291): its sites and their state, for a customer.

    ``<link_id>`` is the tenant's random link id (#500), not its name.  Built
    with Nuxt (#463); the page reads ``/api/status/<link_id>``, with the key
    from a share link's ``#key=`` (never sent to the server in the URL).
    """
    return frontend.shell(headers={"Referrer-Policy": "no-referrer"})


@router.get(
    "/api/status/{link_id:path}",
    name="status.api_status",
    response_model=answers.TenantStatus,
    responses={
        401: {"model": common.Error, "description": "Not signed in, or a token that no longer works"},
        403: {"model": common.Error, "description": "A status token for another tenant"},
        404: {"model": common.Error, "description": "No sites for this tenant, or no such link"},
        503: {"model": common.Error, "description": "No board yet, or it can't be read"},
    },
)
def api_status(link_id: str):
    """A tenant's sites and their state (#291), for the tenant's random link
    id (#500; a name answers 404): ``operational``, ``degraded``,
    ``outage``, ``maintenance`` (with ``until``) or ``unknown``, and ``since``;
    and the tenant's description from Nautobot (#482), which the page shows.

    Where each site is (address, coordinates; #493), but no devices, IPs,
    cases or other tenants.  Open as the board is, or
    with a status token for this tenant (``Authorization: Bearer``).
    """
    try:
        tenant = _tenant_for(link_id)
    except Exception as exc:
        logger.warning("Status page unavailable: %s", type(exc).__name__)
        return deps.Json(UNAVAILABLE, status_code=503)
    # Access first: one who may not look gets the same answer for any link,
    # so whether one exists is no clue (a status token: 403 for all others).
    denied = auth.status_access(tenant or "")
    if denied is not None:
        raise deps.ApiError(*denied)
    if tenant is None:
        return deps.Json(NOT_FOUND, status_code=404)
    try:
        result = status.tenant_status(alerts.get_alert_board_data(), tenant)
    except Exception as exc:
        logger.warning("Status page unavailable: %s", type(exc).__name__)
        return deps.Json(UNAVAILABLE, status_code=503)
    if result is None:
        # Before the first sync the board is empty: that's not "no sites".
        if not inventory.snapshot_initialized():
            return deps.Json(UNAVAILABLE, status_code=503)
        return deps.Json(NOT_FOUND, status_code=404)
    return deps.Json(result, headers={"Cache-Control": "no-store"})
