"""Ported from nautobot_maps/api/status.py (#604), its API under /api/v1. A tenant's status page and its API (#291; on FastAPI since #436)."""

from fastapi import APIRouter
from loguru import logger

from app.auth import permissions
from app.db import aconnection
from app.db import session as db_session
from app.db.session import AsyncDbSession
from app.frontend import controller as frontend
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from app.services import legacy_auth as auth
from app.services import status_link_service as statuslinks
from app.services import status_service as status
from app.v1 import deps, sections
from app.v1.common import dtos as common
from app.v1.status_page import dtos

router = APIRouter(
    route_class=deps.AppRoute,
    dependencies=deps.ROUTER_DEPENDENCIES,
    tags=sections.tag("status_page"),
)

UNAVAILABLE = {"error": "Status unavailable, try again shortly"}
# A link that opens nothing, a tenant's name included (#500): the same answer
# as a tenant without sites, so a guess learns nothing.
NOT_FOUND = {"error": "No sites for this tenant"}


async def _tenant_for(link_id: str) -> str | None:
    """The tenant *link_id* opens (#500).  No connection for anything that
    isn't a link id.  Raises when the database can't be read."""
    if not statuslinks.ID_PATTERN.match(link_id):
        return None  # a name, or anything else: no connection for it
    session = db_session.current()
    try:
        return await statuslinks.tenant_for(session, link_id)
    finally:
        # Given back before the board is read, which takes a connection of its own:
        # a request never holds two, so busy requests can't use up the pool
        # waiting for their second (#608 review).
        await session.close()


def _unless_denied(answer: dict, status_code: int):
    """*answer*, or 401/403 for one who may not look at any page: a database
    problem is no clue to them either (#500).  A status token is a key to its
    own page, so its holder is told."""
    if not auth.is_status_token(auth.get_current_user()):
        denied = auth.status_access("")
        if denied is not None:
            raise deps.ApiError(*denied)
    return deps.Json(answer, status_code=status_code)


async def _circuits(tenant: str, board: dict) -> tuple[list[dict], dict[str, str]]:
    """The tenant's active circuits (#511) and the locations above where they
    and its sites are, two queries on one connection; none when they can't be
    read: the page still shows its sites."""
    try:
        async with aconnection.connect() as conn:
            circuits = await inventory.read_tenant_circuits(conn, tenant)
            if not circuits:
                return [], {}
            where = {
                location_id
                for circuit in circuits
                for location_id in circuit.get("location_ids") or []
            }
            where |= {row.get("id") or "" for row in board.get("alerts") or []}
            return circuits, await inventory.read_location_parents(conn, where)
    except Exception as exc:
        logger.warning("Status page: circuits unavailable: {}", type(exc).__name__)
        return [], {}


@router.get("/status/{link_id}", dependencies=[deps.PUBLIC], name="status.status_page")
def status_page(link_id: str):
    """A tenant's status page (#291): its sites and their state, for a customer.

    ``<link_id>`` is the tenant's random link id (#500), not its name.  Built
    with Nuxt (#463); the page reads ``/api/status/<link_id>``, with the key
    from a share link's ``#key=`` (never sent to the server in the URL).
    """
    return frontend.shell(headers={"Referrer-Policy": "no-referrer"})


@router.get(
    "/api/v1/status/{link_id}",
    dependencies=[deps.PUBLIC],
    name="status.api_status",
    response_model=dtos.TenantStatus,
    responses={
        401: {
            "model": common.Error,
            "description": "Not signed in, or a token that no longer works",
        },
        403: {"model": common.Error, "description": "A status token for another tenant"},
        404: {"model": common.Error, "description": "No sites for this tenant, or no such link"},
        503: {
            "model": common.Error,
            "description": "No board yet, it can't be read, or no database",
        },
    },
)
async def api_status(link_id: str):
    """A tenant's sites and their state (#291), for the tenant's random link
    id (#500; a name answers 404): ``operational``, ``degraded``,
    ``outage``, ``maintenance`` (with ``until``) or ``unknown``, and ``since``;
    and the tenant's description from Nautobot (#482), which the page shows.

    Where each site is (address, coordinates; #493), but no devices, IPs,
    cases or other tenants.  Open as the board is, or
    with a status token for this tenant (``Authorization: Bearer``).
    """
    try:
        tenant = await _tenant_for(link_id)
    except Exception as exc:
        logger.warning("Status page unavailable: {}", type(exc).__name__)
        return _unless_denied(UNAVAILABLE, 503)
    return await _answer(tenant)


async def _answer(tenant: str | None):
    # Access first: one who may not look gets the same answer for any link,
    # so whether one exists is no clue (a status token: 403 for all others).
    denied = auth.status_access(tenant or "")
    if denied is not None:
        raise deps.ApiError(*denied)
    if tenant is None:
        return deps.Json(NOT_FOUND, status_code=404)
    try:
        board = await alerts.get_alert_board_data()
        circuits, parents = await _circuits(tenant, board)
        result = status.tenant_status(board, tenant, circuits, parents)
    except Exception as exc:
        logger.warning("Status page unavailable: {}", type(exc).__name__)
        return deps.Json(UNAVAILABLE, status_code=503)
    if result is None:
        # Before the first sync the board is empty: that's not "no sites".
        if not await inventory.snapshot_initialized():
            return deps.Json(UNAVAILABLE, status_code=503)
        return deps.Json(NOT_FOUND, status_code=404)
    return deps.Json(result, headers={"Cache-Control": "no-store"})


# --- The status links (#529, #687): NWO and admin keep them ---------------------

NO_TENANT = {
    503: {"model": common.Error, "description": "No database"},
    404: {"model": common.Error, "description": "No such tenant, or no link"},
}
VIEW = deps.require_permission(permissions.Permission.STATUS_LINKS_VIEW)
MANAGE = deps.require_permission(permissions.Permission.STATUS_LINKS_MANAGE)


def _who() -> str:
    return auth.get_current_user().get("username") or "unknown"


async def _tenant_or_404(db, tenant: str) -> str:  # type: ignore[no-untyped-def]
    """*tenant* as Nautobot spells it; 404 for one Nautobot doesn't have (a typo must
    not make a link, or leave a leaked one working)."""
    name = await statuslinks.tenant_name(db, tenant)
    if name is None:
        raise deps.ApiError({"error": statuslinks.unknown_tenant(tenant)}, 404)
    return name


@router.get(
    "/api/v1/status-links",
    name="status.api_status_links",
    response_model=dtos.StatusLinks,
    responses={503: NO_TENANT[503]},
    dependencies=[VIEW],
)
async def api_status_links(db: AsyncDbSession):
    """Every tenant's status page link (#687), by tenant: ``/status/<id>`` and when it
    was made. Send one with a status token, ``/status/<id>#key=<token>``."""
    return deps.Json({"links": await statuslinks.listing(db)})


@router.post(
    "/api/v1/status-links/{tenant:path}/replace",
    name="status.api_status_link_replace",
    response_model=dtos.StatusLinkReplaced,
    responses=NO_TENANT,
    dependencies=[MANAGE],
)
async def api_status_link_replace(tenant: str, db: AsyncDbSession):
    """Replace a tenant's status page link that leaked (#529), as
    ``status-link TENANT --new``: the old link stops working at once; the
    tenant's status tokens keep working with the new one."""
    name = await _tenant_or_404(db, tenant)
    link_id = await statuslinks.replace(db, name)
    logger.info("Status link of tenant {!r} replaced by {}", name, _who())
    return deps.Json(
        {
            "tenant": name,
            "status_link": f"/status/{link_id}",
            "message": "The old link no longer works. The tenant's status tokens still work: send them with this link.",
        }
    )


@router.get(
    "/api/v1/status-links/{tenant:path}",
    name="status.api_status_link",
    response_model=dtos.StatusLink,
    responses=NO_TENANT,
    dependencies=[VIEW],
)
async def api_status_link(tenant: str, db: AsyncDbSession):
    """A tenant's status page link, ``/status/<id>`` (#529); 404 when it has none yet
    (#687: reading never makes one; ``POST`` does)."""
    name = await _tenant_or_404(db, tenant)
    link_id = (await statuslinks.existing(db, [name])).get(name)
    if link_id is None:
        raise deps.ApiError({"error": f"No status link for tenant {name!r} yet"}, 404)
    return deps.Json({"tenant": name, "status_link": f"/status/{link_id}"})


@router.post(
    "/api/v1/status-links/{tenant:path}",
    name="status.api_status_link_create",
    response_model=dtos.StatusLink,
    responses=NO_TENANT,
    dependencies=[MANAGE],
)
async def api_status_link_create(tenant: str, db: AsyncDbSession):
    """Make a tenant's status page link (#687), as ``status-link TENANT``; one it has
    already is answered as it is, so asking again never changes it."""
    name = await _tenant_or_404(db, tenant)
    had = await statuslinks.existing(db, [name])
    link_id = (await statuslinks.ensure(db, [name]))[name]
    if name not in had:
        logger.info("Status link of tenant {!r} made by {}", name, _who())
    return deps.Json({"tenant": name, "status_link": f"/status/{link_id}"})


@router.delete(
    "/api/v1/status-links/{tenant:path}",
    name="status.api_status_link_delete",
    response_model=dtos.StatusLinkDeleted,
    responses=NO_TENANT,
    dependencies=[MANAGE],
)
async def api_status_link_delete(tenant: str, db: AsyncDbSession):
    """Delete a tenant's status page link (#687): customers using it lose access at
    once. Its status tokens open nothing until a new link is made (``POST``)."""
    name = await _tenant_or_404(db, tenant)
    if not await statuslinks.delete(db, name):
        raise deps.ApiError({"error": f"No status link for tenant {name!r}"}, 404)
    logger.info("Status link of tenant {!r} deleted by {}", name, _who())
    return deps.Json(
        {
            "tenant": name,
            "message": "The link no longer works. Make a new one to share the tenant's status page again.",
        }
    )
