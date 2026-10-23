"""The MCP server (#250, #424).  The other monitoring routes are on FastAPI
(nautobot_maps/api/ops.py, #436); this one follows in a later phase."""

import logging

from flask import Blueprint, jsonify

from nautobot_maps import mcp, settings

logger = logging.getLogger(__name__)

bp = Blueprint("ops", __name__)


@bp.route("/mcp", methods=["POST"])
def mcp_endpoint():
    """MCP server for AI assistants (#250): the alert board as tools.

    Off unless MCP_ENABLED=true.  Streamable HTTP, stateless, protocol
    2026-07-28 and the older initialize-based versions.  Tools run the
    matching API routes with the caller's sign-in and roles.
    """
    if not settings.MCP_ENABLED:
        return jsonify({"error": "Not found"}), 404
    return mcp.handle()
