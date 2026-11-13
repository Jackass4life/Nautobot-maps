"""The Admin page (#297, #282, #309, #530; #624): the page, tenants, status page
links, the wireless check and a test notification (ported from
nautobot_maps/api/admin.py, #604, with their full /api/v1 paths); and the new
stack's own routers, under /api/v1 in app/v1/__init__.py, each with its own
settings: API tokens (#297, #592, admin only) and the data contract (#309, #598)."""

import threading
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import permissions
from app.auth.dependencies import CurrentUserDep, require_permission
from app.auth.permissions import Permission
from app.core.exceptions import ApiError, responses
from app.core.legacy_settings import settings
from app.core.settings import get_settings
from app.db import aconnection, session
from app.db.session import AsyncDbSession
from app.frontend import controller as frontend
from app.services import auth_service, contract_service, status_link_service, token_service
from app.services import inventory_service as inventory
from app.services import notify_service as notify
from app.services import wireless_service as wireless
from app.services.auth_service import CurrentUser
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


@router.get(
    "/admin",
    dependencies=[deps.require_permission(permissions.Permission.SESSION_BASIC)],
    name="admin.admin_page",
    include_in_schema=False,
)
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
    dependencies=[
        deps.require_permission(
            permissions.Permission.TENANT_STATUS_VIEW, permissions.Permission.STATUS_LINKS_VIEW
        )
    ],
)
async def api_tenants():
    """Nautobot's tenants as of the last sync (#530), with their
    descriptions, by name: what a status token can be made for, and the
    Customers page's list (#697: for those who see a tenant's status or links)."""
    async with aconnection.connect() as conn:
        rows = (
            await conn.execute(
                "SELECT name, description FROM nautobot_tenant_cache ORDER BY lower(name), name"
            )
        ).fetchall()
    return deps.Json(
        {
            "tenants": [
                {"name": row["name"], "description": row["description"] or ""} for row in rows
            ]
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
    dependencies=[deps.require_permission(permissions.Permission.ADMIN)],
)
async def api_wireless_check():
    """Read every wireless controller now (#529), as ``wireless-check``: per
    instance whether it answers, takes the account, how many access points it
    lists and how many match a Nautobot device.  Never the credentials.  One
    check at a time: 429 while one runs."""
    if not _wireless_check_running.acquire(blocking=False):
        return deps.Json(
            {"error": "A wireless check is running already; try again in a minute"}, status_code=429
        )
    try:
        release = await aconnection.try_advisory_lock(WIRELESS_CHECK_LOCK)
        if release is False:
            return deps.Json(
                {"error": "A wireless check is running already; try again in a minute"},
                status_code=429,
            )
        try:
            # The controllers asked in a thread: the check is sync HTTP.
            results = await session.run_sync(wireless.check, await inventory.read_devices())
        finally:
            if callable(release):
                await release()
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
    dependencies=[deps.require_permission(permissions.Permission.ADMIN)],
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

tokens_router = APIRouter(prefix="/tokens", tags=sections.tag("administration"))
# Admins keep every token; NWO (status.links.manage) only the status tokens, the keys
# they send customers with a status link (#687).
ADMIN_OR_STATUS_TOKENS = Depends(
    require_permission(Permission.ADMIN, Permission.STATUS_LINKS_MANAGE)
)


def _all_tokens(request: Request, user: CurrentUser) -> bool:
    """Whether the caller may use every token (an admin), not only status tokens."""
    return (
        auth_service.permission_problem(user, get_settings(), (Permission.ADMIN,), request.method)
        is None
    )


def _status_tokens_only() -> ApiError:
    return ApiError(
        {
            "error": "Insufficient permissions",
            "required_permission": str(Permission.ADMIN),
            "required_role": "admin",
            "detail": "Without admin, only status tokens (role status) can be made, listed and revoked.",
        },
        403,
    )


REFUSALS = responses(401, 403)
Body = Annotated[object, Depends(json_body)]


def _flag(value: str | None) -> bool:
    """A yes/no query value: 1, true or yes."""
    return (value or "").strip().lower() in ("1", "true", "yes")


@tokens_router.get(
    "",
    operation_id="listTokens",
    response_model=Tokens,
    responses=REFUSALS,
    dependencies=[ADMIN_OR_STATUS_TOKENS],
)
async def list_tokens(
    request: Request,
    user: CurrentUserDep,
    db: AsyncDbSession,
    all: Annotated[str | None, Query(description="Revoked and expired ones too (#528).")] = None,
    tenant: Annotated[str | None, Query(description="Only this tenant's status tokens.")] = None,
) -> Tokens:
    """API tokens: name, first characters, role, expiry, last use, state; never the
    secret. Only the active ones unless ``all=true``; ``inactive`` counts the others."""
    rows = await token_service.list_tokens(db, tenant=tenant)
    if not _all_tokens(request, user):
        rows = [token for token in rows if token["role"] == token_service.STATUS_ROLE]
    active = [token for token in rows if token["state"] == "active"]
    return Tokens.model_validate(
        {"tokens": rows if _flag(all) else active, "inactive": len(rows) - len(active)}
    )


async def _status_link(db: AsyncSession, tenant: str) -> str | None:
    """The page to send a status token with (#500). None when it can't be read: the
    token is made already, and its secret must still be shown."""
    try:
        # A savepoint: a database error here mustn't abort the token's own insert.
        async with db.begin_nested():
            link_id = (await status_link_service.ensure(db, [tenant]))[tenant]
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
    dependencies=[ADMIN_OR_STATUS_TOKENS],
)
async def create_token(
    request: Request, body: Body, db: AsyncDbSession, user: CurrentUserDep
) -> JSONResponse:
    """Create an API token for ``Authorization: Bearer <token>``: ``name``, ``role``
    (viewer, operator, admin, or status with a ``tenant``), optional
    ``expires_in_days``. The answer's ``token`` is the secret, shown this once. A
    status token also answers ``status_link``, its tenant's page."""
    if not isinstance(body, dict):
        raise ApiError({"error": "Expected a JSON object"}, 400)
    if body.get("role") != token_service.STATUS_ROLE and not _all_tokens(request, user):
        raise _status_tokens_only()
    try:
        token = await token_service.create(db, body, user.username)
    except token_service.TokenError as exc:
        raise ApiError({"error": exc.message}, 400) from None
    if token.get("tenant"):
        token["status_link"] = await _status_link(db, token["tenant"])
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
    dependencies=[ADMIN_OR_STATUS_TOKENS],
)
async def revoke_token(
    request: Request, token_id: int, db: AsyncDbSession, user: CurrentUserDep
) -> TokenRevoked:
    """Revoke an API token: refused from the next request on."""
    if not _all_tokens(request, user):
        found = await token_service.get(db, token_id)
        if found is None:
            raise ApiError({"error": "No such token"}, 404)
        if found["role"] != token_service.STATUS_ROLE:
            raise _status_tokens_only()
    token = await token_service.revoke(db, token_id, user.username)
    if token is None:
        raise ApiError({"error": "No such token"}, 404)
    return TokenRevoked.model_validate({"token": token})


@tokens_router.post(
    "/purge",
    operation_id="purgeTokens",
    response_model=TokensPurged,
    responses={**REFUSALS, **responses(400)},
    openapi_extra=body_schema(TokenPurge, required=False),
    dependencies=[Depends(require_permission(Permission.ADMIN))],
)
async def purge_tokens(body: Body, db: AsyncDbSession, user: CurrentUserDep) -> TokensPurged:
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
        purged = await token_service.purge(
            db, user.username, older_than_days=body.get("older_than_days"), dry_run=dry_run
        )
    except token_service.TokenError as exc:
        raise ApiError({"error": exc.message}, 400) from None
    return TokensPurged.model_validate(
        {"tokens": purged, "purged": 0 if dry_run else len(purged), "dry_run": dry_run}
    )


# --- The data contract (#309, #598): /api/v1/contract --------------------------

contract_router = APIRouter(prefix="/contract", tags=sections.tag("administration"))


@contract_router.get(
    "",
    dependencies=[deps.require_permission(permissions.Permission.SESSION_BASIC)],
    operation_id="getContract",
    response_model=ContractDto,
)
async def get_contract() -> ContractDto:
    """The data contract and the last check: per upstream endpoint, the fields read,
    their types and what each becomes; per endpoint, when the last sync checked it,
    how many records, and the fields that didn't match."""
    async with aconnection.connect() as conn:
        checks = await contract_service.latest_checks(conn)
    return ContractDto.model_validate({"contract": contract_service.as_json(), "checks": checks})
