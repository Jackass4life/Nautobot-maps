"""The async database functions (#643, #651) from a test's sync code."""

import asyncio
import inspect

from app.db import aconnection, session


def run(function, *args, **kwargs):  # type: ignore[no-untyped-def]
    """``await function(db, *args, **kwargs)`` on a session of its own, committed after
    (as a request's or the CLI's would be)."""

    async def call():  # type: ignore[no-untyped-def]
        async with session.AsyncSessionLocal() as db:
            result = await function(db, *args, **kwargs)
            await db.commit()
            return result

    return asyncio.run(call())


def call(function, *args, **kwargs):  # type: ignore[no-untyped-def]
    """``await function(conn, *args, **kwargs)`` on an ``aconnection`` of its own; an
    async generator's items as a list."""

    async def go():  # type: ignore[no-untyped-def]
        async with aconnection.connect() as conn:
            result = function(conn, *args, **kwargs)
            if inspect.isasyncgen(result):
                return [item async for item in result]
            return await result

    return asyncio.run(go())
