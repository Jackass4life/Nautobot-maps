"""LibreNMS's "Disable polling and alerting", "Disable alerting" and "Ignore
alert tag" take a device off the alert board (#570)."""

# The fixtures come from test_librenms_push: tests name them as arguments.
# ruff: noqa: F811

import asyncio

import pytest

from app.clients import caching, nautobot
from app.core.legacy_settings import settings
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from tests import db as adb
from tests.test_app import _snapshot_device
from tests.test_librenms_push import board_row, lnms, operator_token  # noqa: F401

FLAGS = {
    "disabled": "polling and alerting disabled in LibreNMS",
    "disable_notify": "alerting disabled in LibreNMS",
    "ignore": "alert tag ignored in LibreNMS",
}


def store(lnms_devices: list) -> None:
    conn = adb.connect()
    try:
        with conn.transaction():
            adb.call(inventory.write_librenms_devices, lnms_devices)
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
        (device,) = asyncio.run(
            alerts.enrich_with_librenms(
                [{"id": "d1", "name": "router01", "status": "active"}],
                lnms_devices=lnms_devices,
                lnms_id_map={},
                new_librenms_maps={},
            )
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
        devices, _ = asyncio.run(alerts.get_location_devices_and_alert("loc-1", snapshot_only=True))
        reasons = {d["name"]: alerts.not_monitored_reason(d) for d in devices}
        assert reasons == {"sw0": "", "sw1": FLAGS["disabled"], "sw2": ""}

    def test_the_site_detail_answer_says_why(self, flask_client, lnms, monkeypatch):
        """Through the endpoint, so the answer is checked against its model."""
        monkeypatch.setattr(nautobot, "fetch_all_pages", lambda *args, **kwargs: [])
        monkeypatch.setattr(nautobot, "get", lambda *args, **kwargs: {})
        store([{**lnms.devices[1001], "status": 0, "disabled": 1}])
        response = flask_client.get("/api/v1/locations/loc-1/detail")
        assert response.status_code == 200, response.get_json()
        devices = {d["name"]: d for d in response.get_json()["devices"]}
        assert devices["sw1"]["not_monitored"] == FLAGS["disabled"]
        assert "librenms_muted" not in devices["sw1"]

    def test_a_muted_member_is_not_in_its_stack(self, lnms):
        conn = adb.connect()
        try:
            with conn.transaction():
                adb.call(
                    inventory.write_devices,
                    [
                        {
                            **_snapshot_device(f"dev-{n}", "loc-1", "Access Switch", "Active"),
                            "name": f"sw{n}",
                            "vc_id": "vc-1",
                            "vc_name": "sw-stack",
                            "vc_position": n + 1,
                            "vc_master": n == 0,
                        }
                        for n in range(3)
                    ],
                )
        finally:
            conn.close()
        store([{**lnms.devices[1000], "status": 0}, {**lnms.devices[1002], "disable_notify": 1}])
        (down,) = board_row()["down_devices"]
        assert down["stack"]["size"] == 2 and down["stack"]["members"] == ["sw0", "sw1"]

    def test_not_offered_for_maintenance(self, lnms):
        store([{**lnms.devices[1002], "disable_notify": 1}])
        assert {d["id"] for d in asyncio.run(alerts.site_monitored_devices("loc-1"))} == {
            "dev-0",
            "dev-1",
        }

    def test_an_alert_push_reads_the_switches(self, flask_client, lnms):
        lnms.devices[1001].update(status=0, disable_notify=1)
        response = flask_client.post(
            "/api/v1/librenms/alert", json={"device_id": 1001}, headers=operator_token()
        )
        assert response.get_json()["updated"] is True
        assert board_row()["down_device_count"] == 0


def test_the_switches_are_stored_and_read_back(lnms):
    store([{**lnms.devices[1000], "disabled": 1, "disable_notify": "1", "ignore": True}])
    by_id = {d["device_id"]: d for d in asyncio.run(inventory.read_librenms_devices())}
    assert {flag: by_id[1000][flag] for flag in FLAGS} == dict.fromkeys(FLAGS, True)
    assert {flag: by_id[1001][flag] for flag in FLAGS} == dict.fromkeys(FLAGS, False)


# The current app's own schema migration test is its own: the schema here is
# Alembic's, checked against the current one in tests/test_schema.py (#602).
