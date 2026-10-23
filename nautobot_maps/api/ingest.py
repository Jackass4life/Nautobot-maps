"""Alerts pushed in (#284, #292; on FastAPI since #436): LibreNMS, Alertmanager
and the generic webhook."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from nautobot_maps import caching, db, inventory, scheduler, settings, webhooks
from nautobot_maps.api import deps, models
from nautobot_maps.api.answers import common
from nautobot_maps.api.answers import ingest as answers

logger = logging.getLogger(__name__)

router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES, tags=["Incoming alerts"])

Body = Annotated[object, Depends(deps.json_body)]
Form = Annotated[dict, Depends(deps.form_values)]
NO_DATABASE = {"error": "Persistence DB not configured"}
NO_DATABASE_RESPONSE = {503: {"model": common.Error, "description": "No database"}}


@router.post(
    "/api/librenms/alert",
    name="ingest.api_librenms_alert",
    response_model=answers.LibreNmsPush,
    responses={
        **NO_DATABASE_RESPONSE,
        409: {"model": common.Error, "description": "LibreNMS is not configured"},
        502: {"model": common.Error, "description": "LibreNMS couldn't be asked"},
    },
    dependencies=[deps.require_role("operator")],
    openapi_extra=deps.body_schema(models.LibreNmsPush),
)
def api_librenms_alert(request: Request, body: Body, form: Form):
    """A LibreNMS alert push (#284): refresh one device's status now.

    For LibreNMS's API alert transport, with an operator API token.  The
    device is ``device_id`` (LibreNMS id) or ``hostname``, in a JSON body,
    a form or the query string.  Its status is read from the LibreNMS API
    (the push only says which device), and the board is rebuilt within
    seconds.  A device LibreNMS doesn't know is ``{"updated": false}``, not
    an error, so LibreNMS doesn't keep retrying.
    """
    if not (settings.LIBRENMS_URL or "").strip() or not (settings.LIBRENMS_API_TOKEN or "").strip():
        return deps.Json({"error": "LibreNMS is not configured (LIBRENMS_URL, LIBRENMS_API_TOKEN)"}, status_code=409)
    if not db.dialect():
        return deps.Json(NO_DATABASE, status_code=503)
    values = body if isinstance(body, dict) else {}
    device = ""
    for name in ("device_id", "hostname"):
        value = values.get(name)
        if value is None:
            value = form.get(name) or request.query_params.get(name)
        device = str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""
        if device:
            break
    if not device or len(device) > 255:
        return deps.Json({"error": "device_id or hostname is required"}, status_code=400)
    try:
        result = inventory.librenms_push_refresh(device)
    except Exception as exc:
        # Only the exception type: the message can hold the LibreNMS URL.
        logger.warning("LibreNMS push for %r: asking LibreNMS failed: %s", device, type(exc).__name__)
        return deps.Json({"error": "Could not ask LibreNMS for the device"}, status_code=502)
    if result["updated"]:
        caching.invalidate_alert_board()
        scheduler.rebuild_after_push()
    return deps.Json(result)


def _webhook_alerts(body, parse):
    """Store the alerts *parse* reads from the JSON body (#292); the board is rebuilt."""
    conn = db.get_conn()
    if conn is None:
        return deps.Json(NO_DATABASE, status_code=503)
    try:
        if body is None:
            return deps.Json({"error": "Expected a JSON body"}, status_code=400)
        try:
            events, skipped = parse(body)
        except webhooks.WebhookError as exc:
            return deps.Json({"error": exc.message}, status_code=400)
        webhooks.store(conn, events)
    finally:
        conn.close()
    if events:
        caching.invalidate_alert_board()
        # History and notifications now, not at the next tick.
        scheduler.rebuild_after_push(always=True)
    firing = sum(1 for event in events if event["status"] == "down")
    return deps.Json(
        {
            "firing": firing,
            "resolved": len(events) - firing,
            "skipped": skipped,
            # Firing alerts that name a Nautobot device: 0 hints at the wrong label.
            "matched": webhooks.matched(events),
        }
    )


@router.post(
    "/api/alertmanager",
    name="ingest.api_alertmanager",
    response_model=answers.WebhookResult,
    responses=NO_DATABASE_RESPONSE,
    dependencies=[deps.require_role("operator")],
    openapi_extra=deps.body_schema(models.AlertmanagerWebhook),
)
def api_alertmanager(body: Body):
    """Prometheus Alertmanager webhook (#292): firing alerts mark devices down.

    The device is the first of ``ALERTMANAGER_DEVICE_LABELS`` (default
    ``instance,hostname,host,device``) an alert has, matched to a Nautobot
    device by name, then primary IP.  Resolved alerts end it.  Use an
    operator API token (``http_config.authorization``).
    """
    return _webhook_alerts(body, webhooks.parse_alertmanager)


@router.post(
    "/api/device-status",
    name="ingest.api_device_status",
    response_model=answers.WebhookResult,
    responses=NO_DATABASE_RESPONSE,
    dependencies=[deps.require_role("operator")],
    openapi_extra=deps.body_schema(models.DeviceStatus),
)
def api_device_status(body: Body):
    """Generic webhook (#292): ``{"device", "status": "down"|"up", "source"?,
    "id"?, "summary"?, "started_at"?}``, or a list of them.

    ``device`` is a hostname or IP, matched to a Nautobot device by name,
    then primary IP.  A ``down`` lasts until the same source and ``id``
    (default: the device) sends ``up``, or WEBHOOK_ALERT_TTL_HOURS without
    a repeat.
    """
    return _webhook_alerts(body, lambda value: (webhooks.parse_generic(value), 0))
