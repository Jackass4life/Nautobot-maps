"""The development server (#613, the current app's ``python app.py``):

    python -m app.serve        (from api/; uv run python -m app.serve)

The same app as the image, on uvicorn with the image's options (app/server.py).
FLASK_RUN_PORT is its port (default 5000) and FLASK_DEBUG=true reloads on code
changes: the names are kept from the Flask days. With AUTH_MODE=header it listens on
127.0.0.1 only, so nothing but a local proxy can send the sign-in headers (#187).
"""

import uvicorn

from app import server, startup
from app.utils import logs
from app.utils.legacy_settings import settings


def host() -> str:
    return "127.0.0.1" if settings.AUTH_MODE == "header" else "0.0.0.0"


def port() -> int:
    try:
        return int(settings.FLASK_RUN_PORT or 5000)
    except (ValueError, TypeError):
        return 5000


def main() -> None:
    logs.configure()
    startup.log_alert_board_exclusions()
    uvicorn.run(
        "app.main:app",
        host=host(),
        port=port(),
        reload=settings.FLASK_DEBUG,
        **server.UVICORN_OPTIONS,  # type: ignore[arg-type]
    )


if __name__ == "__main__":
    main()
