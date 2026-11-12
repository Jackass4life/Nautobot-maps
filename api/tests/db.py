"""The async database functions (#643, #651) from a test's sync code."""

import asyncio
import inspect
import re
from collections.abc import Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from datetime import UTC, datetime
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


# The tests' own event loop on a thread (conftest's `_app_loop`): the connections a
# test's sync code holds across statements live there.
TEST_LOOP: asyncio.AbstractEventLoop | None = None


def on_test_loop(awaitable):  # type: ignore[no-untyped-def]
    """Await *awaitable* on the tests' own loop, from a test's sync code."""
    assert TEST_LOOP is not None, "conftest binds the tests' loop"

    async def wait():  # type: ignore[no-untyped-def]
        return await awaitable

    return asyncio.run_coroutine_threadsafe(wait(), TEST_LOOP).result(120)


_own_engine = None


def _engine():  # type: ignore[no-untyped-def]
    """The tests' own engine for these connections: not the app's, which a test may
    replace (an unreachable database) while the fixtures still read and empty tables."""
    global _own_engine
    if _own_engine is None:
        from sqlalchemy.pool import NullPool

        _own_engine = session.make_async_engine(poolclass=NullPool)
    return _own_engine


_ISO_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)?")


def _typed(params):  # type: ignore[no-untyped-def]
    """The tests' seeds give times as ISO text, which psycopg let the database convert;
    asyncpg takes a timestamptz only as a datetime. A parameter that is exactly an ISO
    time is sent as one (without a zone: UTC, the tests' database's)."""

    def one(value):  # type: ignore[no-untyped-def]
        if isinstance(value, str) and _ISO_TIME.fullmatch(value):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return value

    if params is None:
        return None
    if isinstance(params, dict):
        return {key: one(value) for key, value in params.items()}
    return tuple(one(value) for value in params)


class Connection:
    """An ``aconnection`` connection for a test's sync code (#664): the shape the tests
    used psycopg's ``db.get_conn()`` in, ``execute(sql, params).fetchall()``,
    ``executemany``, ``with conn.transaction():``, ``close()``."""

    def __init__(self) -> None:
        async def open_() -> tuple:  # type: ignore[no-untyped-def]
            stack = AsyncExitStack()
            return await stack.enter_async_context(aconnection.connect(_engine())), stack.aclose

        self._conn, self._close = on_test_loop(open_())

    def execute(self, sql: str, params=None) -> aconnection.Rows:  # type: ignore[no-untyped-def]
        return on_test_loop(self._conn.execute(sql, _typed(params)))

    def executemany(self, sql: str, rows) -> None:  # type: ignore[no-untyped-def]
        on_test_loop(self._conn.executemany(sql, [_typed(row) for row in rows]))

    @contextmanager
    def transaction(self) -> Iterator["Connection"]:
        block = self._conn.transaction()
        on_test_loop(block.__aenter__())
        try:
            yield self
        except BaseException as exc:
            on_test_loop(block.__aexit__(type(exc), exc, exc.__traceback__))
            raise
        on_test_loop(block.__aexit__(None, None, None))

    def close(self) -> None:
        if self._close is not None:
            on_test_loop(self._close())
            self._close = None

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def connect() -> Connection:
    return Connection()


def sql(statement: str, params=None) -> list[dict]:  # type: ignore[no-untyped-def]
    """One statement on a connection of its own; its rows (``[]`` when none)."""
    with connect() as conn:
        return conn.execute(statement, params).fetchall()


def hold_lock(name: str):  # type: ignore[no-untyped-def]
    """``aconnection.try_advisory_lock`` held from a test's sync code (another worker,
    say): a release function (sync), or False."""
    release = on_test_loop(aconnection.try_advisory_lock(name))
    if not callable(release):
        return release
    return lambda: on_test_loop(release())


class SyncConnection:
    """A SQLAlchemy ``AsyncConnection`` for a test's sync code: ``execute`` (a buffered
    result: ``scalar()``, ``one()``, rows) and ``run_sync(fn)``, which gives *fn* the
    sync ``Connection`` (Alembic, a schema snapshot)."""

    def __init__(self, conn) -> None:  # type: ignore[no-untyped-def]
        self._conn = conn

    def execute(self, statement, params=None):  # type: ignore[no-untyped-def]
        return on_test_loop(self._conn.execute(statement, params))

    def run_sync(self, function):  # type: ignore[no-untyped-def]
        return on_test_loop(self._conn.run_sync(function))

    def commit(self) -> None:
        on_test_loop(self._conn.commit())


class SyncEngine:
    """A SQLAlchemy engine for a test's sync code (#664: no sync driver any more):
    ``with engine.connect() as conn`` / ``engine.begin()`` on asyncpg, on the tests'
    own loop. *kwargs* for create_async_engine (``isolation_level="AUTOCOMMIT"``)."""

    def __init__(self, url, **kwargs) -> None:  # type: ignore[no-untyped-def]
        from sqlalchemy import make_url
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        async_url, args = session.async_url(make_url(url), 5, 0)
        self._engine = create_async_engine(
            async_url, poolclass=NullPool, connect_args=args, **kwargs
        )

    @contextmanager
    def connect(self) -> Iterator[SyncConnection]:
        conn = self._engine.connect()
        on_test_loop(conn.start())
        try:
            yield SyncConnection(conn)
        finally:
            on_test_loop(conn.close())

    @contextmanager
    def begin(self) -> Iterator[SyncConnection]:
        with self.connect() as conn:
            transaction = on_test_loop(conn._conn.begin())
            try:
                yield conn
            except BaseException:
                on_test_loop(transaction.rollback())
                raise
            on_test_loop(transaction.commit())

    def dispose(self) -> None:
        on_test_loop(self._engine.dispose())
