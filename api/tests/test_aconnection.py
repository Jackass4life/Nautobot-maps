"""The ported code's async connection (#651): psycopg's SQL on the asyncpg pool."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import DBAPIError

from app.db import aconnection
from tests import db as adb


@pytest.mark.parametrize(
    ("sql", "params", "converted", "binds"),
    [
        ("SELECT %s, %s", (1, "a"), "SELECT :p0, :p1", {"p0": 1, "p1": "a"}),
        ("SELECT %(n)s + %(n)s", {"n": 2}, "SELECT :n + :n", {"n": 2}),
        ("SELECT 'a%%' LIKE %s", ("a%",), "SELECT 'a%' LIKE :p0", {"p0": "a%"}),
        ("SELECT %s::text[]", (["x"],), "SELECT (:p0)::text[]", {"p0": ["x"]}),
        # A colon text() would take for a parameter stays as written.
        (
            "SELECT '08:00', 'a :b' WHERE x = %s",
            (1,),
            "SELECT '08:00', 'a \\:b' WHERE x = :p0",
            {"p0": 1},
        ),
        # Without parameters, as psycopg: the SQL as written.
        ("SELECT 'a%' AS x", None, "SELECT 'a%' AS x", {}),
    ],
)
def test_placeholders(sql, params, converted, binds):
    assert aconnection.statement(sql, params) == (converted, binds)


@pytest.mark.parametrize(
    ("sql", "params"),
    [("SELECT %s, %s", (1,)), ("SELECT %(n)s", (1,)), ("SELECT %s", {"n": 1})],
)
def test_mismatched_parameters_are_refused(sql, params):
    with pytest.raises(TypeError):
        aconnection.statement(sql, params)


def test_rows_are_dicts_and_values_keep_their_types():
    when = datetime(2026, 11, 11, 8, 0, tzinfo=UTC)

    async def read(conn):
        result = await conn.execute(
            "SELECT %s::timestamptz AS at, 'a:b' AS colon, '08:00'::time AS t, "
            "%s::jsonb AS data, n FROM generate_series(1, 3) AS n ORDER BY n",
            (when, json.dumps({"k": [1, 2]})),
        )
        first = result.fetchone()
        rest = result.fetchall()
        return first, rest, result.fetchone(), result.rowcount

    first, rest, after, rowcount = adb.call(read)
    assert first["at"] == when and first["colon"] == "a:b" and str(first["t"]) == "08:00:00"
    assert first["data"] == {"k": [1, 2]}
    assert [row["n"] for row in rest] == [2, 3] and after is None and rowcount == 3


def test_each_statement_is_committed_outside_a_transaction():
    async def write(conn):
        await conn.execute(
            "INSERT INTO status_links (tenant_key, link_id) VALUES (%s, %s)", ("a", "1")
        )
        with pytest.raises(DBAPIError):
            await conn.execute(
                "INSERT INTO status_links (tenant_key, link_id) VALUES (%s, %s)", ("a", "2")
            )
        # The failed one rolled back on its own: the connection is usable.
        updated = await conn.execute(
            "UPDATE status_links SET link_id = %s WHERE tenant_key = %s", ("3", "a")
        )
        return updated.rowcount

    assert adb.call(write) == 1
    assert adb.call(_links) == {"a": "3"}


def test_a_transaction_is_all_or_nothing():
    async def write(conn):
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO status_links (tenant_key, link_id) VALUES (%s, %s)", ("b", "1")
            )
        with pytest.raises(RuntimeError, match="boom"):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO status_links (tenant_key, link_id) VALUES (%s, %s)", ("c", "2")
                )
                raise RuntimeError("boom")

    adb.call(write)
    assert adb.call(_links) == {"b": "1"}


def test_executemany_writes_every_row_in_one_go():
    async def write(conn):
        await conn.executemany("INSERT INTO status_links (tenant_key, link_id) VALUES (%s, %s)", [])
        async with conn.transaction():
            await conn.executemany(
                "INSERT INTO status_links (tenant_key, link_id) VALUES (%s, %s)",
                [("d", "1"), ("e", "2")],
            )
        with pytest.raises(DBAPIError):  # outside a transaction: committed or not at all
            await conn.executemany(
                "INSERT INTO status_links (tenant_key, link_id) VALUES (%(k)s, %(v)s)",
                [{"k": "f", "v": "3"}, {"k": "d", "v": "4"}],
            )

    adb.call(write)
    assert adb.call(_links) == {"d": "1", "e": "2"}


def test_stream_reads_a_server_side_cursor_in_a_transaction():
    async def read(conn):
        with pytest.raises(RuntimeError, match="transaction"):
            async for _ in conn.stream("SELECT 1 AS n"):
                pass
        async with conn.transaction():
            return [
                row["n"]
                async for row in conn.stream("SELECT n FROM generate_series(1, 5) n", batch=2)
            ]

    assert adb.call(read) == [1, 2, 3, 4, 5]


def test_connections_are_given_back():
    async def twice():
        for _ in range(2):
            async with aconnection.connect() as conn:
                assert (await conn.execute("SELECT 1 AS one")).fetchone() == {"one": 1}

    asyncio.run(twice())


async def _links(conn):
    rows = (await conn.execute("SELECT tenant_key, link_id FROM status_links")).fetchall()
    return {row["tenant_key"]: row["link_id"] for row in rows}
