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
