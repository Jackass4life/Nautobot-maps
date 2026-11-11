"""The background scheduler (#600), ported from the current app's TestBackgroundScheduler.
Its board parts (alert history recorded by the tick) come with the board (step 7)."""

import asyncio
import threading
from unittest.mock import patch

import pytest

from app.core.legacy_settings import settings
from app.db import connection as db
from app.services import inventory_service as inventory
from app.services import scheduler


@pytest.fixture(autouse=True)
def nautobot_set(monkeypatch):
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")


def test_a_tick_runs_the_due_syncs_and_rebuilds_the_board(pg_database):
    """The full tick (#602): after a sync the board is rebuilt here (alert history,
    notifications), so no rebuild request is left for the current app."""
    with (
        patch.object(inventory, "ensure_snapshot", return_value=True) as ensure,
        patch.object(scheduler, "rebuild_board") as rebuild,
    ):
        assert scheduler.tick() is True
    ensure.assert_called_once_with(wait=True)
    rebuild.assert_called_once()
    with (
        patch.object(inventory, "ensure_snapshot", return_value=False),
        patch.object(scheduler, "rebuild_board") as rebuild,
    ):
        assert scheduler.tick() is False
    rebuild.assert_not_called()


def test_only_one_process_ticks_at_a_time():
    """Another worker (or the current app) holding the lock makes this tick skip."""
    release = db.try_advisory_lock("background_scheduler")
    assert callable(release)
    try:
        assert db.try_advisory_lock("background_scheduler") is False
        with patch.object(inventory, "ensure_snapshot") as ensure:
            assert scheduler.tick() is False
        ensure.assert_not_called()
    finally:
        release()
    # Released: the next one may.
    with patch.object(inventory, "ensure_snapshot", return_value=False):
        assert scheduler.tick() is False
    again = db.try_advisory_lock("background_scheduler")
    assert callable(again)
    again()


def test_the_lock_is_the_current_apps():
    """The same key as nautobot_maps.db.advisory_lock_key("background_scheduler"), so the
    two apps never tick at once during the switchover."""
    assert db.advisory_lock_key("background_scheduler") == -7628381906339041118


def test_loop_survives_a_failing_tick(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "tick_seconds", lambda: 0)

    async def run():
        stop, running = asyncio.Event(), asyncio.get_running_loop()

        def failing_tick():
            calls.append(1)
            if len(calls) == 2:
                running.call_soon_threadsafe(stop.set)
            raise RuntimeError("nautobot down")

        monkeypatch.setattr(scheduler, "tick", failing_tick)
        await asyncio.wait_for(scheduler.loop(stop), 5)

    asyncio.run(run())
    assert len(calls) == 2


def test_the_tick_runs_off_the_event_loop(monkeypatch):
    """A slow tick (an inventory sync) never holds up the requests (#649)."""
    seen = []
    monkeypatch.setattr(scheduler, "tick_seconds", lambda: 0)

    async def run():
        stop, running = asyncio.Event(), asyncio.get_running_loop()

        def tick():
            seen.append(threading.current_thread() is threading.main_thread())
            running.call_soon_threadsafe(stop.set)

        monkeypatch.setattr(scheduler, "tick", tick)
        await asyncio.wait_for(scheduler.loop(stop), 5)

    asyncio.run(run())
    assert seen == [False]


def test_start_is_idempotent_and_respects_the_setting(monkeypatch):
    monkeypatch.setattr(scheduler, "_started", False)
    monkeypatch.setattr(scheduler, "loop", _idle)

    async def run():
        monkeypatch.setattr(settings, "BACKGROUND_SYNC_ENABLED", False)
        assert scheduler.start() is False
        monkeypatch.setattr(settings, "BACKGROUND_SYNC_ENABLED", True)
        assert scheduler.start() is True
        first = scheduler._task
        assert scheduler.start() is True
        assert scheduler._task is first and first.get_name() == "background-scheduler"
        scheduler.stop()
        await asyncio.sleep(0)
        assert first.cancelled() and scheduler._started is False

    asyncio.run(run())


def test_restart_does_not_clear_the_previous_workers_stop_event(monkeypatch):
    monkeypatch.setattr(scheduler, "_started", False)
    monkeypatch.setattr(scheduler, "loop", _idle)
    monkeypatch.setattr(settings, "BACKGROUND_SYNC_ENABLED", True)

    async def run():
        assert scheduler.start() is True
        previous_stop = scheduler._stop
        scheduler.stop()
        assert previous_stop.is_set()
        assert scheduler.start() is True
        assert scheduler._stop is not previous_stop and not scheduler._stop.is_set()
        scheduler.stop()

    asyncio.run(run())


async def _idle(stop):
    await stop.wait()


def test_not_started_without_nautobot(monkeypatch):
    monkeypatch.setattr(scheduler, "_started", False)
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
    assert scheduler.start() is False


def test_tick_seconds_follow_the_sooner_interval(monkeypatch):
    monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 300)
    assert scheduler.tick_seconds() == 30
    monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 10)
    assert scheduler.tick_seconds() == 10
    monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.example.com")
    monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "tok")
    monkeypatch.setattr(settings, "LIBRENMS_SYNC_INTERVAL_SECONDS", 5)
    assert scheduler.tick_seconds() == 5


def test_the_app_starts_and_stops_it(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "start", lambda: calls.append("start"))
    monkeypatch.setattr(scheduler, "stop", lambda: calls.append("stop"))
    from fastapi.testclient import TestClient

    from app.api import create_app

    with TestClient(create_app()):
        assert calls == ["start"]
    assert calls == ["start", "stop"]
