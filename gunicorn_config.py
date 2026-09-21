import os


def _get_bind() -> str:
    if os.getenv("AUTH_MODE", "disabled").strip().lower() == "header":
        return "127.0.0.1:5000"
    return "0.0.0.0:5000"


bind = os.getenv("GUNICORN_BIND", _get_bind())
workers = int(os.getenv("GUNICORN_WORKERS", "4"))
timeout = max(120, int(os.getenv("GUNICORN_TIMEOUT", "120")))


def when_ready(server) -> None:
    import app as flask_app

    flask_app._log_alert_board_exclusions()
