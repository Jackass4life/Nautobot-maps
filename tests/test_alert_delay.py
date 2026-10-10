"""Alert delay (#286): a device must be down this long before it alarms."""

import pytest

from nautobot_maps import alerts, caching, db, inventory, settings, timeutil
from tests.test_app import _snapshot_device, _snapshot_location

NOW = "2026-10-10T12:00:00Z"
LONG_AGO = "2026-01-01T00:00:00Z"


@pytest.fixture
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(timeutil, "iso_utc_now", lambda: state["now"])
    return state


@pytest.fixture
def site(pg_database, monkeypatch, clock):
    """London: a core router and two access switches, all up for long."""
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    monkeypatch.setattr(settings, "ALERT_DELAY_SECONDS", 300)
    monkeypatch.setattr(inventory, "ensure_snapshot", lambda *args, **kwargs: False)
    set_devices(core=("Active", LONG_AGO), acc1=("Active", LONG_AGO), acc2=("Active", LONG_AGO))
    build()  # the site's first level is "ok"
    return "loc-lon"


def set_devices(**devices):
    roles = {"core": "Core Router", "acc1": "Access Switch", "acc2": "Access Switch"}
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            inventory.write_locations(conn, [_snapshot_location("loc-lon", "London HQ")])
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_completed_at=NOW,
                last_successful_sync=NOW,
                cache_version=inventory.CACHE_VERSION,
            )
            inventory.write_devices(
                conn,
                [
                    {**_snapshot_device(name, "loc-lon", roles[name], status), "last_updated": changed}
                    for name, (status, changed) in devices.items()
                ],
            )
    finally:
        conn.close()


def build() -> dict:
    caching.cache.clear()
    (row,) = alerts.build_alert_board_payload(snapshot_only=True)["alerts"]
    return row


def rows(sql: str) -> list[dict]:
    conn = db.get_conn()
    try:
        return [db.row_to_dict(row) for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()


class TestDelay:
    def test_a_blip_never_happened(self, site, clock):
        set_devices(core=("Active", LONG_AGO), acc1=("Offline", NOW), acc2=("Active", LONG_AGO))
        row = build()
        assert row["alert_level"] == "ok" and row["down_devices"] == [] and row["down_device_count"] == 0
        assert row["device_count"] == 3, "still monitored"
        assert rows("SELECT device_id FROM device_states") == [{"device_id": "acc1"}]

        clock["now"] = "2026-10-10T12:02:00Z"
        set_devices(core=("Active", LONG_AGO), acc1=("Active", "2026-10-10T12:02:00Z"), acc2=("Active", LONG_AGO))
        assert build()["alert_level"] == "ok"
        assert rows("SELECT id FROM alert_instances") == []
        assert rows("SELECT id FROM site_level_changes") == []
        assert rows("SELECT id FROM notification_outbox") == []
        assert rows("SELECT device_id FROM device_states") == [], "back up: forgotten"

    def test_a_real_outage_alarms_after_the_delay_from_when_it_began(self, site, clock):
        set_devices(core=("Active", LONG_AGO), acc1=("Offline", NOW), acc2=("Active", LONG_AGO))
        build()
        clock["now"] = "2026-10-10T12:04:59Z"
        assert build()["alert_level"] == "ok"
        clock["now"] = "2026-10-10T12:05:00Z"
        row = build()
        assert row["alert_level"] != "ok" and [d["device_name"] for d in row["down_devices"]] == ["acc1"]
        (instance,) = rows("SELECT device_id, down_started_at FROM alert_instances WHERE status = 'open'")
        assert instance["device_id"] == "acc1" and instance["down_started_at"].startswith("2026-10-10T12:00:00")

    def test_down_for_long_alarms_at_once(self, site, clock):
        # Nautobot's status changed long before this build saw it.
        set_devices(core=("Active", LONG_AGO), acc1=("Offline", "2026-10-10T11:00:00Z"), acc2=("Active", LONG_AGO))
        assert build()["down_device_count"] == 1

    def test_an_alarm_already_open_stays_when_the_delay_is_turned_on(self, site, clock, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_DELAY_SECONDS", 0)
        set_devices(core=("Active", LONG_AGO), acc1=("Offline", NOW), acc2=("Active", LONG_AGO))
        assert build()["down_device_count"] == 1
        monkeypatch.setattr(settings, "ALERT_DELAY_SECONDS", 3600)
        clock["now"] = "2026-10-10T12:01:00Z"
        row = build()
        assert row["down_device_count"] == 1
        assert rows("SELECT status FROM alert_instances") == [{"status": "open"}]

    def test_an_open_alarm_stays_also_when_the_bulk_read_fails(self, site, clock, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_DELAY_SECONDS", 0)
        set_devices(core=("Active", LONG_AGO), acc1=("Offline", NOW), acc2=("Active", LONG_AGO))
        build()

        def broken(conn):
            raise RuntimeError("bulk read failed")

        monkeypatch.setattr(alerts, "read_alert_board_data", broken)
        monkeypatch.setattr(settings, "ALERT_DELAY_SECONDS", 3600)
        clock["now"] = "2026-10-10T12:01:00Z"
        assert build()["down_device_count"] == 1
        assert rows("SELECT status FROM alert_instances") == [{"status": "open"}]

    def test_off_alarms_at_once(self, site, clock, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_DELAY_SECONDS", 0)
        set_devices(core=("Active", LONG_AGO), acc1=("Offline", NOW), acc2=("Active", LONG_AGO))
        assert build()["down_device_count"] == 1

    def test_the_map_leaves_pending_devices_out_too(self, site, clock):
        set_devices(core=("Offline", NOW), acc1=("Active", LONG_AGO), acc2=("Active", LONG_AGO))
        build()  # records since when the core is down
        caching.cache.clear()
        assert "loc-lon" not in alerts.build_location_alert_levels()["levels"]
        clock["now"] = "2026-10-10T12:06:00Z"
        build()
        assert alerts.build_location_alert_levels()["levels"]["loc-lon"]["level"] == "critical"


class TestCleanup:
    def test_rows_of_devices_gone_for_a_week_are_dropped(self, site, clock):
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                conn.execute(
                    "INSERT INTO device_states (device_id, site_id, state, since, updated_at) "
                    "VALUES ('gone', 'loc-lon', 'down', %s, %s), ('recent', 'loc-lon', 'down', %s, %s)",
                    ("2026-09-01T00:00:00Z", "2026-10-02T00:00:00Z", "2026-10-09T00:00:00Z", "2026-10-09T00:00:00Z"),
                )
        finally:
            conn.close()
        build()  # nothing else changed
        assert rows("SELECT device_id FROM device_states") == [{"device_id": "recent"}]


class TestFirstSeenDown:
    def test_down_since_uses_the_earliest_known_time(self):
        librenms_only = {"down_source": "librenms", "first_seen_down_at": "2026-10-10T11:55:00Z"}
        assert alerts.down_since(librenms_only, NOW) == "2026-10-10T11:55:00Z"
        assert alerts.down_since({"down_source": "librenms"}, NOW) == NOW
        assert alerts.earliest_time(None, "", "bad", "2026-10-10T12:00:00Z", "2026-10-10T11:00:00+00:00") == (
            "2026-10-10T11:00:00+00:00"
        )

    def test_states_are_written_only_when_they_change(self, site, clock, monkeypatch):
        set_devices(core=("Active", LONG_AGO), acc1=("Offline", NOW), acc2=("Active", LONG_AGO))
        build()
        writes = []
        real = alerts.write_device_states
        monkeypatch.setattr(alerts, "write_device_states", lambda *args: writes.append(1) or real(*args))
        clock["now"] = "2026-10-10T12:01:00Z"
        build()
        assert writes == [], "acc1 still down since 12:00: nothing to write"
