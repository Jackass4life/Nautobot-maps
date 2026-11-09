"""Database engine, session factory, and the per-request session.

`DbSessionMiddleware` opens one session per API request, commits it when the
response is a success (< 400), rolls it back otherwise, and always closes it.
Controllers get that session through the `get_db` dependency (`DbSession`).
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import URL, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.utils.config import get_settings

_settings = get_settings()


def connect_args(url: URL, connect_timeout: int, statement_timeout_seconds: int) -> dict:
    """Give up connecting after *connect_timeout* s and on any statement after
    *statement_timeout_seconds* (DB_CONNECT/STATEMENT_TIMEOUT_SECONDS, #190), as the
    current app's db.get_conn(): added to any options the URL has, not replacing them."""
    options = url.query.get("options") or ""
    if isinstance(options, tuple):
        options = " ".join(options)
    options = f"{options} -c statement_timeout={statement_timeout_seconds * 1000}".strip()
    return {"connect_timeout": connect_timeout, "options": options}


# create_engine does not connect; importing this module never touches the DB.
engine = create_engine(
    _settings.sqlalchemy_url(),
    echo=_settings.sql_echo,
    pool_pre_ping=True,
    connect_args=connect_args(
        _settings.sqlalchemy_url(),
        _settings.db_connect_timeout_seconds,
        _settings.db_statement_timeout_seconds,
    ),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
# The health check's own (#594): a new connection per probe (no stale pooled one, no
# waiting for a pool slot), 2 s to connect, 2 s for the statement, and a dead peer
# noticed within 2 s (tcp_user_timeout); app/services/health_service.py bounds the whole probe too.
probe_engine = create_engine(
    _settings.sqlalchemy_url(),
    poolclass=NullPool,
    connect_args={**connect_args(_settings.sqlalchemy_url(), 2, 2), "tcp_user_timeout": 2000},
)


class DbSessionMiddleware:
    """Plain ASGI (#594), so an answer's body goes out as the route made it (one
    piece, which gzip needs); the commit happens before the status line is sent."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/v1"):
            await self.app(scope, receive, send)
            return
        session = SessionLocal()
        scope.setdefault("state", {})["db"] = session
        finished = False
        replaced = False

        async def send_after_commit(message: Message) -> None:
            nonlocal finished, replaced
            if replaced:
                return  # the route's answer after a failed commit: dropped
            if message["type"] == "http.response.start" and not finished:
                finished = True
                if message["status"] < 400:
                    try:
                        await run_in_threadpool(session.commit)
                    except SQLAlchemyError:
                        await run_in_threadpool(session.rollback)
                        replaced = True
                        failed = JSONResponse(status_code=500, content={"error": "Commit failed"})
                        await failed(scope, receive, send)
                        return
                else:
                    await run_in_threadpool(session.rollback)
            await send(message)

        try:
            await self.app(scope, receive, send_after_commit)
        except BaseException:
            await run_in_threadpool(session.rollback)
            raise
        finally:
            await run_in_threadpool(session.close)


def get_db(request: Request) -> Session:
    return request.state.db


DbSession = Annotated[Session, Depends(get_db)]
