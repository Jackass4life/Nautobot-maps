"""The map, the alert board and the API explorer pages (#424).

The other pages live with their area's API (maintenance, availability,
circuits, status), whose helpers they share."""

from flask import Blueprint, current_app, render_template, request

from nautobot_maps import alerts, apidocs, settings
from nautobot_maps.routes import common

bp = Blueprint("pages", __name__)


@bp.route("/")
def index():
    """The map: Nautobot locations, address and GPS search, alert banners."""
    return render_template(
        "index.html",
        nautobot_url=settings.NAUTOBOT_URL,
        tile_url=settings.MAP_TILE_URL,
        tile_attribution=settings.MAP_TILE_ATTRIBUTION,
        view=settings.MAP_DEFAULT_VIEW,
        scope_query=common.scope_query(),
    )


@bp.route("/alerts")
def alert_board():
    """The alert board: every site's severity, down devices, cases and history.

    ``?view=wall`` is the wall-screen view (#243): alarms only, down devices
    listed, no controls.
    """
    return render_template(
        "alerts.html",
        nautobot_url=settings.NAUTOBOT_URL,
        case_url_template=settings.CASE_URL_TEMPLATE,
        tier_definitions=alerts.alert_level_definitions(),
        wall_view=request.args.get("view") == "wall",
        scope_query=common.scope_query(),
    )


@bp.route("/docs")
def api_docs():
    """This page: every page and API endpoint, with Try it (#230)."""
    return render_template("docs.html", groups=apidocs.grouped(current_app))
