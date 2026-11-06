"""The routes ported from the current app (nautobot_maps/api/, #604), one router per area,
their API under /api/v1. ``install()`` adds them to the app."""

from app.v1.routes import (
    admin,
    availability,
    board,
    circuits,
    ingest,
    locations,
    maintenance,
    ops,
    status,
    views,
)

ROUTERS = (
    views.router,
    ops.router,
    status.router,
    availability.router,
    circuits.router,
    maintenance.router,
    locations.router,
    board.router,
    ingest.router,
    admin.router,
)


def install(app) -> None:  # type: ignore[no-untyped-def]
    for router in ROUTERS:
        app.include_router(router)
