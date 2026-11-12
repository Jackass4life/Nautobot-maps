"""Tests wait for the app's background tasks before the next test (#299, #663)."""

import asyncio
import time

from app.db import session
from app.services import inventory_service as inventory
from tests.conftest import join_background_threads


def test_a_task_still_running_after_the_wait_is_reported():
    stop = asyncio.Event()
    session.spawn(inventory.SYNC_TASK_NAME, stop.wait)
    loop = session._app_loop()
    try:
        assert join_background_threads(timeout=0.05) == [inventory.SYNC_TASK_NAME]
    finally:
        loop.call_soon_threadsafe(stop.set)
    assert join_background_threads() == []


def test_a_finished_task_is_forgotten():
    async def quick() -> None:
        pass

    session.spawn(inventory.SYNC_TASK_NAME, quick)
    assert join_background_threads() == []
    assert session.background_tasks() == []


def test_a_failing_task_is_logged(caplog):
    async def broken() -> None:
        raise RuntimeError("nautobot down")

    session.spawn("a-test-task", broken)
    assert join_background_threads() == []
    assert "Background task a-test-task failed: nautobot down" in caplog.text


def test_shutdown_gives_running_tasks_a_grace_then_cancels_them(monkeypatch):
    """The lifespan's end (#663): a task that finishes within the grace finishes, one
    that doesn't is cancelled; the scheduler task is cancelled at once."""
    from app.services import scheduler

    monkeypatch.setattr(scheduler, "SHUTDOWN_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(scheduler, "_started", False)
    monkeypatch.setattr(scheduler, "_task", None)

    async def run() -> list[str]:
        events = []

        async def quick() -> None:
            await asyncio.sleep(0.05)
            events.append("quick finished")

        async def slow() -> None:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                events.append("slow cancelled")
                raise

        bound = session.bind_loop()
        try:
            session.spawn("quick", quick)
            session.spawn("slow", slow)
            started = time.monotonic()
            await scheduler.shutdown()
            events.append(f"took {time.monotonic() - started < 2}")
        finally:
            session.restore_loop(bound)
        return events

    assert asyncio.run(run()) == ["quick finished", "slow cancelled", "took True"]
