# The api/ service (#577) with web/'s built pages (#594). From the repository root:
#   docker build --build-context web=web -t api api/
# (compose passes web/ as the named context "web").
FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

# web/ as static files (`npm run generate`), served by the API from the same origin.
FROM node:22-slim AS pages
WORKDIR /src
COPY --from=web package.json package-lock.json ./
# Install scripts don't run; `nuxt prepare` (in generate) makes the generated types.
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY --from=web . .
RUN npm run generate

FROM python:3.13-slim

COPY --from=uv /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# Dependencies first (cached while only the code changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python3

COPY alembic.ini docker-entrypoint.sh gunicorn_config.py ./
COPY alembic ./alembic
COPY app ./app
COPY --from=pages /src/.output/public ./web
ENV WEB_BUILD_DIR=/app/web \
    GUNICORN_BIND=0.0.0.0:8000

RUN groupadd --gid 10001 app && useradd --create-home --uid 10001 --gid 10001 app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8000'), timeout=4)"

ENTRYPOINT ["./docker-entrypoint.sh"]
