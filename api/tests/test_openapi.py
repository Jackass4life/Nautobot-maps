from pathlib import Path

from app.api import app
from scripts.generate_openapi import OUTPUT, render


def test_spec_is_served(client):
    spec = client.get("/api/openapi.json").json()
    assert "/api/v1/items" in spec["paths"]


def test_every_operation_has_stable_id_and_response_schema():
    for path, methods in app.openapi()["paths"].items():
        for method, op in methods.items():
            assert op["operationId"][0].islower() and "_" not in op["operationId"], (path, method)
            ok = [r for code, r in op["responses"].items() if code.startswith("2")]
            assert ok, (path, method)


def test_exported_spec_is_current():
    assert Path(OUTPUT).read_text() == render(), "run: uv run python scripts/generate_openapi.py"


def test_healthz(client):
    expected = {
        "status": "ok",
        "checks": {"app": "ok", "database": "ok"},
        "inventory_sync_age_seconds": None,
    }
    assert client.get("/healthz").json() == expected
    # The path before #594, until the switchover.
    assert client.get("/api/healthz").json() == expected


def test_healthz_without_the_database(client, monkeypatch, caplog):
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db import session

    unreachable = create_async_engine(
        "postgresql+asyncpg://nobody:secret@127.0.0.1:1/none", connect_args={"timeout": 1}
    )
    monkeypatch.setattr(session, "probe_engine", unreachable)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["checks"]["database"] == "unavailable"
    assert "secret" not in response.text and "secret" not in caplog.text


def test_healthz_gives_up_on_a_database_that_never_answers(client, monkeypatch):
    """Accepts the connection, then says nothing (#594 review): the whole probe is
    bounded, connecting included."""
    import socket
    import threading
    import time

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.db import session

    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(8)
    held = []
    stop = threading.Event()

    def accept_and_ignore():
        silent.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(silent.accept()[0])
            except OSError:
                continue

    threading.Thread(target=accept_and_ignore, daemon=True).start()
    port = silent.getsockname()[1]
    monkeypatch.setattr(
        session,
        "probe_engine",
        create_async_engine(
            f"postgresql+asyncpg://x:y@127.0.0.1:{port}/none",
            poolclass=NullPool,
            connect_args={"timeout": 30},  # the probe's own bound must win
        ),
    )
    try:
        started = time.monotonic()
        response = client.get("/healthz")
        elapsed = time.monotonic() - started
    finally:
        stop.set()
        for conn in held:
            conn.close()
        silent.close()
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unavailable"
    assert elapsed < 4


def test_a_stuck_probe_is_cancelled_at_its_deadline(client, monkeypatch):
    """A probe stuck on the database is cancelled at its deadline (#655): none is left
    running, and every request answers within it (#603 review)."""
    import asyncio
    import time

    from app.services import health_service as health

    calls, cancelled = [], []

    async def stuck():
        calls.append(1)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(1)
            raise

    monkeypatch.setattr(health, "_probe", stuck)
    monkeypatch.setattr(health, "PROBE_SECONDS", 0.2)
    monkeypatch.setattr(health, "_running", None)
    for _ in range(2):
        started = time.monotonic()
        assert client.get("/healthz").status_code == 503
        assert time.monotonic() - started < 1
    assert len(calls) == 2 and len(cancelled) == 2


def test_a_probe_nobody_waits_for_any_more_logs_nothing_more(monkeypatch, caplog):
    """Every request gave up before the probe's own deadline: its error is read, so
    asyncio logs no "Task exception was never retrieved" (#659)."""
    import asyncio
    import gc
    import logging

    from app.services import health_service as health

    async def stuck():
        await asyncio.sleep(10)

    monkeypatch.setattr(health, "_probe", stuck)
    monkeypatch.setattr(health, "PROBE_SECONDS", 0.1)
    monkeypatch.setattr(health, "_running", None)

    async def scenario():
        # The request gives up first (as one does when the probe started a while ago).
        with __import__("contextlib").suppress(TimeoutError):
            await asyncio.wait_for(health.check(), 0.01)
        await asyncio.sleep(0.3)  # the probe's own deadline passes, unawaited

    caplog.set_level(logging.ERROR, logger="asyncio")
    asyncio.run(scenario())
    health._running = None  # the last reference: the finished probe is collected now
    gc.collect()
    assert "never retrieved" not in caplog.text


def test_overlapping_checks_share_a_healthy_probe(client, monkeypatch):
    """Two checks at once, the probe still within its deadline: both healthy, one probe
    (#601 review)."""
    import asyncio
    import threading

    from app.services import health_service as health

    calls = []

    async def slow():
        calls.append(1)
        await asyncio.sleep(0.3)

    monkeypatch.setattr(health, "_probe", slow)
    monkeypatch.setattr(health, "_running", None)
    statuses = []
    threads = [
        threading.Thread(target=lambda: statuses.append(client.get("/healthz").status_code))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert statuses == [200, 200] and len(calls) == 1


def test_a_check_that_gives_up_leaves_the_shared_probe_running(client, monkeypatch):
    """One request's deadline passing doesn't cancel the probe another request shares."""
    import asyncio

    from app.services import health_service as health

    async def scenario():
        finished = []

        async def slow():
            await asyncio.sleep(0.2)
            finished.append(1)
            return 5

        monkeypatch.setattr(health, "_probe", slow)
        monkeypatch.setattr(health, "_running", None)
        probe, _ = health._current_probe()
        with __import__("contextlib").suppress(TimeoutError):
            await asyncio.wait_for(health.check(), 0.05)
        assert await probe == 5 and finished == [1]

    asyncio.run(scenario())
