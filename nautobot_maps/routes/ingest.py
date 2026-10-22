"""Alerts pushed in (#424): LibreNMS, Alertmanager and the generic webhook."""

import logging

from flask import Blueprint, jsonify, request

from nautobot_maps import (
    auth,
    caching,
    db,
    inventory,
    scheduler,
    settings,
    webhooks,
)

logger = logging.getLogger(__name__)

bp = Blueprint("ingest", __name__)


@bp.route("/api/librenms/alert", methods=["POST"])
@auth.require_role("operator")
def api_librenms_alert():
    """A LibreNMS alert push (#284): refresh one device's status now.

    For LibreNMS's API alert transport, with an operator API token.  The
    device is ``device_id`` (LibreNMS id) or ``hostname``, in a JSON body,
    a form or the query string.  Its status is read from the LibreNMS API
    (the push only says which device), and the board is rebuilt within
    seconds.  A device LibreNMS doesn't know is ``{"updated": false}``, not
    an error, so LibreNMS doesn't keep retrying.
    """
    if not (settings.LIBRENMS_URL or "").strip() or not (settings.LIBRENMS_API_TOKEN or "").strip():
        return jsonify({"error": "LibreNMS is not configured (LIBRENMS_URL, LIBRENMS_API_TOKEN)"}), 409
    if not db.dialect():
        return jsonify({"error": "Persistence DB not configured"}), 503
    body = request.get_json(silent=True)
    values = body if isinstance(body, dict) else {}
    device = ""
    for name in ("device_id", "hostname"):
        value = values.get(name)
        if value is None:
            value = request.form.get(name) or request.args.get(name)
        device = str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""
        if device:
            break
    if not device or len(device) > 255:
        return jsonify({"error": "device_id or hostname is required"}), 400
    try:
        result = inventory.librenms_push_refresh(device)
    except Exception as exc:
        # Only the exception type: the message can hold the LibreNMS URL.
        logger.warning("LibreNMS push for %r: asking LibreNMS failed: %s", device, type(exc).__name__)
        return jsonify({"error": "Could not ask LibreNMS for the device"}), 502
    if result["updated"]:
        caching.invalidate_alert_board()
        scheduler.rebuild_after_push()
    return jsonify(result)


def _webhook_alerts(parse):
    """Store the alerts *parse* reads from the JSON body (#292); the board is rebuilt."""
    conn = db.get_conn()
    if conn is None:
        return jsonify({"error": "Persistence DB not configured"}), 503
    try:
        body = request.get_json(silent=True)
        if body is None:
            return jsonify({"error": "Expected a JSON body"}), 400
        try:
            events, skipped = parse(body)
        except webhooks.WebhookError as exc:
            return jsonify({"error": exc.message}), 400
        webhooks.store(conn, events)
    finally:
        conn.close()
    if events:
        caching.invalidate_alert_board()
        # History and notifications now, not at the next tick.
        scheduler.rebuild_after_push(always=True)
    firing = sum(1 for event in events if event["status"] == "down")
    return jsonify(
        {
            "firing": firing,
            "resolved": len(events) - firing,
            "skipped": skipped,
            # Firing alerts that name a Nautobot device: 0 hints at the wrong label.
            "matched": webhooks.matched(events),
        }
    )


@bp.route("/api/alertmanager", methods=["POST"])
@auth.require_role("operator")
def api_alertmanager():
    """Prometheus Alertmanager webhook (#292): firing alerts mark devices down.

    The device is the first of ``ALERTMANAGER_DEVICE_LABELS`` (default
    ``instance,hostname,host,device``) an alert has, matched to a Nautobot
    device by name, then primary IP.  Resolved alerts end it.  Use an
    operator API token (``http_config.authorization``).
    """
    return _webhook_alerts(webhooks.parse_alertmanager)


@bp.route("/api/device-status", methods=["POST"])
@auth.require_role("operator")
def api_device_status():
    """Generic webhook (#292): ``{"device", "status": "down"|"up", "source"?,
    "id"?, "summary"?, "started_at"?}``, or a list of them.

    ``device`` is a hostname or IP, matched to a Nautobot device by name,
    then primary IP.  A ``down`` lasts until the same source and ``id``
    (default: the device) sends ``up``, or WEBHOOK_ALERT_TTL_HOURS without
    a repeat.
    """
    return _webhook_alerts(lambda body: (webhooks.parse_generic(body), 0))
