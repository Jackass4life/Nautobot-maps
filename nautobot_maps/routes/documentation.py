"""The documentation as pages, /help (#433): README, docs/ and CONTRIBUTING."""

from flask import Blueprint, abort, render_template, send_from_directory

from nautobot_maps import guide

bp = Blueprint("help", __name__)


@bp.route("/help")
@bp.route("/help/<slug>")
def help_page(slug: str = "overview"):
    """The documentation: the overview, then each guide."""
    found = guide.page(slug)
    if found is None:
        abort(404)
    return render_template("help.html", page=found, contents=guide.contents())


@bp.route("/help/images/<path:name>")
def help_image(name: str):
    """The screenshots the documentation shows."""
    return send_from_directory(guide.IMAGES, name, max_age=3600)
