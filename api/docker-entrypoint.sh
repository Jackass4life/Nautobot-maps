#!/bin/sh
# Apply migrations, then run the server. MIGRATE_ON_START=false skips them
# (e.g. when a separate job runs `python -m app migrate`).
set -e
if [ "${MIGRATE_ON_START:-true}" != "false" ]; then
    # Adopts a database the current app migrated (#590), then upgrades it.
    python -m app migrate
fi
# gunicorn with uvicorn workers (#613): GUNICORN_BIND (0.0.0.0:5000),
# GUNICORN_WORKERS, GUNICORN_TIMEOUT. No proxy headers (app/server.py): the
# AUTH_TRUSTED_PROXIES check (#582) must see the proxy, not the end user.
exec gunicorn --config python:gunicorn_config app.api:app "$@"
