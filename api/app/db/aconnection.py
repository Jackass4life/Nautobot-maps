"""The ported code's database connection, async (#651): ``db.get_conn()``'s shape on
the asyncpg pool, so its SQL moves over with ``await`` and little else.

    async with aconnection.connect() as conn:
        rows = (await conn.execute("SELECT ... WHERE id = %s", (window_id,))).fetchall()
        async with conn.transaction():
            await conn.execute(...)

The SQL keeps psycopg's placeholders (``%s``, ``%(name)s``, ``%%`` for a literal
``%``); each statement runs through SQLAlchemy's ``text()``, so asyncpg gets typed
parameters. asyncpg is strict about them: a time is a ``datetime``, not an ISO string.
Outside ``transaction()`` each statement is committed at once, as psycopg's autocommit
connection did. Rows are dicts. Call these as ``aconnection.function()`` so tests can
replace them.
"""

import re
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

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
            rows = [dict(row) for row in result.mappings()] if result.returns_rows else []
            answer = Rows(rows, result.rowcount)
        except BaseException:
            if not self._in_transaction:
                await self._conn.rollback()
            raise
        if not self._in_transaction:
            await self._conn.commit()
        return answer

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
async def connect() -> AsyncIterator[Connection]:
    """A connection from the app's async pool, given back at the end."""
    async with session.async_engine.connect() as conn:
        yield Connection(conn)
