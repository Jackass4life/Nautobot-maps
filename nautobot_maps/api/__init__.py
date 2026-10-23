"""The app's routes (#436), one router per area; asgi.py installs them."""

from nautobot_maps.api import (
    admin,
    availability,
    board,
    circuits,
    deps,
    frontend,
    ingest,
    locations,
    maintenance,
    ops,
    status,
    views,
)

ROUTERS = (
    views.router,
    frontend.router,
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


def install(app) -> None:
    deps.install_error_handlers(app)
    app.openapi = lambda: deps.openapi(app)
    for router in ROUTERS:
        app.include_router(router)
