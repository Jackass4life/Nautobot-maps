from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.settings import Settings


def configure(app: FastAPI, settings: Settings) -> None:
    """Only for other sites' pages: the app's own share its origin (#594), and without
    any, the middleware would still add "Vary: Origin" to every answer."""
    if not settings.cors_origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # A sign-in proxy's cookie may come along (#582); never with "*".
        allow_credentials="*" not in settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
