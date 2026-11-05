#!/bin/sh
# Apply migrations, then run the server. MIGRATE_ON_START=false skips them
# (e.g. when a separate job runs `alembic upgrade head`).
set -e
if [ "${MIGRATE_ON_START:-true}" != "false" ]; then
    alembic upgrade head
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" "$@"
