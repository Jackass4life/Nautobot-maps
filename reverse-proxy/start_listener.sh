#!/bin/sh
# Starts listener.py (#768), which prints every request it gets.  Settings from
# .env.dev here (git-ignored), all optional:
#   LISTENER_PORT   the port (8443)
#   SSL_CERT_PATH / SSL_KEY_PATH   a certificate and key: HTTPS, else plain HTTP
# Runs with api/'s Python environment (FastAPI, uvicorn): `uv sync` in api/ first.
set -eu
cd "$(dirname "$0")"
if [ -f .env.dev ]; then
    set -a
    . ./.env.dev
    set +a
fi
set -- --host 0.0.0.0 --port "${LISTENER_PORT:-8443}"
if [ -n "${SSL_CERT_PATH:-}" ] && [ -n "${SSL_KEY_PATH:-}" ]; then
    set -- "$@" --ssl-keyfile="$SSL_KEY_PATH" --ssl-certfile="$SSL_CERT_PATH"
fi
exec uv run --project ../api uvicorn listener:app "$@"
