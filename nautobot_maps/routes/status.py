"""A tenant's status page and its API (#291, #424)."""

import logging

from flask import Blueprint, current_app, jsonify, render_template

from nautobot_maps import (
    alerts,
    auth,
    inventory,
    status,
)

logger = logging.getLogger(__name__)

bp = Blueprint("status", __name__)


@bp.route("/status/<path:tenant>")
def status_page(tenant: str):
    """A tenant's status page (#291): its sites and their state, for a customer.

    The page holds no data; it reads ``/api/status/<tenant>``, with the key
    from a share link's ``#key=`` (never sent to the server in the URL).
    """
    response = current_app.make_response(render_template("status.html", tenant=tenant.strip()))
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.route("/api/status/<path:tenant>")
def api_status(tenant: str):
    """A tenant's sites and their state (#291): ``operational``, ``degraded``,
    ``outage``, ``maintenance`` (with ``until``) or ``unknown``, and ``since``.

    No devices, addresses, cases or other tenants.  Open as the board is, or
    with a status token for this tenant (``Authorization: Bearer``).
    """
    denied = auth.status_access(tenant)
    if denied is not None:
        return denied
    try:
        result = status.tenant_status(alerts.get_alert_board_data(), tenant)
    except Exception as exc:
        logger.warning("Status page unavailable: %s", type(exc).__name__)
        return jsonify({"error": "Status unavailable, try again shortly"}), 503
    if result is None:
        # Before the first sync the board is empty: that's not "no sites".
        if not inventory.snapshot_initialized():
            return jsonify({"error": "Status unavailable, try again shortly"}), 503
        return jsonify({"error": "No sites for this tenant"}), 404
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response
