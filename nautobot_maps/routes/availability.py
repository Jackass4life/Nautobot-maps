"""Availability (#424): the page, its API and CSV, and the template filters it uses."""

import logging
from urllib.parse import urlencode

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from nautobot_maps import (
    auth,
    availability,
    db,
    settings,
    templating,
)
from nautobot_maps.routes import common

logger = logging.getLogger(__name__)

bp = Blueprint("availability", __name__)


def _availability_report(conn=None, with_incidents: bool = False) -> tuple[dict | None, str, int]:
    """``(report, error, status)`` for the month or from–to and the basis in the
    query string (#287, #341).  *with_incidents* (#354): every incident too,
    streamed from *conn*, which the caller then closes."""
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
        return (
            availability.report(
                conn,
                common.arg("month"),
                common.arg("basis"),
                sites,
                common.arg("from"),
                common.arg("to"),
                with_incidents=with_incidents,
            ),
            "",
            200,
        )
    except availability.ReportError as exc:
        return None, exc.message, 400
    finally:
        if own:
            conn.close()


# The filters every template may use, Flask's and FastAPI's (#436).
for _name, _filter in templating.FILTERS.items():
    bp.add_app_template_filter(_filter, _name)


@bp.route("/availability")
def availability_page():
    """Availability per site and tenant (#287): ``?month=2026-10`` or
    ``?from=2026-10-03&to=2026-10-09`` (#341), and ``&basis=any_down``."""
    report, error, status = _availability_report()
    at = availability.now()
    # The days shown in the form: the report's, or after an error what was
    # asked for.  The form always sends from and to; a whole month is still
    # a month (availability.resolve_period).
    asked = report["period"] if report else {"from": common.arg("from") or "", "to": common.arg("to") or ""}
    basis = report["basis"] if report else settings.AVAILABILITY_BASIS
    return (
        render_template(
            "availability.html",
            report=report,
            error=error,
            from_date=asked["from"],
            to_date=asked["to"],
            basis=basis,
            csv_query=urlencode({"from": asked["from"], "to": asked["to"], "basis": basis}),
            today=f"{at:%Y-%m-%d}",
            # The calendar's limits: today, and the oldest day the history still has.
            oldest_day=availability.oldest_day(at),
        ),
        status,
    )


@bp.route("/api/availability")
def api_availability():
    """Availability per site and tenant for a period (#287, #341).

    Query parameters: ``month`` (``2026-10``; default the current month, up
    to now), or ``from`` and ``to`` (``2026-10-03``, both days included, at
    most 366 days; #341), and ``basis``: ``any_down`` (any monitored device of the site
    down) or ``critical`` (the site at Critical); default
    ``AVAILABILITY_BASIS``.  Time in maintenance windows is not downtime.
    """
    report, error, status = _availability_report()
    return (jsonify(report), 200) if report is not None else (jsonify({"error": error}), status)


@bp.route("/api/availability.csv")
def api_availability_csv():
    """The availability report as CSV (#287): ``view=sites`` (default; with each
    site's details and outages, #354), ``tenants`` or ``incidents`` (every
    outage in the period, to check the figures); same ``month``,
    ``from``/``to`` and ``basis``.  ``header=1``: the report's period, basis,
    target, totals, and who made it when, above the table."""
    view = (request.args.get("view") or "sites").strip()
    if view not in ("sites", "tenants", "incidents"):
        return jsonify({"error": "view must be sites, tenants or incidents"}), 400
    # Every incident is read while the file streams: the connection stays open until then.
    conn = db.get_conn() if view == "incidents" else None
    report, error, code = _availability_report(conn, with_incidents=conn is not None)
    if report is None:
        if conn is not None:
            conn.close()
        return jsonify({"error": error}), code
    header = common.flag_arg("header")
    by = auth.get_current_user().get("username") or "" if header else ""

    def lines():
        try:
            yield from availability.csv_lines(report, view, header=header, generated_by=by)
        finally:
            if conn is not None:
                conn.close()

    return Response(
        stream_with_context(lines()),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="availability-{view}-{report["period"]["label"].replace(" to ", "_")}.csv"'
        },
    )
