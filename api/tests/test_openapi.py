from pathlib import Path

from app.export_openapi import OUTPUT, render
from app.main import app


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
    assert Path(OUTPUT).read_text() == render(), "run: uv run python -m app.export_openapi"


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
    from sqlalchemy import create_engine

    from app import session

    unreachable = create_engine(
        "postgresql+psycopg://nobody:secret@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
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

    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    from app import session

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
        create_engine(
            f"postgresql+psycopg://x:y@127.0.0.1:{port}/none",
            poolclass=NullPool,
            connect_args={"connect_timeout": 30},  # the probe's own bound must win
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


def test_a_stuck_probe_queues_no_more(client, monkeypatch, caplog):
    """While a probe waits on a stuck database, the next answers at once (#603 review)."""
    import threading

    from app import health

    release = threading.Event()
    calls = []

    def stuck():
        calls.append(1)
        try:
            release.wait(10)
        finally:
            health._in_flight.release()

    monkeypatch.setattr(health, "_probe", stuck)
    monkeypatch.setattr(health, "PROBE_SECONDS", 0.2)
    try:
        assert client.get("/healthz").status_code == 503  # timed out, still running
        assert client.get("/healthz").status_code == 503  # not queued: busy
        assert len(calls) == 1
        assert "the last probe is still waiting" in caplog.text
    finally:
        release.set()
    for _ in range(50):
        if health._in_flight.acquire(blocking=False):
            health._in_flight.release()
            break
        threading.Event().wait(0.05)


def test_the_engines_keep_the_urls_options():
    from sqlalchemy import make_url

    from app.session import connect_args

    url = make_url("postgresql+psycopg://u@db/app?options=-c%20search_path%3Dmaps")
    args = connect_args(url, 5, 60)
    assert args["options"] == "-c search_path=maps -c statement_timeout=60000"
    assert args["connect_timeout"] == 5
    assert connect_args(make_url("postgresql+psycopg://u@db/app"), 5, 1)["options"] == (
        "-c statement_timeout=1000"
    )
