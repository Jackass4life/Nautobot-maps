import os


def _env_int(name: str, default: int) -> int:
    # docker-compose passes unset variables as ""; that means the default.
    return int(os.getenv(name, "").strip() or default)


# All interfaces: in a container, 127.0.0.1 is unreachable from outside it.
# Header auth trusts identity headers only from AUTH_TRUSTED_PROXIES (#187).
bind = os.getenv("GUNICORN_BIND", "").strip() or "0.0.0.0:5000"
workers = _env_int("GUNICORN_WORKERS", 4)
timeout = max(120, _env_int("GUNICORN_TIMEOUT", 120))


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
