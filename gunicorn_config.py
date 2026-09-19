import os


def _get_bind() -> str:
    auth_mode = os.getenv("AUTH_MODE", "disabled").strip().lower() or "disabled"
    host = "127.0.0.1" if auth_mode == "header" else "0.0.0.0"
    return f"{host}:5000"


bind = os.getenv("GUNICORN_BIND", _get_bind())
workers = int(os.getenv("GUNICORN_WORKERS", "4"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
