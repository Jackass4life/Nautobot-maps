"""Logging setup (#193, loguru since #633): LOG_LEVEL, text or JSON lines, each
naming its part (``app/logs/parts.py``).

``configure()`` sends every log line through loguru: the app's own (``from loguru
import logger``) and, through ``InterceptHandler``, Python's standard logging
(uvicorn, gunicorn, alembic, httpx, ...). The access log goes to stdout, the rest
to stderr, as before. ``gunicorn_logconfig()`` hands gunicorn's own loggers to the
same handler (api/gunicorn_config.py).
"""

import json
import logging
import sys
import traceback
from datetime import UTC

from loguru import logger

from app.core.legacy_settings import settings
from app.logs import parts

# loguru adds the stack trace, if any, on the lines below.
TEXT_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} {level} [{extra[part]}] {extra[logger]}: {message}"
# The access log's own level: its lines are kept at LOG_LEVEL=WARNING too, as before.
ACCESS_LEVEL = "INFO"

_configured = False


def name_and_part(record) -> None:  # type: ignore[no-untyped-def]
    """Every record's ``logger`` (the stdlib logger's name, else the module) and
    ``part`` (``parts.of`` unless the line names its own)."""
    extra = record["extra"]
    extra.setdefault("logger", record["name"])
    extra.setdefault("part", parts.of(extra["logger"]))


class InterceptHandler(logging.Handler):
    """Python's standard logging into loguru, keeping the logger's name."""

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "from_loguru", False):
            return  # the tests' way back to the standard logging (tests/conftest.py)
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # The frame that called logging, for loguru's own fields.
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.bind(logger=record.name).opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def is_access(record) -> bool:  # type: ignore[no-untyped-def]
    return record["extra"].get("logger") == parts.ACCESS_LOGGER


def keep_access_line(record) -> bool:  # type: ignore[no-untyped-def]
    """Access lines, but not ``GET /healthz`` answered 200: Docker probes every 30 s,
    which would otherwise be most of the access log."""
    if not is_access(record):
        return False
    extra = record["extra"]
    return not (extra.get("U") == "/healthz" and str(extra.get("s")) == "200")


def _not_access(record) -> bool:  # type: ignore[no-untyped-def]
    return not is_access(record)


def json_line(record) -> str:  # type: ignore[no-untyped-def]
    """One JSON object per line, for Loki / ELK / Splunk: the same fields as before
    (#193) and the line's ``part``."""
    entry = {
        "time": record["time"]
        .astimezone(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "level": record["level"].name,
        "logger": record["extra"]["logger"],
        "part": record["extra"]["part"],
        "message": record["message"],
    }
    if record["exception"] is not None:
        entry["exception"] = _exception_text(record)
    return json.dumps(entry, ensure_ascii=False)


def _exception_text(record) -> str:  # type: ignore[no-untyped-def]
    exception = record["exception"]
    return "".join(
        traceback.format_exception(exception.type, exception.value, exception.traceback)
    ).rstrip()


def _json_sink(stream):  # type: ignore[no-untyped-def]
    def write(message) -> None:  # type: ignore[no-untyped-def]
        stream.write(json_line(message.record) + "\n")
        stream.flush()

    return write


def configure() -> None:
    """Send every log line to stderr (the access log to stdout) at LOG_LEVEL in
    LOG_FORMAT, and Python's standard logging through loguru."""
    global _configured
    logger.remove()
    logger.configure(patcher=name_and_part)
    as_json = settings.LOG_FORMAT == "json"
    for stream, level, keep in (
        (sys.stderr, settings.LOG_LEVEL, _not_access),
        (sys.stdout, ACCESS_LEVEL, keep_access_line),
    ):
        if as_json:
            logger.add(_json_sink(stream), level=level, filter=keep, format="{message}")
        else:
            logger.add(stream, level=level, filter=keep, format=TEXT_FORMAT, colorize=False)
    logging.basicConfig(handlers=[InterceptHandler()], level=settings.LOG_LEVEL, force=True)
    # uvicorn's and gunicorn's own handlers would print a second, unformatted line.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "gunicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    _configured = True


def configured() -> bool:
    """Whether ``configure()`` ran in this process (alembic's env.py keeps it then)."""
    return _configured


def gunicorn_logconfig() -> dict:
    """``logconfig_dict`` for gunicorn: its error log through loguru, like every other line."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"loguru": {"()": InterceptHandler}},
        # gunicorn merges this into its defaults, whose root logger names a
        # "console" handler that this config replaces.
        "root": {"level": settings.LOG_LEVEL, "handlers": ["loguru"]},
        "loggers": {
            "gunicorn.error": {
                "level": settings.LOG_LEVEL,
                "handlers": ["loguru"],
                "propagate": False,
            },
            # Off (accesslog = None): the app writes its own access log (#436).
            "gunicorn.access": {"level": "INFO", "handlers": ["loguru"], "propagate": False},
            "uvicorn.error": {
                "level": settings.LOG_LEVEL,
                "handlers": ["loguru"],
                "propagate": False,
            },
        },
    }
