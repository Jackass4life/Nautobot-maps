import os

from nautobot_maps import logs, settings


def _env_int(name: str, default: int) -> int:
    # docker-compose passes unset variables as ""; that means the default.
    return int(os.getenv(name, "").strip() or default)


# All interfaces: in a container, 127.0.0.1 is unreachable from outside it.
# Header auth trusts identity headers only from AUTH_TRUSTED_PROXIES (#187).
bind = os.getenv("GUNICORN_BIND", "").strip() or "0.0.0.0:5000"
workers = _env_int("GUNICORN_WORKERS", 4)
timeout = max(120, _env_int("GUNICORN_TIMEOUT", 120))

# Access log (#193): client, user (from the auth proxy's header), request,
# status, size and response time in ms.  Successful health checks are left
# out (see logs.SkipSuccessfulHealthChecks).
accesslog = "-"
access_log_format = '%(h)s "%({' + settings.AUTH_HEADER_USER.lower() + '}i)s" "%(r)s" %(s)s %(b)s %(M)sms "%(a)s"'
loglevel = settings.LOG_LEVEL.lower()
logconfig_dict = logs.gunicorn_logconfig()


def when_ready(server) -> None:
    import app as flask_app

    flask_app._log_alert_board_exclusions()


def post_worker_init(worker) -> None:
    # One scheduler thread per worker; a database lock lets only one of them
    # work at a time (#154).  Started here, not on import, so tests and
    # one-off imports never start background threads.
    import app  # noqa: F401 - sets up the app (logging, database) first
    from nautobot_maps import scheduler

    scheduler.start()
