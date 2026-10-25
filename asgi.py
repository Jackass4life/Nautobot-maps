"""The app as ASGI (#436): FastAPI, every route in nautobot_maps/api/, the
access log around it.  gunicorn serves ``asgi:app`` with uvicorn workers
(gunicorn_config.py); ``python app.py`` serves it for development.
"""

from fastapi import FastAPI

import app as startup  # noqa: F401 - logging, the cache and the database, once
import nautobot_maps.api
from nautobot_maps import access_log
from nautobot_maps.api import deps

# /api/docs (Swagger UI) and /api/openapi.json are our own routes (#483), behind the sign-in check.
api = FastAPI(
    title="Nautobot Maps",
    version="1",
    description="The map, the alert board and everything around them, as an API.",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
nautobot_maps.api.install(api)


app = access_log.AccessLog(deps.HeadAsGet(api))
