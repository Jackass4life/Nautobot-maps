"""Which part of the app a log line comes from (#633), so one part's lines can be
found: ``[sync]`` in the text format, ``"part": "sync"`` in JSON.

A line's part follows from its logger's name (the module, for the app's own): the
longest prefix in ``PARTS`` wins. The access log names its part per request:
``api`` for the API, ``web`` for the pages.
"""

PARTS: dict[str, str] = {
    # The app's API routes and what answers them.
    "app": "api",
    "app.v1": "api",
    "app.services.health_service": "api",
    "app.services.mcp_service": "api",
    # Reads from Nautobot, LibreNMS and the wireless controllers.
    "app.clients": "sync",
    "app.services.inventory_service": "sync",
    "app.services.wireless_service": "sync",
    "app.services.neighbours_service": "sync",
    "app.services.contract_service": "sync",
    "httpx": "sync",
    "requests": "sync",
    "urllib3": "sync",
    # The alert board's rules and its rebuilds.
    "app.services.alerts_service": "board",
    # The background jobs.
    "app.services.scheduler": "scheduler",
    # Alerts pushed to the app: LibreNMS and webhooks.
    "app.v1.incoming_alerts": "alerts-in",
    "app.services.webhooks_service": "alerts-in",
    # Notifications sent out.
    "app.services.notify_service": "notify",
    # Sign-in and API tokens.
    "app.auth": "auth",
    "app.services.auth_service": "auth",
    "app.services.legacy_auth": "auth",
    "app.services.token_service": "auth",
    # Migrations and the database.
    "app.migrate": "db",
    "app.db": "db",
    "app.repositories": "db",
    "alembic": "db",
    "sqlalchemy": "db",
    # The server itself: start, settings, workers.
    "app.startup": "server",
    "app.core": "server",
    "app.logs": "server",
    "gunicorn": "server",
    "uvicorn": "server",
}

# Everything else (Python's own, other libraries).
DEFAULT = "server"

ACCESS_LOGGER = "app.access"
# Requests the API answers; every other path is a page or one of its files.
API_PATHS = ("/api/", "/mcp", "/metrics", "/healthz")

NAMES = ("api", "web", "sync", "board", "scheduler", "alerts-in", "notify", "auth", "db", "server")


def of(logger_name: str) -> str:
    """The part of the logger *logger_name* (``app.services.scheduler`` → ``scheduler``)."""
    name = logger_name
    while name:
        if name in PARTS:
            return PARTS[name]
        name = name.rpartition(".")[0]
    return DEFAULT


def of_request(path: str) -> str:
    """The access log's part for a request to *path*."""
    return "api" if path.startswith(API_PATHS) or path == "/api" else "web"
