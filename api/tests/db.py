"""The async database functions (#643) from a test's sync code."""

import asyncio

from app.db import session


def run(function, *args, **kwargs):  # type: ignore[no-untyped-def]
    """``await function(db, *args, **kwargs)`` on a session of its own, committed after
    (as a request's or the CLI's would be)."""

    async def call():  # type: ignore[no-untyped-def]
        async with session.AsyncSessionLocal() as db:
            result = await function(db, *args, **kwargs)
            await db.commit()
            return result

    return asyncio.run(call())
