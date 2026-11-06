"""API tokens (#297, #592), admin only: the current app's /api/tokens routes."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.services import status_link_service, token_service
from app.session import DbSession
from app.utils.auth import CurrentUserDep, require_role
from app.utils.body import body_schema, json_body
from app.utils.errors import ApiError, responses
from app.v1.tokens.dtos import (
    TokenCreatedDto,
    TokenCreateDto,
    TokenPurgeDto,
    TokenRevokedDto,
    TokensDto,
    TokensPurgedDto,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tokens", tags=["tokens"], dependencies=[Depends(require_role("admin"))])
REFUSALS = responses(401, 403)
Body = Annotated[object, Depends(json_body)]


def _flag(value: str | None) -> bool:
    """A yes/no query value: 1, true or yes."""
    return (value or "").strip().lower() in ("1", "true", "yes")


@router.get("", operation_id="listTokens", response_model=TokensDto, responses=REFUSALS)
def list_tokens(
    db: DbSession,
    all: Annotated[str | None, Query(description="Revoked and expired ones too (#528).")] = None,
    tenant: Annotated[str | None, Query(description="Only this tenant's status tokens.")] = None,
) -> TokensDto:
    """API tokens: name, first characters, role, expiry, last use, state; never the
    secret. Only the active ones unless ``all=true``; ``inactive`` counts the others."""
    rows = token_service.list_tokens(db, tenant=tenant)
    active = [token for token in rows if token["state"] == "active"]
    return TokensDto.model_validate(
        {"tokens": rows if _flag(all) else active, "inactive": len(rows) - len(active)}
    )


def _status_link(db: DbSession, tenant: str) -> str | None:
    """The page to send a status token with (#500). None when it can't be read: the
    token is made already, and its secret must still be shown."""
    try:
        return f"/status/{status_link_service.ensure(db, [tenant])[tenant]}"
    except Exception as exc:  # noqa: BLE001 - the token stands; say why in the log
        logger.warning(
            "Status token made, but its status link could not be read: %s", type(exc).__name__
        )
        return None


@router.post(
    "",
    operation_id="createToken",
    status_code=201,
    response_model=TokenCreatedDto,
    responses={**REFUSALS, **responses(400)},
    openapi_extra=body_schema(TokenCreateDto),
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
    created = TokenCreatedDto.model_validate({"token": token})
    # no-transform: never compressed, the secret beside the name the client chose (#349).
    return JSONResponse(
        created.model_dump(mode="json"),
        status_code=201,
        headers={"Cache-Control": "no-store, no-transform"},
    )


@router.post(
    "/{token_id}/revoke",
    operation_id="revokeToken",
    response_model=TokenRevokedDto,
    responses={**REFUSALS, **responses(404)},
)
def revoke_token(token_id: int, db: DbSession, user: CurrentUserDep) -> TokenRevokedDto:
    """Revoke an API token: refused from the next request on."""
    token = token_service.revoke(db, token_id, user.username)
    if token is None:
        raise ApiError({"error": "No such token"}, 404)
    return TokenRevokedDto.model_validate({"token": token})


@router.post(
    "/purge",
    operation_id="purgeTokens",
    response_model=TokensPurgedDto,
    responses={**REFUSALS, **responses(400)},
    openapi_extra=body_schema(TokenPurgeDto, required=False),
)
def purge_tokens(body: Body, db: DbSession, user: CurrentUserDep) -> TokensPurgedDto:
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
    return TokensPurgedDto.model_validate(
        {"tokens": purged, "purged": 0 if dry_run else len(purged), "dry_run": dry_run}
    )
