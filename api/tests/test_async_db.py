"""The async database layer (#643): asyncpg's URL and timeouts, one session per
request, the base repository."""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import make_url, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.context.db import db_session_ctx
from app.core.repository import AsyncBaseRepository
from app.db import session
from app.db.middleware import DbSessionMiddleware
from app.models import Item
from tests import db as adb
from tests.conftest import TEST_DATABASE_URL


def test_the_url_is_asyncpgs_with_the_libpq_options_translated():
    url, args = session.async_url(
        make_url(
            "postgresql+psycopg://u:p%40ss@db:5433/app?sslmode=require&connect_timeout=30"
            "&application_name=maps&options=-c%20search_path%3Dmaps%20--lock-timeout%3D5s"
        ),
        5,
        60,
    )
    assert url.drivername == "postgresql+asyncpg"
    assert (url.host, url.port, url.database, url.password) == ("db", 5433, "app", "p@ss")
    assert dict(url.query) == {"ssl": "require"}
    # The setting's timeouts win, as with psycopg.
    assert args == {
        "timeout": 5,
        "server_settings": {
            "search_path": "maps",
            "lock_timeout": "5s",
            "application_name": "maps",
            "statement_timeout": "60000",
        },
    }


@pytest.mark.parametrize("options", ["-x", "-c search_path", "search_path=maps"])
def test_options_asyncpg_cannot_take_are_refused(options):
    with pytest.raises(ValueError, match="Database URL options"):
        session.async_url(make_url("postgresql://u@db/app").set(query={"options": options}), 5, 1)


def test_the_timeouts_and_options_reach_the_database():
    async def shown() -> tuple[str, str]:
        url, args = session.async_url(
            make_url(TEST_DATABASE_URL).set(query={"options": "-c search_path=maps,public"}), 5, 7
        )
        engine = create_async_engine(url, poolclass=NullPool, connect_args=args)
        try:
            async with engine.connect() as conn:
                timeout = (await conn.execute(text("SHOW statement_timeout"))).scalar()
                path = (await conn.execute(text("SHOW search_path"))).scalar()
                return timeout, path
        finally:
            await engine.dispose()

    assert asyncio.run(shown()) == ("7s", "maps,public")


@pytest.fixture
def sessions(monkeypatch):
    """The sessions the middleware opens, each with whether it was closed."""
    opened = []
    real = session.AsyncSessionLocal

    def tracked():
        db = real()
        state = {"session": db, "closed": False}
        close = db.close

        async def closing():
            state["closed"] = True
            await close()

        db.close = closing
        opened.append(state)
        return db

    monkeypatch.setattr(session, "AsyncSessionLocal", tracked)
    return opened


def small_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(DbSessionMiddleware)

    @app.post("/api/v1/item/{name}")
    async def add(name: str, status: int = 201):
        session.current().add(Item(name=name))
        await session.current().flush()
        return JSONResponse({"name": name}, status_code=status)

    @app.post("/api/v1/broken/{name}")
    async def broken(name: str):
        session.current().add(Item(name=name))
        await session.current().flush()
        raise RuntimeError("route failed")

    @app.get("/other")
    async def other():
        return {"session": db_session_ctx.get() is not None}

    return app


def names() -> list[str]:
    async def read(db):
        return list(await db.scalars(select(Item.name).order_by(Item.name)))

    return adb.run(read)


def test_a_success_is_committed_anything_else_rolled_back(sessions):
    with TestClient(small_app(), raise_server_exceptions=False) as client:
        assert client.post("/api/v1/item/kept").status_code == 201
        assert client.post("/api/v1/item/refused?status=400").status_code == 400
        assert client.post("/api/v1/broken/failed").status_code == 500
        # Only /api/v1 requests get one.
        assert client.get("/other").json() == {"session": False}
    assert len(sessions) == 3 and all(state["closed"] for state in sessions)
    assert names() == ["kept"]


def test_a_failed_commit_answers_500_not_the_routes_answer(sessions, monkeypatch):
    with TestClient(small_app()) as client:
        real = session.AsyncSessionLocal

        def refusing():
            db = real()

            async def commit():
                raise SQLAlchemyError("could not serialize access")

            db.commit = commit
            return db

        monkeypatch.setattr(session, "AsyncSessionLocal", refusing)
        response = client.post("/api/v1/item/lost")
    assert response.status_code == 500 and response.json() == {"error": "Commit failed"}
    monkeypatch.undo()
    assert names() == []


def test_outside_a_request_there_is_no_session():
    with pytest.raises(RuntimeError, match="No database session"):
        session.current()


def test_the_base_repository():
    async def use(db):
        items = AsyncBaseRepository(Item, db)
        first = await items.create(Item(name="first"))
        await items.create(Item(name="second"))
        assert first.id and first.created_at, "the id and server defaults are loaded"
        assert (await items.get(first.id)).name == "first"
        assert await items.get(first.id + 100) is None
        assert [i.name for i in await items.get_all(select(Item).order_by(Item.name))] == [
            "first",
            "second",
        ]
        assert len(await items.get_all()) == 2
        assert (await items.get_first(select(Item).where(Item.name == "second"))).name == "second"
        first.description = "changed"
        await items.save(first)
        assert await items.delete(first.id) is True
        assert await items.delete(first.id) is False

    adb.run(use)
    assert names() == ["second"]


def test_from_sync_reaches_the_bound_loop_from_any_thread(monkeypatch):
    """A thread anyio didn't start (the scheduler's rebuilds) once the lifespan has
    bound the loop (#655); before that, the error says so."""
    import threading

    import anyio
    import anyio.to_thread

    monkeypatch.setattr(session, "_loop_token", None)
    results: list = []

    def in_a_thread():
        try:
            results.append(session.from_sync(asyncio.sleep, 0, "ok"))
        except anyio.NoEventLoopError as exc:
            results.append(type(exc).__name__)

    async def main():
        for bind in (False, True):
            if bind:
                session.bind_loop()
            thread = threading.Thread(target=in_a_thread)
            thread.start()
            await anyio.to_thread.run_sync(thread.join)

    asyncio.run(main())
    assert results == ["NoEventLoopError", "ok"]
