"""Database engines and sessions. The per-request `AsyncSession` is opened by
`app.db.middleware.DbSessionMiddleware`; routes get it through the `AsyncDbSession`
dependency, other code through `current()`.

The engine is asyncpg's (#643; the only one since psycopg went, #664). `spawn` runs
background work (syncs, rebuilds) as tasks on the app's loop (#663).
"""

import asyncio
import functools
import shlex
import threading
from collections.abc import Awaitable, Callable
from typing import Annotated

import anyio
import anyio.from_thread
import anyio.lowlevel
import anyio.to_thread
from fastapi import Depends
from loguru import logger
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.context.db import db_session_ctx
from app.core.settings import get_settings

_settings = get_settings()


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
    """The database URL for asyncpg, and its connect arguments: give up connecting after
    *connect_timeout* s and on any statement after *statement_timeout_seconds*
    (DB_CONNECT/STATEMENT_TIMEOUT_SECONDS, #190), and the URL's libpq options as asyncpg
    names them (``options`` and ``application_name`` as server settings, ``sslmode`` as
    ``ssl``), so a URL written for libpq (the current app's) keeps working."""
    query = dict(url.query)
    options = query.pop("options", "")
    if isinstance(options, tuple):
        options = " ".join(options)
    server_settings = _server_settings(options)
    if "application_name" in query:
        server_settings["application_name"] = str(query.pop("application_name"))
    if "sslmode" in query:
        query["ssl"] = query.pop("sslmode")
    # The setting's timeouts win over the URL's.
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


# Background work started with `spawn` (#663): kept here until done, so it isn't
# collected mid-run and tests can wait for it.
_background: set[asyncio.Task] = set()
_background_lock = threading.Lock()
SPAWN_START_TIMEOUT_SECONDS = 5


def _app_loop() -> asyncio.AbstractEventLoop:
    """The loop `bind_loop` bound, else the running one."""
    if _loop_token is not None:
        return _loop_token.native_token
    return asyncio.get_running_loop()


def spawn(name: str, function: Callable[..., Awaitable[object]], *args: object) -> None:
    """``await function(*args)`` as a task named *name* on the app's event loop, from
    async code or any thread: the background syncs and rebuilds (#663), as their threads
    did before. A failure is logged, never raised."""
    loop = _app_loop()

    async def run() -> None:
        try:
            await function(*args)
        except Exception as exc:  # noqa: BLE001 - logged, the task ends
            logger.opt(exception=True).warning("Background task {} failed: {}", name, exc)

    def start() -> None:
        task = loop.create_task(run(), name=name)
        with _background_lock:
            _background.add(task)
        task.add_done_callback(_forget)

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        start()
        return
    # From another thread: started (and so listed by `background_tasks`) on return.
    started = threading.Event()

    def start_there() -> None:
        try:
            start()
        finally:
            started.set()

    loop.call_soon_threadsafe(start_there)
    started.wait(SPAWN_START_TIMEOUT_SECONDS)


def _forget(task: asyncio.Task) -> None:
    with _background_lock:
        _background.discard(task)


def background_tasks(names: set[str] | None = None) -> list[asyncio.Task]:
    """The tasks `spawn` started that are still running (with *names*: those)."""
    with _background_lock:
        tasks = list(_background)
    return [t for t in tasks if not t.done() and (names is None or t.get_name() in names)]


async def finish_background(timeout: float) -> None:
    """At shutdown: this loop's background tasks get *timeout* seconds to finish, then
    are cancelled (a rebuild's transaction rolls back)."""
    loop = asyncio.get_running_loop()
    tasks = [task for task in background_tasks() if task.get_loop() is loop]
    if not tasks:
        return
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=1)
