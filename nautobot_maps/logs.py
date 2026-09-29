"""Logging setup (#193): LOG_LEVEL, optional JSON lines, and gunicorn's logs.

``configure()`` sets up the app's loggers (called by app.py);
``gunicorn_logconfig()`` gives gunicorn's error and access logs the same
format (used by gunicorn_config.py).
"""

import json
import logging
import sys
from datetime import UTC, datetime

from nautobot_maps import settings

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for Loki / ELK / Splunk."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def make_formatter() -> logging.Formatter:
    return JsonFormatter() if settings.LOG_FORMAT == "json" else logging.Formatter(TEXT_FORMAT)


class SkipSuccessfulHealthChecks(logging.Filter):
    """Drop access-log lines for ``GET /healthz`` answered 200: Docker probes
    every 30 s, which would otherwise be most of the access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        atoms = record.args if isinstance(record.args, dict) else {}
        return not (atoms.get("U") == "/healthz" and str(atoms.get("s")) == "200")


def configure() -> None:
    """Send the app's logs to stderr at LOG_LEVEL in LOG_FORMAT."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(make_formatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.LOG_LEVEL)


def gunicorn_logconfig() -> dict:
    """``logconfig_dict`` for gunicorn: its error and access logs in the same format."""
    formatter = {"()": JsonFormatter} if settings.LOG_FORMAT == "json" else {"format": TEXT_FORMAT}
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"app": formatter},
        "filters": {"skip_health_checks": {"()": SkipSuccessfulHealthChecks}},
        "handlers": {
            "error_console": {"class": "logging.StreamHandler", "formatter": "app", "stream": "ext://sys.stderr"},
            "access_console": {
                "class": "logging.StreamHandler",
                "formatter": "app",
                "filters": ["skip_health_checks"],
                "stream": "ext://sys.stdout",
            },
        },
        # gunicorn merges this into its defaults, whose root logger names a
        # "console" handler that this config replaces.
        "root": {"level": settings.LOG_LEVEL, "handlers": ["error_console"]},
        "loggers": {
            "gunicorn.error": {"level": settings.LOG_LEVEL, "handlers": ["error_console"], "propagate": False},
            "gunicorn.access": {"level": "INFO", "handlers": ["access_console"], "propagate": False},
        },
    }
