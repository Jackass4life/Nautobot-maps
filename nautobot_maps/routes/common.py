"""Helpers the route modules share (#424)."""

import logging
from urllib.parse import urlencode

from flask import jsonify, request

logger = logging.getLogger(__name__)


def nautobot_service_unavailable(context: str, exc: Exception):
    logger.warning("%s: %s", context, exc)
    return jsonify({"error": "Nautobot service unavailable"}), 503


def scope_query() -> str:
    """The page's ?tenant= / ?q= (#364, #367) as a query string, "" without
    them: kept on the links between the map, the board and the wall."""
    return urlencode(
        [("tenant", name) for name in request.args.getlist("tenant") if name.strip()]
        + [("q", text) for text in request.args.getlist("q") if text.strip()]
    )


def arg(name: str) -> str | None:
    """A query-string value, stripped; None when empty."""
    return (request.args.get(name) or "").strip() or None


def flag_arg(name: str) -> bool:
    """A yes/no query-string value: 1, true or yes."""
    return (arg(name) or "").lower() in {"1", "true", "yes"}
