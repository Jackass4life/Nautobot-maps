"""A forced sync (the board's ?refresh=1 and its ↻ button) is skipped for a
source that finished less than a minute ago (#382)."""

from datetime import UTC, datetime, timedelta

import pytest

from nautobot_maps import db, inventory, settings


@pytest.fixture
def sources(client, pg_database, monkeypatch):  # client: the app, so the schema's cache can be cleared
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.example.com")
    monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "token")
    ran = []
    monkeypatch.setattr(inventory, "sync_nautobot", lambda force=False: ran.append("nautobot"))
    monkeypatch.setattr(inventory, "sync_librenms", lambda force=False: ran.append("librenms"))
    return ran


def finished(source: str, seconds_ago: int, status: str = "idle") -> None:
    at = (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            inventory.record_sync_state(
                conn,
                source,
                last_completed_at=at,
                last_successful_sync=at,
                cache_version=inventory.cache_version(),
                status=status,
            )
    finally:
        conn.close()


def force(cooldown: int) -> bool:
    return inventory.ensure_snapshot(force=True, full=False, wait=True, cooldown_seconds=cooldown)


def test_a_source_synced_just_now_is_not_forced_again(sources):
    finished("nautobot_inventory", 10)
    finished("librenms_inventory", 10)
    assert force(60) is False and sources == []


def test_each_source_on_its_own(sources):
    finished("nautobot_inventory", 10)
    finished("librenms_inventory", 120)
    force(60)
    assert sources == ["librenms"]


def test_after_the_cooldown_it_runs(sources):
    finished("nautobot_inventory", 61)
    finished("librenms_inventory", 61)
    force(60)
    assert sources == ["nautobot", "librenms"]


def test_without_a_cooldown_force_is_as_before(sources):
    """The other forced syncs (an empty cache on the first load) aren't held back."""
    finished("nautobot_inventory", 1)
    finished("librenms_inventory", 1)
    force(0)
    assert sources == ["nautobot", "librenms"]


def test_never_synced_runs(sources):
    force(60)
    assert sources == ["nautobot", "librenms"]


def test_a_failed_sync_is_retried_at_once(sources):
    """Pressing ↻ after an outage retries: only a successful sync starts the cooldown."""
    finished("nautobot_inventory", 5, status="error")
    finished("librenms_inventory", 5)
    force(60)
    assert sources == ["nautobot"]


def test_nothing_to_do_starts_nothing(sources, monkeypatch):
    """In the background (the board's ↻), every source just synced: no thread,
    and False, so the board doesn't say it is updating (#382)."""
    finished("nautobot_inventory", 10)
    finished("librenms_inventory", 10)
    started = []
    monkeypatch.setattr(inventory.threading, "Thread", lambda **kwargs: started.append(kwargs))
    assert inventory.ensure_snapshot(force=True, full=False, wait=False, cooldown_seconds=60) is False
    assert started == []
    assert not inventory.sync_lock.locked()
