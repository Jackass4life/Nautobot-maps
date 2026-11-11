"""Database engines and sessions. The per-request `AsyncSession` is opened by
`app.db.middleware.DbSessionMiddleware`; routes get it through the `AsyncDbSession`
dependency, other code through `current()`.

The async engine is asyncpg (#643). The sync `engine` (psycopg) is for the code not
async yet: the data contract's checks, written by the syncs' threads.
"""

import functools
import shlex
from collections.abc import Awaitable, Callable
from typing import Annotated

import anyio
import anyio.from_thread
import anyio.lowlevel
import anyio.to_thread
from fastapi import Depends
from sqlalchemy import URL, create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.context.db import db_session_ctx
from app.core.settings import get_settings

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


def _server_settings(options: str) -> dict[str, str]:
    """libpq's ``options`` (``-c name=value``, ``--name=value``) as asyncpg's
    server_settings: asyncpg has no ``options``."""
    found: dict[str, str] = {}
    words = shlex.split(options)
    while words:
        word = words.pop(0)
        if word == "-c" and words:
            word = words.pop(0)
        elif word.startswith("-c"):
            word = word[2:]
        elif word.startswith("--"):
            word = word[2:]
        else:
            raise ValueError(f"Database URL options: only -c name=value is supported, not {word!r}")
        name, equals, value = word.partition("=")
        if not equals or not name:
            raise ValueError(f"Database URL options: expected name=value, not {word!r}")
        found[name.replace("-", "_")] = value
    return found


def async_url(url: URL, connect_timeout: int, statement_timeout_seconds: int) -> tuple[URL, dict]:
    """The database URL for asyncpg, and its connect arguments: the same timeouts as
    `connect_args`, the URL's libpq options as asyncpg names them (``options`` and
    ``application_name`` as server settings, ``sslmode`` as ``ssl``)."""
    query = dict(url.query)
    options = query.pop("options", "")
    if isinstance(options, tuple):
        options = " ".join(options)
    server_settings = _server_settings(options)
    if "application_name" in query:
        server_settings["application_name"] = str(query.pop("application_name"))
    if "sslmode" in query:
        query["ssl"] = query.pop("sslmode")
    # The setting's timeout, as connect_args gives psycopg (it wins over the URL's).
    query.pop("connect_timeout", None)
    server_settings["statement_timeout"] = str(statement_timeout_seconds * 1000)
    return url.set(drivername="postgresql+asyncpg", query=query), {
        "timeout": connect_timeout,
        "server_settings": server_settings,
    }


def make_async_engine(**kwargs):  # type: ignore[no-untyped-def]
    """The app's async engine; *kwargs* for create_async_engine (tests: poolclass)."""
    url, args = async_url(
        _settings.sqlalchemy_url(),
        _settings.db_connect_timeout_seconds,
        _settings.db_statement_timeout_seconds,
    )
    return create_async_engine(
        url, echo=_settings.sql_echo, pool_pre_ping=True, connect_args=args, **kwargs
    )


# Neither connects; importing this module never touches the DB. A pooled async
# connection belongs to the event loop that opened it: the app has one per worker.
async_engine = make_async_engine()
AsyncSessionLocal = async_sessionmaker(async_engine, autoflush=False, expire_on_commit=False)

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
# The health check's own (#594): a new connection per probe (no stale pooled one, no
# waiting for a pool slot), 2 s to connect and 2 s for the statement;
# app/services/health_service.py cancels the whole probe after 2 s too (#655).
_probe_url, _probe_args = async_url(_settings.sqlalchemy_url(), 2, 2)
probe_engine = create_async_engine(_probe_url, poolclass=NullPool, connect_args=_probe_args)


def current() -> AsyncSession:
    """This request's session (`DbSessionMiddleware`)."""
    db = db_session_ctx.get()
    if db is None:
        raise RuntimeError("No database session: not in an /api/v1 request")
    return db


async def get_database() -> AsyncSession:
    return current()


AsyncDbSession = Annotated[AsyncSession, Depends(get_database)]


# The app's event loop (the lifespan binds it), for `from_sync` in threads anyio
# didn't start: the scheduler's push and change rebuilds (#655).
_loop_token: anyio.lowlevel.EventLoopToken | None = None


def bind_loop() -> anyio.lowlevel.EventLoopToken | None:
    """At startup, on the app's event loop: `from_sync` reaches it from any thread.
    Returns the loop bound before, for `restore_loop` at shutdown."""
    global _loop_token
    previous, _loop_token = _loop_token, anyio.lowlevel.current_token()
    return previous


def restore_loop(previous: anyio.lowlevel.EventLoopToken | None) -> None:
    """At shutdown: the loop bound before `bind_loop`, not one about to close."""
    global _loop_token
    _loop_token = previous


def from_sync[T](function: Callable[..., Awaitable[T]], *args: object, **kwargs: object) -> T:
    """``await function(*args, **kwargs)`` from sync code in a thread, on the app's event
    loop (its connections are that loop's): a sync route's thread, the scheduler's tick,
    or any other thread once the lifespan has bound the loop."""
    call = functools.partial(function, *args, **kwargs) if kwargs else function
    try:
        return anyio.from_thread.run(call, *([] if kwargs else args))
    except anyio.NoEventLoopError:
        if _loop_token is None:
            raise
    return anyio.from_thread.run(call, *([] if kwargs else args), token=_loop_token)


async def run_sync[T](function: Callable[..., T], *args: object, **kwargs: object) -> T:
    """``function(*args, **kwargs)`` in a worker thread, from async code (#661): sync code
    that blocks (an upstream call, a sync waited for) off the event loop, and still able to
    reach this loop with `from_sync`."""
    return await anyio.to_thread.run_sync(functools.partial(function, *args, **kwargs))
