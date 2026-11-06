# The api/ service (#577). Build from api/: docker build -t api api/
FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

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

COPY alembic.ini docker-entrypoint.sh ./
COPY alembic ./alembic
COPY app ./app

RUN groupadd --gid 10001 app && useradd --create-home --uid 10001 --gid 10001 app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/healthz' % os.environ.get('PORT', '8000'), timeout=4)"

ENTRYPOINT ["./docker-entrypoint.sh"]
