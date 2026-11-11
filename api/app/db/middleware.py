"""The per-request database session: `DbSessionMiddleware` opens one `AsyncSession` per
API request (#643), commits it when the response is a success (< 400), rolls it back
otherwise, and always closes it. Routes get it as `AsyncDbSession`, other code through
`app.db.session.current()`."""

from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.context.db import db_session_ctx
from app.db import session as db_session


class DbSessionMiddleware:
    """Plain ASGI (#594), so an answer's body goes out as the route made it (one
    piece, which gzip needs); the commit happens before the status line is sent."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/v1"):
            await self.app(scope, receive, send)
            return
        # Connects only when first used: a route without the database holds none.
        session = db_session.AsyncSessionLocal()
        token = db_session_ctx.set(session)
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
                        await session.commit()
                    except SQLAlchemyError:
                        await session.rollback()
                        replaced = True
                        failed = JSONResponse(status_code=500, content={"error": "Commit failed"})
                        await failed(scope, receive, send)
                        return
                else:
                    await session.rollback()
            await send(message)

        try:
            await self.app(scope, receive, send_after_commit)
        except BaseException:
            await session.rollback()
            raise
        finally:
            db_session_ctx.reset(token)
            await session.close()
