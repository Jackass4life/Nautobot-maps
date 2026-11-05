"""Database engine, session factory, and the per-request session.

`DbSessionMiddleware` opens one session per HTTP request, commits it when the
response is a success (< 400), rolls it back otherwise, and always closes it.
Controllers get that session through the `get_db` dependency (`DbSession`).
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.config import get_settings

_settings = get_settings()

# create_engine does not connect; importing this module never touches the DB.
engine = create_engine(_settings.database_url, echo=_settings.sql_echo, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class DbSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not request.url.path.startswith("/api/v1"):
            return await call_next(request)
        session = SessionLocal()
        request.state.db = session
        try:
            response = await call_next(request)
            if response.status_code < 400:
                try:
                    await run_in_threadpool(session.commit)
                except SQLAlchemyError:
                    await run_in_threadpool(session.rollback)
                    return JSONResponse(status_code=500, content={"detail": "Commit failed"})
            else:
                await run_in_threadpool(session.rollback)
            return response
        except BaseException:
            await run_in_threadpool(session.rollback)
            raise
        finally:
            await run_in_threadpool(session.close)


def get_db(request: Request) -> Session:
    return request.state.db


DbSession = Annotated[Session, Depends(get_db)]
