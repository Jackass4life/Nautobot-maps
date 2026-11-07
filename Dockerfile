# The image (#613): api/ (FastAPI, uv) with web/'s pages (Nuxt, static files) and
# the docs the help pages read (#433). Node is needed only to build the pages.
FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

FROM node:22-slim AS pages
# Not "a new npm is available" in every build: npm comes with the Node image.
ENV NPM_CONFIG_UPDATE_NOTIFIER=false
WORKDIR /web
COPY web/package.json web/package-lock.json ./
# The lock file pins every package with its hash; no install scripts run.
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY web/ ./
RUN npm run generate

FROM python:3.13-slim

# Don't write .pyc files at run time (the app user can't write to /app anyway), send
# logs straight to `docker logs`, and keep uv's environment where PATH finds it.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Build-time only (ARG, not ENV): no terminal, so debconf mustn't look for one.
ARG DEBIAN_FRONTEND=noninteractive

# Debian security fixes released after the base image was built (#246); the
# Trivy scan in CI fails on fixed HIGH/CRITICAL ones.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# uv installs everything; the base image's pip is never used, and its vendored
# packages (urllib3, msgpack, pkg_resources) only show up in the Trivy scan (#613).
RUN python -m pip uninstall -y pip

COPY --from=uv /uv /usr/local/bin/uv

# The repository's layout: api/ next to docs/ and README.md (the help pages).
WORKDIR /app/api

# Dependencies first (cached while only the code changes); uv.lock pins every
# package with its hash (#186).
COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python3

# Run as an unprivileged user. The code stays root-owned (read-only for the app);
# all state lives in PostgreSQL. Group 10001 too, explicitly: the files in ./config
# are given to it (#517).
RUN groupadd --gid 10001 app && useradd --create-home --uid 10001 --gid 10001 app

COPY api/alembic.ini api/docker-entrypoint.sh api/gunicorn_config.py ./
COPY api/alembic ./alembic
COPY api/app ./app
COPY README.md CONTRIBUTING.md /app/
COPY docs /app/docs
COPY --from=pages /web/.output/public /app/web
ENV WEB_BUILD_DIR=/app/web

USER app

EXPOSE 5000

# Liveness probe. The slim image has no curl, so use Python's urllib; any non-2xx
# answer (e.g. 503 when the database is down) marks the container unhealthy. Set
# HEALTHCHECK_PORT if the app listens on another port (GUNICORN_BIND).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('HEALTHCHECK_PORT', '5000'), timeout=4)"

# Migrate the database once (#201, adopting the current app's, #590), then gunicorn.
ENTRYPOINT ["./docker-entrypoint.sh"]
