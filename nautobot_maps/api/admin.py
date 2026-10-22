"""Administration (#297, #282, #309; on FastAPI since #436): API tokens, a test
notification and the data contract."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends

from nautobot_maps import auth, contract, db, notify, settings, tokens
from nautobot_maps.api import deps, models

logger = logging.getLogger(__name__)

router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES, tags=["Administration"])

NO_DATABASE = {"error": "Persistence DB not configured"}


@router.get("/api/contract", name="admin.api_contract")
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
    return deps.Json({"contract": contract.as_json(), "checks": checks})


@router.get("/api/tokens", name="admin.api_tokens_list", dependencies=[deps.require_role("admin")])
def api_tokens_list():
    """API tokens (#297): name, first characters, role, expiry, last use, state; never the secret."""
    conn = db.get_conn()
    if conn is None:
        return deps.Json(NO_DATABASE, status_code=503)
    try:
        return deps.Json({"tokens": tokens.list_tokens(conn)})
    finally:
        conn.close()


@router.post(
    "/api/tokens",
    name="admin.api_tokens_create",
    dependencies=[deps.require_role("admin")],
    openapi_extra=deps.body_schema(models.TokenCreate),
)
def api_tokens_create(body: Annotated[object, Depends(deps.json_body)]):
    """Create an API token (#297) for ``Authorization: Bearer <token>``.

    JSON body: ``name``, ``role`` (viewer, operator or admin) and optional
    ``expires_in_days``.  The answer's ``token`` is the secret: shown this
    once, store it now.
    """
    conn = db.get_conn()
    if conn is None:
        return deps.Json(NO_DATABASE, status_code=503)
    if not isinstance(body, dict):
        conn.close()
        return deps.Json({"error": "Expected a JSON object"}, status_code=400)
    try:
        token = tokens.create(conn, body, auth.get_current_user().get("username") or "")
    except tokens.TokenError as exc:
        return deps.Json({"error": exc.message}, status_code=400)
    finally:
        conn.close()
    # no-transform: never compressed, the secret beside the name the client chose (#349, BREACH).
    return deps.Json({"token": token}, status_code=201, headers={"Cache-Control": "no-store, no-transform"})


@router.post(
    "/api/tokens/{token_id:int}/revoke",
    name="admin.api_tokens_revoke",
    dependencies=[deps.require_role("admin")],
)
def api_tokens_revoke(token_id: int):
    """Revoke an API token (#297): refused from the next request on."""
    conn = db.get_conn()
    if conn is None:
        return deps.Json(NO_DATABASE, status_code=503)
    try:
        token = tokens.revoke(conn, token_id, auth.get_current_user().get("username") or "")
    finally:
        conn.close()
    if token is None:
        return deps.Json({"error": "No such token"}, status_code=404)
    return deps.Json({"token": token})


@router.post(
    "/api/notifications/test",
    name="admin.api_notifications_test",
    dependencies=[deps.require_role("admin")],
)
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
        return deps.Json({"error": error}, status_code=400)
    results = notify.send_test()
    status = 200 if all(value == "ok" for value in results.values()) else 502
    return deps.Json({"results": results}, status_code=status)
