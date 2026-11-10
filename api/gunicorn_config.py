"""gunicorn for the image (#613, the current app's gunicorn_config.py):
``gunicorn --config python:gunicorn_config app.api:app`` from api/.

GUNICORN_BIND (default 0.0.0.0:5000), GUNICORN_WORKERS (4) and GUNICORN_TIMEOUT
(120 s, never less) as before. Each worker starts its own scheduler thread in the
app's lifespan; a database lock lets one of them work at a time (#154).
"""

import os

from app.core.legacy_settings import settings
from app.logs import config as logs


def _env_int(name: str, default: int) -> int:
    # docker-compose passes unset variables as ""; that means the default.
    return int(os.getenv(name, "").strip() or default)


bind = os.getenv("GUNICORN_BIND", "").strip() or "0.0.0.0:5000"
workers = _env_int("GUNICORN_WORKERS", 4)
worker_class = "app.server.Worker"
timeout = max(120, _env_int("GUNICORN_TIMEOUT", 120))

accesslog = None
loglevel = settings.LOG_LEVEL.lower()
logconfig_dict = logs.gunicorn_logconfig()


def when_ready(server) -> None:  # type: ignore[no-untyped-def]
    from app import startup

    startup.log_alert_board_exclusions()
