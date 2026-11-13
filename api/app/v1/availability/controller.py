"""Ported from nautobot_maps/api/availability.py (#604), its API under /api/v1. Availability (#287; on FastAPI since #436): the page, its API and CSV."""

from contextlib import AsyncExitStack
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from loguru import logger

from app.auth import permissions
from app.db import aconnection
from app.db import session as db_session
from app.frontend import controller as frontend
from app.services import availability_service as availability
from app.services import legacy_auth as auth
from app.services import status_link_service as statuslinks
from app.v1 import deps, sections
from app.v1.availability import dtos
from app.v1.common import dtos as common

router = APIRouter(
    route_class=deps.AppRoute,
    dependencies=deps.ROUTER_DEPENDENCIES,
    tags=sections.tag("availability"),
)

PeriodQuery = Annotated[dtos.PeriodQuery, Query()]


async def _availability_report(
    period: dtos.PeriodQuery, conn: aconnection.Connection, with_incidents: bool = False
) -> tuple[dict | None, str, int]:
    """``(report, error, status)`` for the month or from–to and the basis asked
    for (#287, #341).  *with_incidents* (#354): every incident too, streamed
    from *conn*, which the caller keeps open until then."""
    try:
        sites = await availability.board_sites()
    except Exception as exc:
        # Without the board's sites and tenants the figures would be wrong.
        logger.opt(exception=True).warning(
            "Availability report: could not read the alert board: {}", exc
        )
        return (
            None,
            "The alert board could not be read, so the report would miss sites; try again shortly.",
            503,
        )
    try:
        report = await availability.report(
            conn,
            period.month,
            period.basis,
            sites,
            period.from_,
            period.to,
            with_incidents=with_incidents,
        )
    except availability.ReportError as exc:
        return None, exc.message, 400
    return report, "", 200


@router.get(
    "/availability",
    dependencies=[deps.require_permission(permissions.Permission.PAGE_AVAILABILITY)],
    name="availability.availability_page",
)
def availability_page():
    """Availability per site and tenant (#287): ``?month=2026-10`` or
    ``?from=2026-10-03&to=2026-10-09`` (#341), and ``&basis=any_down``.

    Built with Nuxt (#463); the page reads ``/api/availability``.
    """
    return frontend.shell()


@router.get(
    "/api/v1/availability",
    dependencies=[deps.require_permission(permissions.Permission.PAGE_AVAILABILITY)],
    name="availability.api_availability",
    response_model=dtos.Report,
    responses={
        503: {"model": common.Error, "description": "No database, or the board can't be read"}
    },
)
async def api_availability(period: PeriodQuery):
    """Availability per site and tenant for a period (#287, #341).

    Query parameters: ``month`` (``2026-10``; default the current month, up
    to now), or ``from`` and ``to`` (``2026-10-03``, both days included, at
    most 366 days; #341), and ``basis``: ``any_down`` (any monitored device of the site
    down) or ``critical`` (the site at Critical); default
    ``AVAILABILITY_BASIS``.  Time in maintenance windows is not downtime.
    ``status_links``: each tenant's status page link id (#500), ``/status/<id>``, for
    those that have one; only with the ``status.links.view`` permission (#687).
    """
    async with aconnection.connect() as conn:
        report, error, status = await _availability_report(period, conn)
    if report is None:
        return deps.Json({"error": error}, status_code=status)
    # The links only for those who may see them (#687): a link id opens a tenant's page.
    if not auth.may(permissions.Permission.STATUS_LINKS_VIEW):
        return deps.Json(report)
    return deps.Json({**report, "status_links": await _status_links(report)})


async def _status_links(report: dict) -> dict[str, str]:
    """Each tenant's status page link id (#500), for those that have one: a read
    never makes one (#687, ``POST /api/v1/status-links/<tenant>`` does); none when
    the database can't be read."""
    tenants = {t["tenant"] for t in report["tenants"]} | {
        t for site in report["sites"] for t in site["tenants"]
    }
    session = db_session.current()
    try:
        return await statuslinks.existing(session, sorted(tenants))
    except Exception as exc:
        logger.warning("Availability: could not read the status links: {}", type(exc).__name__)
        await session.rollback()
        return {}


@router.get(
    "/api/v1/availability.csv",
    dependencies=[
        deps.require_permission(permissions.Permission.PAGE_AVAILABILITY),
        deps.require_permission(permissions.Permission.EXPORT_CSV),
    ],
    name="availability.api_availability_csv",
)
async def api_availability_csv(period: Annotated[dtos.AvailabilityCsv, Query()]):
    """The availability report as CSV (#287): ``view=sites`` (default; with each
    site's details and outages, #354), ``tenants`` or ``incidents`` (every
    outage in the period, to check the figures); same ``month``,
    ``from``/``to`` and ``basis``.  ``header=1``: the report's period, basis,
    target, totals, and who made it when, above the table."""
    view = period.view or "sites"
    if view not in ("sites", "tenants", "incidents"):
        return deps.Json({"error": "view must be sites, tenants or incidents"}, status_code=400)
    # Every incident is read while the file streams: the connection stays open until then.
    held = AsyncExitStack()
    conn = await held.enter_async_context(aconnection.connect())
    try:
        report, error, code = await _availability_report(
            period, conn, with_incidents=view == "incidents"
        )
    except BaseException:
        await held.aclose()
        raise
    if report is None or view != "incidents":
        await held.aclose()
    if report is None:
        return deps.Json({"error": error}, status_code=code)
    with_header = common.flag(period.header)
    by = auth.get_current_user().get("username") or "" if with_header else ""

    async def lines():
        try:
            async for line in availability.csv_lines(
                report, view, header=with_header, generated_by=by
            ):
                yield line
        finally:
            await held.aclose()

    label = report["period"]["label"].replace(" to ", "_")
    return StreamingResponse(
        lines(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="availability-{view}-{label}.csv"'},
    )
