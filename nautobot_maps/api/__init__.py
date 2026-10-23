"""The routes on FastAPI (#436), one router per area as they move from Flask
(nautobot_maps/routes/).  asgi.py installs them in front of the Flask app."""

from nautobot_maps.api import deps, ops, status

ROUTERS = (ops.router, status.router)


def install(app) -> None:
    deps.install_error_handlers(app)
    for router in ROUTERS:
        app.include_router(router)
