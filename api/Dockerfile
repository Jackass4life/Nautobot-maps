# The API on its own (#672): FastAPI with gunicorn, migrating the database at start.
# Built from this folder:
#
#   docker build -t nautobot-maps-api .
#   docker build --build-context certs=../certs --build-context docs=.. -t nautobot-maps-api .
#
# Both contexts are optional:
#   certs  company CA certificates (*.crt, *.pem) that outgoing HTTPS (Nautobot, LibreNMS,
#          the wireless controllers, SMTP) trusts, besides the usual public ones
#   docs   the repository's root: docs/, README.md and CONTRIBUTING.md, which the help
#          pages show; without it the help pages say they aren't in this image
# The pages are the web image's (web/Dockerfile).
FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

# The optional contexts: empty unless --build-context gives them.
FROM scratch AS certs
FROM scratch AS docs

# What the optional contexts hold that the image uses.
FROM python:3.13-slim AS extras
COPY --from=certs . /in/certs/
COPY --from=docs . /in/docs/
RUN mkdir -p /out/ca /out/repo/docs \
    && find /in/certs -type f \( -name '*.crt' -o -name '*.pem' \) | while read -r file; do \
         cp "$file" "/out/ca/$(basename "${file%.*}").crt"; \
       done \
    && if [ -d /in/docs/docs ]; then cp -r /in/docs/docs/. /out/repo/docs/; fi \
    && for file in README.md CONTRIBUTING.md; do \
         if [ -f "/in/docs/$file" ]; then cp "/in/docs/$file" /out/repo/; fi; \
       done

FROM python:3.13-slim

# Don't write .pyc files at run time (the app user can't write to /app anyway), send
# logs straight to `docker logs`, and keep uv's environment where PATH finds it.
# SSL_CERT_FILE: the system's CA bundle, with the certs context's (httpx otherwise
# uses its own bundle, without them).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    PATH=/opt/venv/bin:$PATH

# Build-time only (ARG, not ENV): no terminal, so debconf mustn't look for one.
ARG DEBIAN_FRONTEND=noninteractive

# Debian security fixes released after the base image was built (#246); the
# Trivy scan in CI fails on fixed HIGH/CRITICAL ones.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# The certs context's CA certificates, in the system's bundle.
COPY --from=extras /out/ca/ /usr/local/share/ca-certificates/company/
RUN update-ca-certificates >/dev/null

# uv installs everything; the base image's pip is never used, and its vendored
# packages (urllib3, msgpack, pkg_resources) only show up in the Trivy scan (#613).
RUN python -m pip uninstall -y pip

COPY --from=uv /uv /usr/local/bin/uv

# The repository's layout: the app in /app/api, the help pages' files beside it.
WORKDIR /app/api

# Dependencies first (cached while only the code changes); uv.lock pins every
# package with its hash (#186).
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python3

# Run as an unprivileged user. The code stays root-owned (read-only for the app);
# all state lives in PostgreSQL. Group 10001 too, explicitly: the files in ./config
# are given to it (#517).
RUN groupadd --gid 10001 app && useradd --create-home --uid 10001 --gid 10001 app

COPY alembic.ini docker-entrypoint.sh gunicorn_config.py ./
COPY alembic ./alembic
COPY app ./app
COPY --from=extras /out/repo/ /app/

USER app

EXPOSE 5000

# Liveness probe. The slim image has no curl, so use Python's urllib; any non-2xx
# answer (e.g. 503 when the database is down) marks the container unhealthy. Set
# HEALTHCHECK_PORT if the app listens on another port (GUNICORN_BIND).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('HEALTHCHECK_PORT', '5000'), timeout=4)"

# Migrate the database once (#201, adopting the current app's, #590), then gunicorn.
ENTRYPOINT ["./docker-entrypoint.sh"]
