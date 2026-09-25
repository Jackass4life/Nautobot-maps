import os


def _get_bind() -> str:
    if os.getenv("AUTH_MODE", "disabled").strip().lower() == "header":
        return "127.0.0.1:5000"
    return "0.0.0.0:5000"


def _env_int(name: str, default: int) -> int:
    # docker-compose passes unset variables as ""; that means the default.
    return int(os.getenv(name, "").strip() or default)


bind = os.getenv("GUNICORN_BIND", "").strip() or _get_bind()
workers = _env_int("GUNICORN_WORKERS", 4)
timeout = max(120, _env_int("GUNICORN_TIMEOUT", 120))


def when_ready(server) -> None:
    import app as flask_app

    flask_app._log_alert_board_exclusions()
