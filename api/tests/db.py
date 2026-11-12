"""The async database functions (#643, #651) from a test's sync code."""

import asyncio
import inspect
from contextlib import asynccontextmanager
from unittest.mock import patch

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


def items(generator):  # type: ignore[no-untyped-def]
    """An async generator's items as a list (one that needs no connection)."""

    async def go():  # type: ignore[no-untyped-def]
        return [item async for item in generator]

    return asyncio.run(go())


def spawn_inline(_name: str, function, *args) -> None:  # type: ignore[no-untyped-def]
    """``session.spawn``'s stand-in (#663): the background task run to its end now, from
    a test's own thread."""
    asyncio.run(function(*args))


async def always_locked(_name: str):  # type: ignore[no-untyped-def]
    """``aconnection.try_advisory_lock``'s stand-in: this process always gets the lock."""

    async def release() -> None:
        pass

    return release


def async_of(function):  # type: ignore[no-untyped-def]
    """*function* (sync) as a coroutine function: a test's stand-in for an async one (#661).
    What it returns is awaited when it can be (a stand-in that calls the real one)."""

    async def call(*args, **kwargs):  # type: ignore[no-untyped-def]
        result = function(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    return call


def counting_connections():  # type: ignore[no-untyped-def]
    """``(state, patcher)``: while *patcher* is active, the ``aconnection`` connections
    opened are counted in *state*: ``taken``, ``open`` now and the ``most`` open at once."""
    state = {"taken": 0, "open": 0, "most": 0}
    real = aconnection.connect

    @asynccontextmanager
    async def counted(engine=None):  # type: ignore[no-untyped-def]
        state["taken"] += 1
        state["open"] += 1
        state["most"] = max(state["most"], state["open"])
        try:
            async with real(engine) as conn:
                yield conn
        finally:
            state["open"] -= 1

    return state, patch.object(aconnection, "connect", counted)
