from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients import caching, http
from app.db import session
from app.services import scheduler


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Startup (#596, #600): the response cache from the settings, the event loop for
    sync code's async calls (#655), this process's background scheduler (off with
    BACKGROUND_SYNC_ENABLED=false or without Nautobot); at shutdown, the upstream HTTP
    clients' connections closed (#645)."""
    caching.init()
    session.bind_loop()
    scheduler.start()
    yield
    scheduler.stop()
    http.close()
