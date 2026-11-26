"""Who the API sees: lets web/ show the user and hide what they may not do; and the
app's own sign-in with AD (#717; the example's v1/auth/controller.py): /auth (the
page), /api/v1/auth/login, /verify and /logout."""

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.auth.dependencies import CurrentUserDep, require_permission
from app.auth.permissions import Permission
from app.core.exceptions import ApiError, responses
from app.core.settings import settings
from app.db import aconnection
from app.frontend import controller as frontend
from app.services import ad_service, auth_service, identity_service, login_service
from app.utils.body import body_schema
from app.v1 import deps, sections
from app.v1.auth.dtos import AccessTokenResponseDto, Jwt, MeDto, SignedOutDto, SignInDto

router = APIRouter(prefix="/me", tags=sections.tag("auth"))


@router.get(
    "",
    dependencies=[Depends(require_permission(Permission.SESSION_BASIC))],
    operation_id="getCurrentUser",
    response_model=MeDto,
)
def get_current_user(user: CurrentUserDep) -> MeDto:
    return MeDto(
        username=user.username or None,
        # Not signed in, no role (role_from_groups gives the default role to anyone).
        role=(user.role or None) if user.is_authenticated else None,
        groups=user.groups,
        auth_mode=settings.AUTH_MODE,
        # Writes as the board's own changes are (open with AUTH_MODE=disabled).
        access=auth_service.access(user, settings),
        can_write=auth_service.may_change_the_board(user, settings),
        can_administer=auth_service.may_administer(user, settings),
    )


# --- The app's own sign-in with AD (#717) ---------------------------------------------

sign_in_router = APIRouter(prefix="/auth", tags=sections.tag("auth"))
page_router = APIRouter(
    route_class=deps.AppRoute,
    dependencies=deps.ROUTER_DEPENDENCIES,
    tags=sections.tag("auth"),
)
WRONG = "Wrong user name or password"


def _off() -> ApiError:
    return ApiError(
        {"error": "Sign-in with AD is off", "detail": "Set AD_HOST (and AUTH_MODE=header)."},
        404,
    )


@page_router.get("/auth", dependencies=[deps.PUBLIC], name="auth.sign_in_page")
def sign_in_page():
    """The sign-in page (#717): user name and password, with AD sign-in on."""
    if not auth_service.app_sign_in_on(settings):
        raise _off()
    return frontend.shell()


async def _credentials(request: Request) -> tuple[str, str]:
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    try:
        if kind in ("application/x-www-form-urlencoded", "multipart/form-data"):
            form = await request.form()
            sent = {"username": form.get("username"), "password": form.get("password")}
        else:
            sent = await request.json()
        found = SignInDto.model_validate(sent)
    except ValueError:
        raise ApiError({"error": "Expected username and password"}, 400) from None
    return found.username, found.password


def _address(request: Request) -> str:
    return request.client.host if request.client else ""


FIRST_SIGN_IN_ROLE = "viewer"


async def _first_sign_in(found: ad_service.AdUser) -> identity_service.Identity | None:
    """An AD user without an identity gets one with the viewer role (#782, the
    example's _create_basic_reader_user); an admin gives more. None when it can't be
    made (the viewer role deleted, say)."""
    try:
        async with aconnection.connect() as conn, conn.transaction():
            await identity_service.create(
                conn,
                found.unique_name,
                roles=[FIRST_SIGN_IN_ROLE],
                common_name=found.common_name,
                description="Created at the first AD sign-in.",
            )
    except identity_service.IdentityError as exc:
        # Made at the same moment by another sign-in, or no viewer role.
        logger.warning("AD sign-in {!r}: no identity made: {}", found.unique_name, exc.message)
    identity_service.forget()
    return await identity_service.lookup(found.unique_name)


@sign_in_router.post(
    "/login",
    dependencies=[deps.PUBLIC],
    operation_id="signIn",
    response_model=AccessTokenResponseDto,
    responses=responses(400, 401, 403, 404, 429, 503),
    openapi_extra=body_schema(SignInDto),
)
async def sign_in(request: Request) -> JSONResponse:
    """Sign in with an AD user name and password (#717). An AD user gets a token, for
    ``Authorization: Bearer``, also set as an HttpOnly cookie for the pages; the first
    time also an identity with the viewer role (#782). An unknown user and a wrong
    password get the same answer; failed sign-ins wait longer after 5."""
    if not auth_service.app_sign_in_on(settings):
        raise _off()
    username, password = await _credentials(request)
    address = _address(request)
    wait = login_service.wait_seconds(username, address)
    if wait:
        raise ApiError(
            {"error": "Too many failed sign-ins: wait a little"},
            429,
            headers={"Retry-After": str(wait)},
        )
    try:
        found = await asyncio.to_thread(ad_service.authenticate, username, password, settings)
    except ad_service.DirectoryUnavailable:
        raise ApiError(
            {"error": "The directory can't be reached: try again shortly"}, 503
        ) from None
    if found is None:
        login_service.failed(username, address)
        logger.info("AD sign-in refused for {!r} from {}", username.strip()[:100], address)
        raise ApiError({"error": WRONG}, 401)
    login_service.succeeded(username, address)
    identity_service.forget()
    identity = await identity_service.lookup(found.unique_name)
    if identity is None:
        identity = await _first_sign_in(found)
    if identity is None:
        raise ApiError(
            {
                "error": "No access",
                "detail": f"{found.unique_name!r} has no identity here: ask an admin to add you.",
            },
            403,
        )
    token, _ = login_service.issue(identity, settings)
    logger.info("AD sign-in: {!r}", identity.unique_name)
    answer = AccessTokenResponseDto(
        access_token=token, token_type="Bearer", expires_in=settings.JWT_LIFETIME_SECONDS
    )
    response = JSONResponse(answer.model_dump(), headers={"Cache-Control": "no-store"})
    response.set_cookie(
        login_service.COOKIE,
        token,
        max_age=settings.JWT_LIFETIME_SECONDS,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response


@sign_in_router.get(
    "/verify",
    dependencies=[Depends(require_permission(Permission.SESSION_BASIC))],
    operation_id="verifySignIn",
    response_model=Jwt,
    responses=responses(401, 403),
)
def verify_sign_in(request: Request, user: CurrentUserDep) -> Jwt:
    """The caller's sign-in token's claims (the example's /auth/verify): 401 without
    a valid one."""
    claims = (
        login_service.verify(auth_service.app_token(request), settings)
        if user.signed_in_with == "app"
        else None
    )
    if claims is None:
        raise ApiError({"error": "Not signed in with the app's own sign-in"}, 401)
    return Jwt.model_validate({key: claims.get(key) for key in Jwt.model_fields})


@sign_in_router.post(
    "/logout",
    dependencies=[deps.PUBLIC],
    operation_id="signOut",
    response_model=SignedOutDto,
)
def sign_out() -> JSONResponse:
    """Sign out (#717): the cookie goes. A token kept elsewhere stays valid until it
    expires (JWT_LIFETIME_SECONDS)."""
    response = JSONResponse({"signed_out": True}, headers={"Cache-Control": "no-store"})
    response.delete_cookie(
        login_service.COOKIE, path="/", secure=True, httponly=True, samesite="strict"
    )
    return response
