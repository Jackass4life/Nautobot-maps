"""The app as ASGI (#436): FastAPI on the outside, the Flask app mounted
behind it until every area has moved.  gunicorn serves ``asgi:app`` with
uvicorn workers (gunicorn_config.py); ``python app.py`` serves it for
development.  Routes FastAPI doesn't have yet fall through to Flask, which
keeps its own sign-in check, headers and error pages for them.
"""

from a2wsgi import WSGIMiddleware
from fastapi import FastAPI

import app as flask_app
from nautobot_maps import access_log

# The API docs come at the end of the move (#436): /docs is still Flask's explorer.
api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
# Everything FastAPI doesn't serve itself: one thread per request, as before.
api.mount("/", WSGIMiddleware(flask_app.app, workers=16))

app = access_log.AccessLog(api)
