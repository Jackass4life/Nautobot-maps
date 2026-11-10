from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients import caching, nautobot
from app.services import scheduler


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Startup (#596, #600): the response cache from the settings, Nautobot's SSL
    warnings, this process's background scheduler (off with BACKGROUND_SYNC_ENABLED=false
    or without Nautobot)."""
    caching.init()
    nautobot.configure_ssl_warnings()
    scheduler.start()
    yield
    scheduler.stop()
