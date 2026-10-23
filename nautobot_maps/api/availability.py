"""Availability (#287; on FastAPI since #436): the page, its API and CSV."""

import logging
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from nautobot_maps import auth, availability, db, settings
from nautobot_maps.api import deps, models, pages

logger = logging.getLogger(__name__)

router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES)

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
def availability_page(period: PeriodQuery):
    """Availability per site and tenant (#287): ``?month=2026-10`` or
    ``?from=2026-10-03&to=2026-10-09`` (#341), and ``&basis=any_down``."""
    report, error, status = _availability_report(period)
    at = availability.now()
    # The days shown in the form: the report's, or after an error what was
    # asked for.  The form always sends from and to; a whole month is still
    # a month (availability.resolve_period).
    asked = report["period"] if report else {"from": period.from_ or "", "to": period.to or ""}
    basis = report["basis"] if report else settings.AVAILABILITY_BASIS
    return pages.page(
        "availability.html",
        status_code=status,
        report=report,
        error=error,
        from_date=asked["from"],
        to_date=asked["to"],
        basis=basis,
        csv_query=urlencode({"from": asked["from"], "to": asked["to"], "basis": basis}),
        today=f"{at:%Y-%m-%d}",
        # The calendar's limits: today, and the oldest day the history still has.
        oldest_day=availability.oldest_day(at),
    )


@router.get("/api/availability", name="availability.api_availability")
def api_availability(period: PeriodQuery):
    """Availability per site and tenant for a period (#287, #341).

    Query parameters: ``month`` (``2026-10``; default the current month, up
    to now), or ``from`` and ``to`` (``2026-10-03``, both days included, at
    most 366 days; #341), and ``basis``: ``any_down`` (any monitored device of the site
    down) or ``critical`` (the site at Critical); default
    ``AVAILABILITY_BASIS``.  Time in maintenance windows is not downtime.
    """
    report, error, status = _availability_report(period)
    return deps.Json(report) if report is not None else deps.Json({"error": error}, status_code=status)


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
