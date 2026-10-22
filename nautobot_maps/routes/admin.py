"""Administration (#424): API tokens, a test notification and the data contract."""

import logging

from flask import Blueprint, jsonify, request

from nautobot_maps import (
    auth,
    contract,
    db,
    notify,
    settings,
    tokens,
)

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__)


@bp.route("/api/contract")
def api_contract():
    """The data contract with Nautobot and LibreNMS (#309) and the last check.

    ``contract``: per upstream endpoint, the fields read, their types and what
    each becomes.  ``checks``: per endpoint, when the last sync checked it,
    how many records, and the fields that didn't match.
    """
    conn = db.get_conn()
    checks = []
    if conn is not None:
        try:
            checks = contract.latest_checks(conn)
        finally:
            conn.close()
    return jsonify({"contract": contract.as_json(), "checks": checks})


@bp.route("/api/tokens", methods=["GET"])
@auth.require_role("admin")
def api_tokens_list():
    """API tokens (#297): name, first characters, role, expiry, last use, state; never the secret."""
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        return jsonify({"tokens": tokens.list_tokens(conn)})
    finally:
        conn.close()


@bp.route("/api/tokens", methods=["POST"])
@auth.require_role("admin")
def api_tokens_create():
    """Create an API token (#297) for ``Authorization: Bearer <token>``.

    JSON body: ``name``, ``role`` (viewer, operator or admin) and optional
    ``expires_in_days``.  The answer's ``token`` is the secret: shown this
    once, store it now.
    """
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        conn.close()
        return jsonify({"error": "Expected a JSON object"}), 400
    try:
        token = tokens.create(conn, body, auth.get_current_user().get("username") or "")
    except tokens.TokenError as exc:
        return jsonify({"error": exc.message}), 400
    finally:
        conn.close()
    response = jsonify({"token": token})
    # no-transform: never compressed, the secret beside the name the client chose (#349, BREACH).
    response.headers["Cache-Control"] = "no-store, no-transform"
    return response, 201


@bp.route("/api/tokens/<int:token_id>/revoke", methods=["POST"])
@auth.require_role("admin")
def api_tokens_revoke(token_id: int):
    """Revoke an API token (#297): refused from the next request on."""
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        token = tokens.revoke(conn, token_id, auth.get_current_user().get("username") or "")
    finally:
        conn.close()
    if token is None:
        return jsonify({"error": "No such token"}), 404
    return jsonify({"token": token})


@bp.route("/api/notifications/test", methods=["POST"])
@auth.require_role("admin")
def api_notifications_test():
    """Send a test message on every configured notification channel now (#282).

    Returns ``{"results": {"webhook": "ok", "email": "error: ..."}}``; errors
    never include a URL or password.  400 when no channel is configured.
    """
    channels = notify.configured_channels()
    if not channels:
        if settings.NOTIFY_EMAIL_TO and not settings.SMTP_HOST:
            error = "NOTIFY_EMAIL_TO is set, but SMTP_HOST is not: email needs a mail server"
        else:
            error = (
                "No notification channel configured: set NOTIFY_WEBHOOK_URL, NOTIFY_TEAMS_WEBHOOK_URL, "
                "or NOTIFY_EMAIL_TO together with SMTP_HOST"
            )
        return jsonify({"error": error}), 400
    results = notify.send_test()
    status = 200 if all(value == "ok" for value in results.values()) else 502
    return jsonify({"results": results}), status
