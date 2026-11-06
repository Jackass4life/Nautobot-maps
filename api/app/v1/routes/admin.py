"""Ported from nautobot_maps/api/admin.py (#604), its API under /api/v1. Administration (#297, #282, #309; on FastAPI since #436): API tokens, a test
notification and the data contract."""

import logging
import threading

from fastapi import APIRouter

from app import db
from app.frontend import controller as frontend
from app.services import legacy_auth as auth
from app.services import notify_service as notify
from app.services import status_link_service as statuslinks
from app.services import wireless_service as wireless
from app.utils.legacy_settings import settings
from app.v1 import deps
from app.v1.answers import admin as answers
from app.v1.answers import common

logger = logging.getLogger(__name__)

router = APIRouter(
    route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES, tags=["Administration"]
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
            "Status token made, but its status link could not be read: %s", type(exc).__name__
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
    "/api/v1/status-links/{tenant:path}/replace",
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
    logger.info(
        "Status link of tenant %r replaced by %s",
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
