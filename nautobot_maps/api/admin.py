"""Administration (#297, #282, #309; on FastAPI since #436): API tokens, a test
notification and the data contract."""

import logging
import threading
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from nautobot_maps import auth, contract, db, notify, settings, statuslinks, tokens, wireless
from nautobot_maps.api import deps, frontend, models
from nautobot_maps.api.answers import admin as answers
from nautobot_maps.api.answers import common

logger = logging.getLogger(__name__)

router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES, tags=["Administration"])

NO_DATABASE = {"error": "Persistence DB not configured"}
NO_DATABASE_RESPONSE = {503: {"model": common.Error, "description": "No database"}}


@router.get("/api/contract", name="admin.api_contract", response_model=answers.Contract)
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


@router.get("/admin", name="admin.admin_page", include_in_schema=False)
def admin_page():
    """Administration (#530): API tokens, a test notification and the
    wireless controllers' check; drawn from the API, which keeps each
    action to the admin role."""
    return frontend.shell()


@router.get(
    "/api/tenants",
    name="admin.api_tenants",
    response_model=answers.Tenants,
    responses=NO_DATABASE_RESPONSE,
    dependencies=[deps.require_role("admin")],
)
def api_tenants():
    """Nautobot's tenants as of the last sync (#530), with their
    descriptions, by name: what a status token can be made for."""
    conn = db.get_conn()
    if conn is None:
        return deps.Json(NO_DATABASE, status_code=503)
    try:
        rows = conn.execute("SELECT name, description FROM nautobot_tenant_cache ORDER BY lower(name), name").fetchall()
    finally:
        conn.close()
    return deps.Json({"tenants": [{"name": row["name"], "description": row["description"] or ""} for row in rows]})


@router.get(
    "/api/tokens",
    name="admin.api_tokens_list",
    response_model=answers.Tokens,
    responses=NO_DATABASE_RESPONSE,
    dependencies=[deps.require_role("admin")],
)
def api_tokens_list(query: Annotated[models.TokenList, Query()]):
    """API tokens (#297): name, first characters, role, expiry, last use, state; never the secret.

    Only the active ones unless ``all=true`` (#528); ``inactive`` counts the
    revoked and expired ones either way.  ``tenant``: only that tenant's
    status tokens.
    """
    conn = db.get_conn()
    if conn is None:
        return deps.Json(NO_DATABASE, status_code=503)
    try:
        rows = tokens.list_tokens(conn, tenant=query.tenant)
    finally:
        conn.close()
    active = [token for token in rows if token["state"] == "active"]
    return deps.Json({"tokens": rows if models.flag(query.all) else active, "inactive": len(rows) - len(active)})


def _status_link(conn, tenant: str) -> str | None:
    """The page to send a status token with (#500).  None when it can't be
    read: the token is made already, and its secret must still be shown."""
    try:
        return f"/status/{statuslinks.ensure(conn, [tenant])[tenant]}"
    except Exception as exc:
        logger.warning("Status token made, but its status link could not be read: %s", type(exc).__name__)
        return None


@router.post(
    "/api/tokens",
    name="admin.api_tokens_create",
    status_code=201,
    response_model=answers.TokenCreated,
    responses=NO_DATABASE_RESPONSE,
    dependencies=[deps.require_role("admin")],
    openapi_extra=deps.body_schema(models.TokenCreate),
)
def api_tokens_create(body: Annotated[object, Depends(deps.json_body)]):
    """Create an API token (#297) for ``Authorization: Bearer <token>``.

    JSON body: ``name``, ``role`` (viewer, operator or admin) and optional
    ``expires_in_days``.  The answer's ``token`` is the secret: shown this
    once, store it now.  A status token (``role`` status, ``tenant``) also
    answers ``status_link``, its tenant's page (#500).
    """
    conn = db.get_conn()
    if conn is None:
        return deps.Json(NO_DATABASE, status_code=503)
    if not isinstance(body, dict):
        conn.close()
        return deps.Json({"error": "Expected a JSON object"}, status_code=400)
    try:
        token = tokens.create(conn, body, auth.get_current_user().get("username") or "")
        if token.get("tenant"):
            token["status_link"] = _status_link(conn, token["tenant"])
    except tokens.TokenError as exc:
        return deps.Json({"error": exc.message}, status_code=400)
    finally:
        conn.close()
    # no-transform: never compressed, the secret beside the name the client chose (#349, BREACH).
    return deps.Json({"token": token}, status_code=201, headers={"Cache-Control": "no-store, no-transform"})


@router.post(
    "/api/tokens/{token_id:int}/revoke",
    name="admin.api_tokens_revoke",
    response_model=answers.TokenRevoked,
    responses={**NO_DATABASE_RESPONSE, 404: {"model": common.Error, "description": "No such token"}},
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
    "/api/tokens/purge",
    name="admin.api_tokens_purge",
    response_model=answers.TokensPurged,
    responses={**NO_DATABASE_RESPONSE, 400: {"model": common.Error, "description": "Bad older_than_days"}},
    dependencies=[deps.require_role("admin")],
    openapi_extra=deps.body_schema(models.TokenPurge),
)
def api_tokens_purge(body: Annotated[object, Depends(deps.json_body)]):
    """Delete revoked and expired API tokens for good (#528), never an active one.

    JSON body, all optional: ``older_than_days`` (only those revoked or
    expired longer ago) and ``dry_run`` (list them, delete nothing).  Their
    names are free again; cases and maintenance windows keep their
    ``token:<name>``.
    """
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return deps.Json({"error": "Expected a JSON object"}, status_code=400)
    dry_run = body.get("dry_run", False)
    if not isinstance(dry_run, bool):
        return deps.Json({"error": "dry_run must be true or false"}, status_code=400)
    conn = db.get_conn()
    if conn is None:
        return deps.Json(NO_DATABASE, status_code=503)
    try:
        purged = tokens.purge(
            conn,
            auth.get_current_user().get("username") or "",
            older_than_days=body.get("older_than_days"),
            dry_run=dry_run,
        )
    except tokens.TokenError as exc:
        return deps.Json({"error": exc.message}, status_code=400)
    finally:
        conn.close()
    return deps.Json({"tokens": purged, "purged": 0 if dry_run else len(purged), "dry_run": dry_run})


def _known_tenant(conn, tenant: str) -> str | None:
    """*tenant* as Nautobot spells it, or as given when only its link is left
    (a removed tenant, #500); None when neither: the CLI's check (#528)."""
    if not tenant.strip() or not statuslinks.known(conn, tenant):
        return None
    return statuslinks.nautobot_name(conn, tenant) or tenant.strip()


NO_TENANT_RESPONSE = {**NO_DATABASE_RESPONSE, 404: {"model": common.Error, "description": "No such tenant"}}


@router.get(
    "/api/status-links/{tenant:path}",
    name="admin.api_status_link",
    response_model=answers.StatusLink,
    responses=NO_TENANT_RESPONSE,
    dependencies=[deps.require_role("admin")],
)
def api_status_link(tenant: str):
    """A tenant's status page link (#529), as ``status-link TENANT`` prints it:
    ``/status/<id>``, made the first time it is asked for.  404 for a tenant
    Nautobot doesn't have (as of the last sync)."""
    conn = db.get_conn()
    if conn is None:
        return deps.Json(NO_DATABASE, status_code=503)
    try:
        name = _known_tenant(conn, tenant)
        if name is None:
            return deps.Json({"error": statuslinks.unknown_tenant(tenant)}, status_code=404)
        with db.transaction(conn):
            link_id = statuslinks.ensure(conn, [name])[name]
    finally:
        conn.close()
    return deps.Json({"tenant": name, "status_link": f"/status/{link_id}"})


@router.post(
    "/api/status-links/{tenant:path}/replace",
    name="admin.api_status_link_replace",
    response_model=answers.StatusLinkReplaced,
    responses=NO_TENANT_RESPONSE,
    dependencies=[deps.require_role("admin")],
)
def api_status_link_replace(tenant: str):
    """Replace a tenant's status page link that leaked (#529), as
    ``status-link TENANT --new``: the old link stops working at once; the
    tenant's status tokens keep working with the new one."""
    conn = db.get_conn()
    if conn is None:
        return deps.Json(NO_DATABASE, status_code=503)
    try:
        name = _known_tenant(conn, tenant)
        if name is None:
            return deps.Json({"error": statuslinks.unknown_tenant(tenant)}, status_code=404)
        with db.transaction(conn):
            link_id = statuslinks.replace(conn, name)
    finally:
        conn.close()
    logger.info("Status link of tenant %r replaced by %s", name, auth.get_current_user().get("username") or "unknown")
    return deps.Json(
        {
            "tenant": name,
            "status_link": f"/status/{link_id}",
            "message": "The old link no longer works. The tenant's status tokens still work: send them with this link.",
        }
    )


# One check at a time (#529): in this process, and across workers through
# the database, so the controllers are never asked by several at once.
_wireless_check_running = threading.Lock()
WIRELESS_CHECK_LOCK = "wireless_check"


@router.post(
    "/api/wireless/check",
    name="admin.api_wireless_check",
    response_model=answers.WirelessCheck,
    responses={429: {"model": common.Error, "description": "A check is running already"}},
    dependencies=[deps.require_role("admin")],
)
def api_wireless_check():
    """Read every wireless controller now (#529), as ``wireless-check``: per
    instance whether it answers, takes the account, how many access points it
    lists and how many match a Nautobot device.  Never the credentials.  One
    check at a time: 429 while one runs."""
    if not _wireless_check_running.acquire(blocking=False):
        return deps.Json({"error": "A wireless check is running already; try again in a minute"}, status_code=429)
    try:
        release = db.try_advisory_lock(WIRELESS_CHECK_LOCK)
        if release is False:
            return deps.Json({"error": "A wireless check is running already; try again in a minute"}, status_code=429)
        try:
            results = wireless.check()
        finally:
            if callable(release):
                release()
    finally:
        _wireless_check_running.release()
    duplicates = next((result for result in results if "duplicates" in result), None)
    return deps.Json(
        {
            "controllers": [result for result in results if "duplicates" not in result],
            "duplicates": duplicates["duplicates"] if duplicates else {},
            "duplicate_count": duplicates["duplicate_count"] if duplicates else 0,
        }
    )


@router.post(
    "/api/notifications/test",
    name="admin.api_notifications_test",
    response_model=answers.NotificationResults,
    responses={
        400: {"model": common.Error, "description": "No channel configured"},
        502: {"model": answers.NotificationResults, "description": "A channel failed"},
    },
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
