"""The routes on FastAPI (#436), one router per area as they move from Flask
(nautobot_maps/routes/).  asgi.py installs them in front of the Flask app."""

from nautobot_maps.api import availability, board, circuits, deps, locations, maintenance, ops, status

ROUTERS = (
    ops.router,
    status.router,
    availability.router,
    circuits.router,
    maintenance.router,
    locations.router,
    board.router,
)


def install(app) -> None:
    deps.install_error_handlers(app)
    for router in ROUTERS:
        app.include_router(router)
