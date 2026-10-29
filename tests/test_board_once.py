"""The alert board is built once and served from the stored copy (#295)."""

import threading

import pytest

from nautobot_maps import alerts, caching, db, metrics, scheduler, settings
from tests import test_maintenance

clock = test_maintenance.clock
site = test_maintenance.site


def stats() -> dict:
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT trigger, builds FROM board_build_stats").fetchall()
        return {row["trigger"]: row["builds"] for row in map(db.row_to_dict, rows)}
    finally:
        conn.close()


def test_a_request_builds_once_then_serves_the_stored_board(site):
    caching.cache.clear()
    first = alerts.board()
    assert alerts.board() is not None and stats() == {"request": 1}
    assert caching.get(alerts.BOARD_CACHE_KEY)["checked_at"] == first["checked_at"]
    scheduler.rebuild_board()
    assert stats() == {"request": 1, "scheduler": 1}, "the scheduler always rebuilds"


def test_a_second_caller_waits_for_the_running_build(site, monkeypatch):
    caching.cache.clear()
    builds = []
    real_build = alerts.build_alert_board_payload
    monkeypatch.setattr(alerts, "build_alert_board_payload", lambda **kw: builds.append(1) or real_build(**kw))
    release = db.try_advisory_lock(alerts.BOARD_BUILD_LOCK)  # another worker is building
    assert callable(release)
    result = {}
    waiter = threading.Thread(target=lambda: result.setdefault("board", alerts.board()))
    waiter.start()
    waiter.join(0.5)
    assert waiter.is_alive(), "it waits for the lock"
    caching.set(alerts.BOARD_CACHE_KEY, {"alerts": [], "checked_at": "built-elsewhere"}, timeout=60)
    release()
    waiter.join(5)
    assert result["board"]["checked_at"] == "built-elsewhere" and builds == []


def test_after_the_wait_it_builds_anyway(site, monkeypatch):
    caching.cache.clear()
    monkeypatch.setattr(alerts, "BOARD_BUILD_WAIT_SECONDS", 0.3)
    release = db.try_advisory_lock(alerts.BOARD_BUILD_LOCK)
    try:
        assert alerts.board()["alerts"], "built without the lock"
    finally:
        release()


def test_the_stored_board_outlives_the_sync_interval(site, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_TTL", 300)
    assert alerts.board_cache_seconds({}) == 300 + alerts.BOARD_TTL_SLACK_SECONDS


def test_metrics(site):
    caching.cache.clear()
    alerts.board()
    conn = db.get_conn()
    try:
        text = metrics.collect(conn)
    finally:
        conn.close()
    assert "# TYPE nautobot_maps_board_builds_total counter" in text
    assert 'nautobot_maps_board_builds_total{trigger="request"} 1' in text
    assert 'nautobot_maps_board_build_last_duration_seconds{trigger="request"}' in text
    assert 'nautobot_maps_board_build_seconds_total{trigger="request"}' in text


@pytest.mark.parametrize("started", [False, True])
def test_a_change_rebuilds_in_the_background_only_with_the_scheduler(site, monkeypatch, started):
    calls = []
    monkeypatch.setattr(scheduler, "_started", started)
    monkeypatch.setattr(scheduler, "rebuild_after_push", lambda **kwargs: calls.append(kwargs))
    caching.set(alerts.BOARD_CACHE_KEY, {"alerts": []}, timeout=60)
    scheduler.board_changed()
    assert caching.get(alerts.BOARD_CACHE_KEY) is None
    assert calls == ([{"always": True, "trigger": "change"}] if started else [])


def test_a_change_whose_rebuild_lost_the_lock_is_rebuilt_by_the_tick(site, monkeypatch, clock):
    from nautobot_maps import inventory

    built = []
    monkeypatch.setattr(scheduler, "rebuild_board", lambda trigger="scheduler": built.append(trigger))
    monkeypatch.setattr(scheduler, "send_notifications", lambda: None)
    monkeypatch.setattr(inventory, "ensure_snapshot", lambda **kwargs: False)
    assert scheduler.pending_changes() is None
    scheduler.board_changed()  # scheduler not started here: no rebuild of its own
    assert scheduler.pending_changes() is not None
    assert scheduler.tick() is True and built == ["scheduler"]
    assert scheduler.pending_changes() is None and scheduler.tick() is False, "taken in once"


def test_the_rebuild_loop_drains_a_change_made_during_its_build(site, monkeypatch, clock):
    calls = []

    def build(trigger="scheduler"):
        calls.append(trigger)
        if len(calls) == 1:  # a change lands while the first build runs
            clock["now"] = "2026-10-09T12:00:05Z"
            scheduler.request_change_rebuild()

    class InlineThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(scheduler, "rebuild_board", build)
    monkeypatch.setattr(scheduler.threading, "Thread", InlineThread)
    scheduler.request_change_rebuild()
    scheduler.rebuild_after_push(always=True, trigger="change")
    assert calls == ["change", "change"] and scheduler.pending_changes() is None


def test_the_variant_with_excluded_locations_is_built_once_too(site, monkeypatch):
    caching.cache.clear()
    calls = []
    real = alerts.build_alert_board_payload
    monkeypatch.setattr(alerts, "build_alert_board_payload", lambda **kw: calls.append(kw) or real(**kw))
    alerts.get_alert_board_data(include_non_operational=True)
    alerts.get_alert_board_data(include_non_operational=True)
    assert calls == [{"snapshot_only": True, "include_non_operational": True}]
    assert stats() == {"request": 1}


def commit_sync(completed_at: str, **statuses) -> None:
    """A Nautobot sync committing London's devices, without dropping the stored board."""
    from nautobot_maps import inventory
    from tests.test_app import _snapshot_device, _snapshot_location

    roles = {"core": "Core Router", "acc1": "Access Switch", "acc2": "Access Switch"}
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            inventory.write_locations(conn, [_snapshot_location("loc-lon", "London HQ")])
            inventory.write_devices(
                conn, [_snapshot_device(name, "loc-lon", roles[name], status) for name, status in statuses.items()]
            )
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_completed_at=completed_at,
                last_successful_sync=completed_at,
                cache_version=inventory.CACHE_VERSION,
            )
    finally:
        conn.close()


def london_down(board: dict) -> int:
    return next(row for row in board["alerts"] if row["id"] == "loc-lon")["down_device_count"]


def test_a_sync_committed_before_the_board_is_dropped_is_served_at_once(site):
    """Between a sync's commit and its drop of the stored board, a request
    gets the new data, not the board stored before (#523)."""
    caching.cache.clear()
    assert london_down(alerts.board()) == 2
    commit_sync("2026-10-09T12:05:00Z", core="Active", acc1="Active", acc2="Active")
    assert london_down(alerts.board()) == 0
    assert london_down(alerts.apply_alert_board_freshness(alerts.board())) == 0
    assert "_snapshot" not in alerts.apply_alert_board_freshness(alerts.board())


def test_a_build_across_a_sync_commit_is_not_served_after_it(site, monkeypatch):
    caching.cache.clear()
    real_build = alerts.build_alert_board_payload

    def build_then_commit(**kwargs):
        payload = real_build(**kwargs)  # read before the commit
        commit_sync("2026-10-09T12:05:00Z", core="Active", acc1="Active", acc2="Active")
        return payload

    monkeypatch.setattr(alerts, "build_alert_board_payload", build_then_commit)
    assert london_down(alerts.board()) == 2
    monkeypatch.setattr(alerts, "build_alert_board_payload", real_build)
    assert london_down(alerts.board()) == 0


def test_a_cold_start_board_built_during_the_first_sync_is_not_stored(pg_database, monkeypatch):
    """The first sync commits while a request builds the (empty) board: that
    board must not be stored as the finished one (#523)."""
    from nautobot_maps import inventory

    monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    monkeypatch.setattr(inventory, "ensure_snapshot", lambda *args, **kwargs: False)
    caching.cache.clear()
    real_build = alerts.build_alert_board_payload

    def build_then_commit(**kwargs):
        payload = real_build(**kwargs)
        commit_sync("2026-10-09T12:05:00Z", core="Offline", acc1="Active", acc2="Active")
        return payload

    monkeypatch.setattr(alerts, "build_alert_board_payload", build_then_commit)
    assert alerts.board()["alerts"] == []
    monkeypatch.setattr(alerts, "build_alert_board_payload", real_build)
    assert london_down(alerts.board()) == 1
