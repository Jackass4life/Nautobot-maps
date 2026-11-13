"""What every page reads (#463, #419; #624): the page-config the pages draw
themselves from, and the data's freshness for the top bar."""

import asyncio
import time

from fastapi import APIRouter
from loguru import logger

from app.auth import permissions
from app.core.legacy_settings import settings
from app.db import aconnection
from app.db import connection as db
from app.services import alerts_service as alerts
from app.services import availability_service as availability
from app.services import legacy_auth as auth
from app.services import maintenance_service as maintenance
from app.services import metrics_service as metrics
from app.services import navigation_service as navigation
from app.services import notify_service as notify
from app.services import templating_service as templating
from app.services import wireless_service as wireless
from app.v1 import deps, sections
from app.v1.shared import dtos

router = APIRouter(
    route_class=deps.AppRoute,
    dependencies=deps.ROUTER_DEPENDENCIES,
    tags=sections.tag("shared"),
)


@router.get(
    "/api/v1/page-config",
    dependencies=[deps.require_permission(permissions.Permission.SESSION_BASIC)],
    name="pages.api_page_config",
    response_model=dtos.PageConfig,
)
def api_page_config():
    """What the pages built with Nuxt read to draw themselves (#463): who is
    signed in (the top bar), Nautobot's address (links to its pages), the
    menu, the same as the server-rendered pages show, the Maintenance page's
    and Availability's limits, the map's tiles and first view, and the
    board's case links and level definitions."""
    at = availability.now()
    view = settings.MAP_DEFAULT_VIEW
    access = auth.access()
    return deps.Json(
        {
            "user": templating.bar_user(),
            "access": access,
            "nautobot_url": settings.NAUTOBOT_URL or "",
            # The pages this caller may open (#693).
            "pages": navigation.pages(allowed=access["pages"]),
            "maintenance": {
                "recent_days": maintenance.PAGE_RECENT_DAYS,
                "history_days": maintenance.HISTORY_DEFAULT_DAYS,
                "max_sites": maintenance.MAX_SITES,
            },
            "availability": {
                "today": f"{at:%Y-%m-%d}",
                "oldest_day": availability.oldest_day(at),
                "basis": settings.AVAILABILITY_BASIS,
                "target": availability.target_pct(),
            },
            "map": {
                "tile_url": settings.MAP_TILE_URL,
                "tile_attribution": settings.MAP_TILE_ATTRIBUTION,
                "view": {"latitude": view[0], "longitude": view[1], "zoom": view[2]}
                if view
                else None,
            },
            "board": {
                "case_url_template": settings.CASE_URL_TEMPLATE or "",
                "levels": alerts.alert_level_definitions(),
            },
            "admin": {
                "notification_channels": notify.configured_channels(),
                "wireless_controllers": len(wireless.configured()),
            }
            if auth.may_administer()
            else None,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/api/v1/app-status",
    dependencies=[deps.require_permission(permissions.Permission.SESSION_BASIC)],
    name="ops.api_app_status",
    response_model=dtos.AppStatus,
)
async def api_app_status():
    """How fresh the app's data is, for the top bar on every page (#419).

    Read from the sync state the scheduler records: never calls Nautobot or
    LibreNMS, so every open tab polling once a minute adds nothing upstream.
    Response: ``state`` (``ok``, ``stale`` or ``down``), ``updated_at`` (the
    oldest source's last good sync) and ``sources`` (``name``, ``state``,
    ``message``, ``last_success``).
    """
    # No database configured: nothing synced, not an outage.
    rows = {} if not db.dialect() else None
    if db.dialect():
        try:
            # A bar polled by every open tab: an unreachable database is "down" within 2 s.
            async with asyncio.timeout(2), aconnection.connect() as conn:
                rows = await metrics.sync_rows(conn)
        except Exception as exc:
            logger.warning("App status: database unavailable: {}", str(exc) or type(exc).__name__)
    body = metrics.app_status(
        rows,
        librenms=bool(settings.LIBRENMS_URL and settings.LIBRENMS_API_TOKEN),
        intervals={
            "INVENTORY_SYNC_INTERVAL_SECONDS": settings.INVENTORY_SYNC_INTERVAL_SECONDS,
            "LIBRENMS_SYNC_INTERVAL_SECONDS": settings.LIBRENMS_SYNC_INTERVAL_SECONDS,
            "WIRELESS_SYNC_INTERVAL_SECONDS": settings.WIRELESS_SYNC_INTERVAL_SECONDS,
        },
        now=time.time(),
        wireless=tuple(
            (wireless.source(c), wireless.display_name(c)) for c in wireless.configured()
        ),
    )
    return deps.Json(body, headers={"Cache-Control": "no-store"})
