"""LibreNMS's "Disable polling and alerting", "Disable alerting" and "Ignore
alert tag" take a device off the alert board (#570)."""

# The fixtures come from test_librenms_push: tests name them as arguments.
# ruff: noqa: F811

import pytest

from nautobot_maps import alerts, caching, db, inventory, settings
from tests.test_librenms_push import board_row, client, lnms, operator_token  # noqa: F401

FLAGS = {
    "disabled": "polling and alerting disabled in LibreNMS",
    "disable_notify": "alerting disabled in LibreNMS",
    "ignore": "alert tag ignored in LibreNMS",
}


def store(lnms_devices: list) -> None:
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            inventory.write_librenms_devices(conn, lnms_devices)
    finally:
        conn.close()
    caching.cache.clear()


class TestReason:
    @pytest.mark.parametrize("flag", FLAGS)
    @pytest.mark.parametrize("value", [1, "1", True, "true"])
    def test_each_switch_on(self, flag, value):
        assert alerts.librenms_muted({flag: value}) == FLAGS[flag]

    @pytest.mark.parametrize("value", [0, "0", False, None, ""])
    def test_off(self, value):
        assert alerts.librenms_muted({flag: value for flag in FLAGS}) == ""

    def test_polling_disabled_is_named_first(self):
        assert alerts.librenms_muted({"ignore": 1, "disabled": 1}) == FLAGS["disabled"]


class TestEnrichment:
    @pytest.fixture(autouse=True)
    def configured(self, monkeypatch):
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.test")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "tok")

    def enrich(self, record: dict) -> dict:
        lnms_devices = [{"device_id": 1, "hostname": "router01", **record}]
        (device,) = alerts.enrich_with_librenms(
            [{"id": "d1", "name": "router01", "status": "active"}],
            lnms_devices=lnms_devices,
            lnms_id_map={},
            new_librenms_maps={},
        )
        return device

    def test_a_muted_down_device_is_not_marked_offline(self):
        device = self.enrich({"status": 0, "disable_notify": 1})
        assert device["status"] == "active" and device["librenms_muted"] == FLAGS["disable_notify"]

    def test_without_a_switch_nothing_changes(self):
        device = self.enrich({"status": 0, "disabled": 0, "disable_notify": 0, "ignore": 0})
        assert device["status"] == "offline" and "librenms_muted" not in device


class TestBoard:
    @pytest.mark.parametrize("flag", FLAGS)
    def test_a_down_device_with_a_switch_set_is_left_off(self, lnms, flag):
        store([{**lnms.devices[1000], "status": 0}, {**lnms.devices[1001], "status": 0, flag: 1}])
        row = board_row()
        assert [d["device_name"] for d in row["down_devices"]] == ["sw0"]
        # Not counted at all: 3 devices, one left out.
        assert row["device_count"] == 2

    def test_the_only_down_device_muted_leaves_the_site_ok(self, lnms):
        store([{**lnms.devices[1001], "status": 0, "disable_notify": 1}])
        row = board_row()
        assert row["down_device_count"] == 0 and row["alert_level"] == "ok"

    def test_turning_the_switch_off_brings_it_back(self, lnms):
        store([{**lnms.devices[1001], "status": 0, "ignore": 1}])
        assert board_row()["down_device_count"] == 0
        store([{**lnms.devices[1001], "status": 0, "ignore": 0}])
        assert board_row()["down_device_count"] == 1

    def test_the_site_panel_says_why(self, lnms):
        store([{**lnms.devices[1001], "status": 0, "disabled": 1}])
        # The panel's devices, as get_location_detail reads them.
        devices, _ = alerts.get_location_devices_and_alert("loc-1", snapshot_only=True)
        reasons = {d["name"]: alerts.not_monitored_reason(d) for d in devices}
        assert reasons == {"sw0": "", "sw1": FLAGS["disabled"], "sw2": ""}

    def test_not_offered_for_maintenance(self, lnms):
        store([{**lnms.devices[1002], "disable_notify": 1}])
        assert {d["id"] for d in alerts.site_monitored_devices("loc-1")} == {"dev-0", "dev-1"}

    def test_an_alert_push_reads_the_switches(self, client, lnms):
        lnms.devices[1001].update(status=0, disable_notify=1)
        response = client.post("/api/librenms/alert", json={"device_id": 1001}, headers=operator_token())
        assert response.get_json()["updated"] is True
        assert board_row()["down_device_count"] == 0


def test_the_switches_are_stored_and_read_back(lnms):
    store([{**lnms.devices[1000], "disabled": 1, "disable_notify": "1", "ignore": True}])
    by_id = {d["device_id"]: d for d in inventory.read_librenms_devices()}
    assert {flag: by_id[1000][flag] for flag in FLAGS} == dict.fromkeys(FLAGS, True)
    assert {flag: by_id[1001][flag] for flag in FLAGS} == dict.fromkeys(FLAGS, False)


def test_migration_33_adds_the_switches_switched_off(pg_database):
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            for flag in FLAGS:
                conn.execute(f"ALTER TABLE librenms_device_status DROP COLUMN {flag}")
            conn.execute("INSERT INTO librenms_device_status (device_id, hostname) VALUES (1, 'a')")
            db.add_librenms_muted(conn)
        row = db.row_to_dict(
            conn.execute("SELECT disabled, disable_notify, ignore FROM librenms_device_status").fetchone()
        )
    finally:
        conn.close()
    assert row == dict.fromkeys(FLAGS, False)
