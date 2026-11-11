"""The alert board on the async connection (#661): its lock wait leaves the event loop
free, it answers without a database connection, and its writes store their times as the
columns' types."""

import asyncio
import time
from datetime import UTC, datetime

from app.clients import caching
from app.db import aconnection
from app.db import connection as db
from app.services import alerts_service as alerts
from tests import db as adb
from tests import test_maintenance
from tests.test_maintenance import set_devices

# London HQ: core and acc1 down, acc2 up (#283's fixture).
clock = test_maintenance.clock
site = test_maintenance.site


def rows(sql: str) -> list[dict]:
    conn = db.get_conn()
    try:
        return [dict(row) for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def test_waiting_for_another_build_leaves_the_event_loop_free(site):
    """A build waiting for the lock sleeps on the loop, never blocks it: requests are
    answered meanwhile."""
    caching.cache.clear()
    release = db.try_advisory_lock(alerts.BOARD_BUILD_LOCK)  # another worker is building
    assert callable(release)

    async def go() -> tuple[dict, float]:
        waiting = asyncio.create_task(alerts.board())
        started = time.monotonic()
        for _ in range(50):  # half a second, unless the loop is blocked
            await asyncio.sleep(0.01)
        ticking = time.monotonic() - started
        assert not waiting.done(), "it waits for the lock"
        caching.set(alerts.BOARD_CACHE_KEY, {"alerts": [], "checked_at": "elsewhere"}, timeout=60)
        await asyncio.to_thread(release)
        return await asyncio.wait_for(waiting, 5), ticking

    board, ticking = asyncio.run(go())
    assert board["checked_at"] == "elsewhere"
    # Blocked by each 0.1 s wait between its tries, it would take 5 s.
    assert ticking < 2, ticking


def test_without_a_database_connection_the_board_still_answers(site, monkeypatch):
    """No connection for the build's reads and writes: the sites are still scored from
    the snapshot, and no history or level is written."""

    async def no_connection():
        raise OSError("the database is down")

    monkeypatch.setattr(aconnection, "open_connection", no_connection)
    board = asyncio.run(alerts.build_alert_board_payload(snapshot_only=True))
    (row,) = board["alerts"]
    assert (row["id"], row["down_device_count"]) == ("loc-lon", 2)
    assert rows("SELECT id FROM alert_instances") == []
    assert rows("SELECT site_id FROM site_alert_levels") == []


def at(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC)


def test_the_lifecycle_stores_its_times_as_the_columns_types(site, clock):
    """The build's times are ISO text; asyncpg takes a time only as one, so each is
    cast: an alarm opened, acknowledged and resolved, its device states and the
    site's level, all at the times the build saw."""
    caching.cache.clear()
    asyncio.run(alerts.build_alert_board_payload(snapshot_only=True))
    opened = rows("SELECT last_seen_down_at, down_started_at FROM alert_instances")
    assert [row["last_seen_down_at"] for row in opened] == [at(clock["now"])] * 2
    (level,) = rows("SELECT alert_level, first_seen_at FROM site_alert_levels")
    assert level["first_seen_at"] == at(clock["now"])
    # Down since Nautobot's last change of them (#166), the alarm's start too.
    states = rows("SELECT since FROM device_states WHERE state = 'down'")
    assert {row["since"] for row in states} == {row["down_started_at"] for row in opened}

    clock["now"] = "2026-10-09T12:10:00Z"
    adb.call(alerts.acknowledge, "loc-lon", None, "olga", "on it")
    acknowledged = rows("SELECT acknowledged_at FROM alert_instances")
    assert {row["acknowledged_at"] for row in acknowledged} == {at(clock["now"])}

    clock["now"] = "2026-10-09T12:20:00Z"
    set_devices(core="Active", acc1="Active", acc2="Active")
    caching.cache.clear()
    asyncio.run(alerts.build_alert_board_payload(snapshot_only=True))
    resolved = rows("SELECT status, resolved_at, total_downtime_seconds FROM alert_instances")
    assert {(row["status"], row["resolved_at"]) for row in resolved} == {
        ("resolved", at(clock["now"]))
    }
    (change,) = rows("SELECT to_level, changed_at FROM site_level_changes")
    assert change == {"to_level": "ok", "changed_at": at(clock["now"])}
