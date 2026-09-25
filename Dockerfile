FROM python:3.12-slim

# Don't write .pyc files (the app user can't write to /app anyway) and send
# logs straight to `docker logs` without buffering.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Run as an unprivileged user.  The code stays root-owned (read-only for the
# app); all state lives in PostgreSQL.
RUN useradd --create-home --uid 10001 app

COPY . .

USER app

EXPOSE 5000

# Liveness probe.  The slim image has no curl, so use Python's urllib; any
# non-2xx answer (e.g. 503 when the database is down) marks the container
# unhealthy.  Set HEALTHCHECK_PORT if the app listens on another port.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('HEALTHCHECK_PORT', '5000'), timeout=4)"

CMD ["gunicorn", "--config", "python:gunicorn_config", "app:app"]
