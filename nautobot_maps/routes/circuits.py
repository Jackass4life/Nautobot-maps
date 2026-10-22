"""Circuit mapping (#424): the page and its API."""

import logging

from flask import Blueprint, Response, jsonify, render_template

from nautobot_maps import (
    circuits,
    db,
    settings,
)

logger = logging.getLogger(__name__)

bp = Blueprint("circuits", __name__)


@bp.route("/circuits")
def circuits_page():
    """Circuit mapping (#385): circuits without a termination and their likely site; the data comes from the API."""
    return render_template("circuits.html", nautobot_url=settings.NAUTOBOT_URL)


def _circuit_mapping() -> tuple[dict | None, tuple | None]:
    conn = db.get_conn()
    if conn is None:
        return None, (jsonify({"error": "Persistence DB not configured"}), 503)
    try:
        return circuits.mapping(conn), None
    finally:
        conn.close()


@bp.route("/api/circuit-mapping", methods=["GET"])
def api_circuit_mapping():
    """Circuits without a termination at a location, Decommissioned ones left out (#385), with the sites their
    description's address points to: ``summary`` (``total``, ``several``,
    ``none``, ``one``) and ``circuits`` (``id``, ``cid``, ``provider``,
    ``circuit_type``, ``status``, ``tenant``, ``commit_rate``, ``description``,
    ``group``, ``sites``), several sites first, then none, then one."""
    data, error = _circuit_mapping()
    return error or jsonify(data)


@bp.route("/api/circuit-mapping.csv", methods=["GET"])
def api_circuit_mapping_csv():
    """The circuit mapping as CSV (#385): one row per circuit, sites joined with "; "."""
    data, error = _circuit_mapping()
    if error:
        return error
    return Response(
        circuits.csv_lines(data),
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="circuit-mapping.csv"'},
    )
