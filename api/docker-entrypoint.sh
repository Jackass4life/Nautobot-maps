#!/bin/sh
# Apply migrations, then run the server. MIGRATE_ON_START=false skips them
# (e.g. when a separate job runs `alembic upgrade head`).
set -e
if [ "${MIGRATE_ON_START:-true}" != "false" ]; then
    # Adopts a database the current app migrated (#590), then upgrades it.
    python -m app.migrate
fi
# --no-proxy-headers: X-Forwarded-For must not replace the peer address, or
# the AUTH_TRUSTED_PROXIES check (#582) would see the end user, not the proxy.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --no-proxy-headers "$@"
