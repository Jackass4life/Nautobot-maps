import os


def _get_bind() -> str:
    return "0.0.0.0:5000"


bind = os.getenv("GUNICORN_BIND", _get_bind())
workers = int(os.getenv("GUNICORN_WORKERS", "4"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
