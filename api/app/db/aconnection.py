"""The ported code's database connection, async (#651): ``db.get_conn()``'s shape on
the asyncpg pool, so its SQL moves over with ``await`` and little else.

    async with aconnection.connect() as conn:
        rows = (await conn.execute("SELECT ... WHERE id = %s", (window_id,))).fetchall()
        async with conn.transaction():
            await conn.execute(...)
            await conn.executemany("INSERT ... VALUES (%s, %s)", rows)

The SQL keeps psycopg's placeholders (``%s``, ``%(name)s``, ``%%`` for a literal
``%``); each statement runs through SQLAlchemy's ``text()``, so asyncpg gets typed
parameters. asyncpg is strict about them: a time is a ``datetime``, not an ISO string.
Outside ``transaction()`` each statement is committed at once, as psycopg's autocommit
connection did. Rows are dicts. Call these as ``aconnection.function()`` so tests can
replace them.
"""

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db import session

# psycopg's placeholders, and its escaped percent sign.
_PLACEHOLDER = re.compile(r"%\((\w+)\)s|%s|%%")
# What text() would take for a bind parameter: a colon before a name, not "::" casts.
_BIND_LIKE = re.compile(r"(?<![:\w\\]):(?=\w)")


def _bind(name: str, match: re.Match) -> str:
    """``:name``; ``(:name)`` before a ``::`` cast, which text() wouldn't read as one."""
    return f"(:{name})" if match.string.startswith("::", match.end()) else f":{name}"


def statement(sql: str, params: Sequence | Mapping | None = None) -> tuple[str, dict]:
    """*sql* with psycopg's placeholders as text()'s named ones, and the parameters by
    name: ``("... id = :p0", {"p0": 7})``. A colon that text() would read as a
    parameter (none is meant as one here) is kept as written."""
    sql = _BIND_LIKE.sub(r"\\:", sql)
    if params is None:
        # As psycopg: without parameters the SQL is sent as written, a "%" included.
        return sql, {}
    binds: dict = {}
    position = 0

    def replace(match: re.Match) -> str:
        nonlocal position
        if match.group(0) == "%%":
            return "%"
        if match.group(1):
            if not isinstance(params, Mapping):
                raise TypeError(f"%({match.group(1)})s needs the parameters as a mapping")
            if match.group(1) not in params:
                raise TypeError(f"No parameter {match.group(1)!r}")
            binds[match.group(1)] = params[match.group(1)]
            return _bind(match.group(1), match)
        if isinstance(params, Mapping):
            raise TypeError("%s needs the parameters as a sequence")
        if position >= len(params):
            raise TypeError(f"{len(params)} parameters for more placeholders")
        name = f"p{position}"
        binds[name] = params[position]
        position += 1
        return _bind(name, match)

    converted = _PLACEHOLDER.sub(replace, sql)
    if not isinstance(params, Mapping) and position != len(params):
        raise TypeError(f"{len(params)} parameters for {position} placeholders")
    return converted, binds


# Rows converted between letting other requests in: a big read (every cached device)
# doesn't hold the event loop for all of its rows (#661).
TURN_ROWS = 2000


async def in_turns[T](rows: Iterable[T], every: int = TURN_ROWS) -> AsyncIterator[dict]:
    """*rows* as dicts, *every* rows at a time: the event loop runs in between."""
    for index, row in enumerate(rows):
        if index % every == every - 1:
            await asyncio.sleep(0)
        yield dict(row)  # type: ignore[call-overload]


class Rows:
    """A statement's answer, read in full: ``fetchone()``, ``fetchall()``, ``rowcount``."""

    def __init__(self, rows: list[dict], rowcount: int) -> None:
        self._rows = rows
        self._next = 0
        self.rowcount = rowcount

    def fetchone(self) -> dict | None:
        if self._next >= len(self._rows):
            return None
        self._next += 1
        return self._rows[self._next - 1]

    def fetchall(self) -> list[dict]:
        rows, self._next = self._rows[self._next :], len(self._rows)
        return rows

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.fetchall())


class Connection:
    """One pooled connection; each statement committed at once outside ``transaction()``."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn
        self._in_transaction = False

    async def execute(self, sql: str, params: Sequence | Mapping | None = None) -> Rows:
        converted, binds = statement(sql, params)
        try:
            result = await self._conn.execute(text(converted), binds)
            rows = [row async for row in in_turns(result.mappings())] if result.returns_rows else []
            answer = Rows(rows, result.rowcount)
        except BaseException:
            if not self._in_transaction:
                await self._conn.rollback()
            raise
        if not self._in_transaction:
            await self._conn.commit()
        return answer

    async def executemany(self, sql: str, rows: Sequence[Sequence | Mapping]) -> None:
        """*sql* once per row of *rows*, sent as one batch (psycopg's
        ``cursor.executemany``); nothing for no rows."""
        if not rows:
            return
        converted, _ = statement(sql, rows[0])
        binds = [statement(sql, row)[1] for row in rows]
        try:
            await self._conn.execute(text(converted), binds)
        except BaseException:
            if not self._in_transaction:
                await self._conn.rollback()
            raise
        if not self._in_transaction:
            await self._conn.commit()

    async def stream(
        self, sql: str, params: Sequence | Mapping | None = None, batch: int = 2000
    ) -> AsyncIterator[dict]:
        """The rows one at a time from a server-side cursor, *batch* at a time: a big
        export in steady memory. Inside ``transaction()``, as such a cursor must be."""
        if not self._in_transaction:
            raise RuntimeError("stream() needs a transaction()")
        converted, binds = statement(sql, params)
        result = await self._conn.stream(text(converted).execution_options(yield_per=batch), binds)
        async for row in result.mappings():
            yield dict(row)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["Connection"]:
        """One transaction: committed at the end, rolled back on an error."""
        if self._in_transaction:
            raise RuntimeError("Already in a transaction")
        if self._conn.in_transaction():
            await self._conn.commit()
        self._in_transaction = True
        try:
            yield self
        except BaseException:
            await self._conn.rollback()
            raise
        else:
            await self._conn.commit()
        finally:
            self._in_transaction = False


@asynccontextmanager
async def connect(engine: AsyncEngine | None = None) -> AsyncIterator[Connection]:
    """A connection from the app's async pool (or *engine*'s), given back at the end."""
    async with (engine or session.async_engine).connect() as conn:
        yield Connection(conn)


@asynccontextmanager
async def using(conn: Connection | None = None) -> AsyncIterator[Connection]:
    """*conn*, or a connection of the pool's for this block when there is none: the
    ported readers' ``conn=None`` (#661)."""
    if conn is not None:
        yield conn
        return
    async with connect() as own:
        yield own


async def open_connection() -> tuple[Connection, Callable[[], Awaitable[None]]]:
    """``(conn, close)``: a pooled connection and the coroutine function that gives it
    back, for code that opens and closes it in different places (the board build's
    write connection, opened on first use and replaced after a failure, #661)."""
    stack = AsyncExitStack()
    conn = await stack.enter_async_context(connect())
    return conn, stack.aclose
