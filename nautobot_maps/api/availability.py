"""Availability (#287; on FastAPI since #436): the page, its API and CSV."""

import logging
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from nautobot_maps import auth, availability, db, statuslinks
from nautobot_maps.api import deps, frontend, models
from nautobot_maps.api.answers import availability as answers
from nautobot_maps.api.answers import common

logger = logging.getLogger(__name__)

router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES, tags=["Availability"])

PeriodQuery = Annotated[models.Period, Query()]


def _availability_report(
    period: models.Period, conn=None, with_incidents: bool = False
) -> tuple[dict | None, str, int]:
    """``(report, error, status)`` for the month or from–to and the basis asked
    for (#287, #341).  *with_incidents* (#354): every incident too, streamed
    from *conn*, which the caller then closes."""
    own = conn is None
    conn = conn or db.get_conn()
    if conn is None:
        return None, "The availability report needs the PostgreSQL database (NAUTOBOT_MAPS_DATABASE_URL).", 503
    try:
        try:
            sites = availability.board_sites()
        except Exception as exc:
            # Without the board's sites and tenants the figures would be wrong.
            logger.warning("Availability report: could not read the alert board: %s", exc, exc_info=True)
            return None, "The alert board could not be read, so the report would miss sites; try again shortly.", 503
        report = availability.report(
            conn, period.month, period.basis, sites, period.from_, period.to, with_incidents=with_incidents
        )
        return report, "", 200
    except availability.ReportError as exc:
        return None, exc.message, 400
    finally:
        if own:
            conn.close()


@router.get("/availability", name="availability.availability_page")
def availability_page():
    """Availability per site and tenant (#287): ``?month=2026-10`` or
    ``?from=2026-10-03&to=2026-10-09`` (#341), and ``&basis=any_down``.

    Built with Nuxt (#463); the page reads ``/api/availability``.
    """
    return frontend.shell()


@router.get(
    "/api/availability",
    name="availability.api_availability",
    response_model=answers.Report,
    responses={503: {"model": common.Error, "description": "No database, or the board can't be read"}},
)
def api_availability(period: PeriodQuery):
    """Availability per site and tenant for a period (#287, #341).

    Query parameters: ``month`` (``2026-10``; default the current month, up
    to now), or ``from`` and ``to`` (``2026-10-03``, both days included, at
    most 366 days; #341), and ``basis``: ``any_down`` (any monitored device of the site
    down) or ``critical`` (the site at Critical); default
    ``AVAILABILITY_BASIS``.  Time in maintenance windows is not downtime.
    ``status_links``: each tenant's status page link id (#500), ``/status/<id>``.
    """
    report, error, status = _availability_report(period)
    if report is None:
        return deps.Json({"error": error}, status_code=status)
    return deps.Json({**report, "status_links": _status_links(report)})


def _status_links(report: dict) -> dict[str, str]:
    """Each tenant's status page link id (#500), made for those without one;
    none (no links shown) when the database can't be written."""
    tenants = {t["tenant"] for t in report["tenants"]} | {t for site in report["sites"] for t in site["tenants"]}
    conn = db.get_conn()
    if conn is None:
        return {}
    try:
        return statuslinks.ensure(conn, sorted(tenants))
    except Exception as exc:
        logger.warning("Availability: could not read the status links: %s", type(exc).__name__)
        return {}
    finally:
        conn.close()


@router.get("/api/availability.csv", name="availability.api_availability_csv")
def api_availability_csv(period: Annotated[models.AvailabilityCsv, Query()]):
    """The availability report as CSV (#287): ``view=sites`` (default; with each
    site's details and outages, #354), ``tenants`` or ``incidents`` (every
    outage in the period, to check the figures); same ``month``,
    ``from``/``to`` and ``basis``.  ``header=1``: the report's period, basis,
    target, totals, and who made it when, above the table."""
    view = period.view or "sites"
    if view not in ("sites", "tenants", "incidents"):
        return deps.Json({"error": "view must be sites, tenants or incidents"}, status_code=400)
    # Every incident is read while the file streams: the connection stays open until then.
    conn = db.get_conn() if view == "incidents" else None
    report, error, code = _availability_report(period, conn, with_incidents=conn is not None)
    if report is None:
        if conn is not None:
            conn.close()
        return deps.Json({"error": error}, status_code=code)
    with_header = models.flag(period.header)
    by = auth.get_current_user().get("username") or "" if with_header else ""

    def lines():
        try:
            yield from availability.csv_lines(report, view, header=with_header, generated_by=by)
        finally:
            if conn is not None:
                conn.close()

    label = report["period"]["label"].replace(" to ", "_")
    return StreamingResponse(
        lines(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="availability-{view}-{label}.csv"'},
    )
