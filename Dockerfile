# The pages built with Nuxt (#463): static files, so Node is needed only here,
# not in the image that runs.
FROM node:22-slim AS frontend
# Not "a new npm is available" in every build: npm comes with the Node image.
ENV NPM_CONFIG_UPDATE_NOTIFIER=false
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
# The lock file pins every package with its hash; no install scripts run.
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY frontend/ ./
RUN npm run generate

FROM python:3.12-slim

# Don't write .pyc files (the app user can't write to /app anyway) and send
# logs straight to `docker logs` without buffering.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Debian security fixes released after the base image was built (#246); the
# Trivy scan in CI fails on fixed HIGH/CRITICAL ones.
# Noninteractive: no terminal here, so debconf mustn't look for one.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt is the compiled lock file: every package, transitive ones
# included, pinned with hashes (#186).  Installed as root into the image's own
# Python on purpose (the app runs as `app` below); pip's own version comes with
# the base image, which Dependabot updates.
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes --root-user-action=ignore --disable-pip-version-check \
    -r requirements.txt

# Run as an unprivileged user.  The code stays root-owned (read-only for the
# app); all state lives in PostgreSQL.
RUN useradd --create-home --uid 10001 app

COPY . .
COPY --from=frontend /frontend/.output/public /app/frontend/.output/public

USER app

EXPOSE 5000

# Liveness probe.  The slim image has no curl, so use Python's urllib; any
# non-2xx answer (e.g. 503 when the database is down) marks the container
# unhealthy.  Set HEALTHCHECK_PORT if the app listens on another port.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('HEALTHCHECK_PORT', '5000'), timeout=4)"

# Migrate the database once (#201), then start gunicorn in its place.
CMD ["sh", "-c", "python -m nautobot_maps migrate && exec gunicorn --config python:gunicorn_config asgi:app"]
