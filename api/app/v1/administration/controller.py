"""The Admin page (#297, #282, #309, #530; #624): the page, tenants, status page
links, the wireless check and a test notification (ported from
nautobot_maps/api/admin.py, #604, with their full /api/v1 paths); and the new
stack's own routers, under /api/v1 in app/v1/__init__.py, each with its own
settings: API tokens (#297, #592, admin only) and the data contract (#309, #598)."""

import threading
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from loguru import logger

from app.auth.dependencies import CurrentUserDep, require_role
from app.core.exceptions import ApiError, responses
from app.core.legacy_settings import settings
from app.db import connection as db
from app.db.session import DbSession
from app.frontend import controller as frontend
from app.services import contract_service, status_link_service, token_service
from app.services import legacy_auth as auth
from app.services import notify_service as notify
from app.services import status_link_service as statuslinks
from app.services import wireless_service as wireless
from app.utils.body import body_schema, json_body
from app.v1 import deps, sections
from app.v1.administration import dtos
from app.v1.administration.dtos import (
    ContractDto,
    TokenCreate,
    TokenCreated,
    TokenPurge,
    TokenRevoked,
    Tokens,
    TokensPurged,
)
from app.v1.common import dtos as common

router = APIRouter(
    route_class=deps.AppRoute,
    dependencies=deps.ROUTER_DEPENDENCIES,
    tags=sections.tag("administration"),
)

NO_DATABASE = {"error": "Persistence DB not configured"}
NO_DATABASE_RESPONSE = {503: {"model": common.Error, "description": "No database"}}


@router.get("/admin", name="admin.admin_page", include_in_schema=False)
def admin_page():
    """Administration (#530): API tokens, a test notification and the
    wireless controllers' check; drawn from the API, which keeps each
    action to the admin role."""
    return frontend.shell()


@router.get(
    "/api/v1/tenants",
    name="admin.api_tenants",
    response_model=dtos.Tenants,
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
        rows = conn.execute(
            "SELECT name, description FROM nautobot_tenant_cache ORDER BY lower(name), name"
        ).fetchall()
    finally:
        conn.close()
    return deps.Json(
        {
            "tenants": [
                {"name": row["name"], "description": row["description"] or ""} for row in rows
            ]
        }
    )


def _status_link(conn, tenant: str) -> str | None:
    """The page to send a status token with (#500).  None when it can't be
    read: the token is made already, and its secret must still be shown."""
    try:
        return f"/status/{statuslinks.ensure(conn, [tenant])[tenant]}"
    except Exception as exc:
        logger.warning(
            "Status token made, but its status link could not be read: {}", type(exc).__name__
        )
        return None


def _known_tenant(conn, tenant: str) -> str | None:
    """*tenant* as Nautobot spells it, or as given when only its link is left
    (a removed tenant, #500); None when neither: the CLI's check (#528)."""
    if not tenant.strip() or not statuslinks.known(conn, tenant):
        return None
    return statuslinks.nautobot_name(conn, tenant) or tenant.strip()


NO_TENANT_RESPONSE = {
    **NO_DATABASE_RESPONSE,
    404: {"model": common.Error, "description": "No such tenant"},
}


@router.get(
    "/api/v1/status-links/{tenant:path}",
    name="admin.api_status_link",
    response_model=dtos.StatusLink,
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
    "/api/v1/status-links/{tenant:path}/replace",
    name="admin.api_status_link_replace",
    response_model=dtos.StatusLinkReplaced,
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
    logger.info(
        "Status link of tenant {!r} replaced by {}",
        name,
        auth.get_current_user().get("username") or "unknown",
    )
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
    "/api/v1/wireless/check",
    name="admin.api_wireless_check",
    response_model=dtos.WirelessCheck,
    responses={429: {"model": common.Error, "description": "A check is running already"}},
    dependencies=[deps.require_role("admin")],
)
def api_wireless_check():
    """Read every wireless controller now (#529), as ``wireless-check``: per
    instance whether it answers, takes the account, how many access points it
    lists and how many match a Nautobot device.  Never the credentials.  One
    check at a time: 429 while one runs."""
    if not _wireless_check_running.acquire(blocking=False):
        return deps.Json(
            {"error": "A wireless check is running already; try again in a minute"}, status_code=429
        )
    try:
        release = db.try_advisory_lock(WIRELESS_CHECK_LOCK)
        if release is False:
            return deps.Json(
                {"error": "A wireless check is running already; try again in a minute"},
                status_code=429,
            )
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
    "/api/v1/notifications/test",
    name="admin.api_notifications_test",
    response_model=dtos.NotificationResults,
    responses={
        400: {"model": common.Error, "description": "No channel configured"},
        502: {"model": dtos.NotificationResults, "description": "A channel failed"},
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


# --- API tokens (#297, #592): admin only, /api/v1/tokens ---------------------

tokens_router = APIRouter(
    prefix="/tokens",
    tags=sections.tag("administration"),
    dependencies=[Depends(require_role("admin"))],
)
REFUSALS = responses(401, 403)
Body = Annotated[object, Depends(json_body)]


def _flag(value: str | None) -> bool:
    """A yes/no query value: 1, true or yes."""
    return (value or "").strip().lower() in ("1", "true", "yes")


@tokens_router.get("", operation_id="listTokens", response_model=Tokens, responses=REFUSALS)
def list_tokens(
    db: DbSession,
    all: Annotated[str | None, Query(description="Revoked and expired ones too (#528).")] = None,
    tenant: Annotated[str | None, Query(description="Only this tenant's status tokens.")] = None,
) -> Tokens:
    """API tokens: name, first characters, role, expiry, last use, state; never the
    secret. Only the active ones unless ``all=true``; ``inactive`` counts the others."""
    rows = token_service.list_tokens(db, tenant=tenant)
    active = [token for token in rows if token["state"] == "active"]
    return Tokens.model_validate(
        {"tokens": rows if _flag(all) else active, "inactive": len(rows) - len(active)}
    )


def _status_link(db: DbSession, tenant: str) -> str | None:
    """The page to send a status token with (#500). None when it can't be read: the
    token is made already, and its secret must still be shown."""
    try:
        # A savepoint: a database error here mustn't abort the token's own insert.
        with db.begin_nested():
            link_id = status_link_service.ensure(db, [tenant])[tenant]
        return f"/status/{link_id}"
    except Exception as exc:
        logger.warning(
            "Status token made, but its status link could not be read: {}", type(exc).__name__
        )
        return None


@tokens_router.post(
    "",
    operation_id="createToken",
    status_code=201,
    response_model=TokenCreated,
    responses={**REFUSALS, **responses(400)},
    openapi_extra=body_schema(TokenCreate),
)
def create_token(body: Body, db: DbSession, user: CurrentUserDep) -> JSONResponse:
    """Create an API token for ``Authorization: Bearer <token>``: ``name``, ``role``
    (viewer, operator, admin, or status with a ``tenant``), optional
    ``expires_in_days``. The answer's ``token`` is the secret, shown this once. A
    status token also answers ``status_link``, its tenant's page."""
    if not isinstance(body, dict):
        raise ApiError({"error": "Expected a JSON object"}, 400)
    try:
        token = token_service.create(db, body, user.username)
    except token_service.TokenError as exc:
        raise ApiError({"error": exc.message}, 400) from None
    if token.get("tenant"):
        token["status_link"] = _status_link(db, token["tenant"])
    created = TokenCreated.model_validate({"token": token})
    # no-transform: never compressed, the secret beside the name the client chose (#349).
    return JSONResponse(
        created.model_dump(mode="json"),
        status_code=201,
        headers={"Cache-Control": "no-store, no-transform"},
    )


@tokens_router.post(
    "/{token_id}/revoke",
    operation_id="revokeToken",
    response_model=TokenRevoked,
    responses={**REFUSALS, **responses(404)},
)
def revoke_token(token_id: int, db: DbSession, user: CurrentUserDep) -> TokenRevoked:
    """Revoke an API token: refused from the next request on."""
    token = token_service.revoke(db, token_id, user.username)
    if token is None:
        raise ApiError({"error": "No such token"}, 404)
    return TokenRevoked.model_validate({"token": token})


@tokens_router.post(
    "/purge",
    operation_id="purgeTokens",
    response_model=TokensPurged,
    responses={**REFUSALS, **responses(400)},
    openapi_extra=body_schema(TokenPurge, required=False),
)
def purge_tokens(body: Body, db: DbSession, user: CurrentUserDep) -> TokensPurged:
    """Delete revoked and expired API tokens for good (#528), never an active one.
    Optional ``older_than_days`` and ``dry_run``. Their names are free again."""
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ApiError({"error": "Expected a JSON object"}, 400)
    dry_run = body.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise ApiError({"error": "dry_run must be true or false"}, 400)
    try:
        purged = token_service.purge(
            db, user.username, older_than_days=body.get("older_than_days"), dry_run=dry_run
        )
    except token_service.TokenError as exc:
        raise ApiError({"error": exc.message}, 400) from None
    return TokensPurged.model_validate(
        {"tokens": purged, "purged": 0 if dry_run else len(purged), "dry_run": dry_run}
    )


# --- The data contract (#309, #598): /api/v1/contract --------------------------

contract_router = APIRouter(prefix="/contract", tags=sections.tag("administration"))


@contract_router.get("", operation_id="getContract", response_model=ContractDto)
def get_contract(db: DbSession) -> ContractDto:
    """The data contract and the last check: per upstream endpoint, the fields read,
    their types and what each becomes; per endpoint, when the last sync checked it,
    how many records, and the fields that didn't match."""
    checks = contract_service.latest_checks(db.connection())
    return ContractDto.model_validate({"contract": contract_service.as_json(), "checks": checks})
