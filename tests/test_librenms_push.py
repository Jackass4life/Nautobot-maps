"""LibreNMS alert pushes (#284): a device's status within seconds, not minutes."""

import pytest
import requests

import app as flask_app
from nautobot_maps import alerts, caching, db, inventory, librenms, scheduler, settings, tokens
from tests.test_app import _snapshot_device, _snapshot_location, auth_config

REAL_REBUILD_AFTER_PUSH = scheduler.rebuild_after_push


class FakeLibreNMS:
    """LibreNMS's ``GET devices/<id or hostname>``, counting the calls."""

    def __init__(self):
        self.devices = {
            1000 + n: {"device_id": 1000 + n, "hostname": f"sw{n}.corp.example", "status": 1} for n in range(3)
        }
        self.calls = []
        self.fail = None

    def get(self, path, params=None):
        self.calls.append(path)
        if self.fail:
            raise self.fail
        key = path.removeprefix("devices/")
        for device in self.devices.values():
            if key in (str(device["device_id"]), device["hostname"]):
                return {"status": "ok", "devices": [dict(device)]}
        response = requests.Response()
        response.status_code = 404
        raise requests.HTTPError("404 Client Error", response=response)


@pytest.fixture
def lnms(pg_database, monkeypatch):
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.test")
    monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "lnms-secret")
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", False)
    monkeypatch.setattr(inventory, "ensure_snapshot", lambda *args, **kwargs: False)
    fake = FakeLibreNMS()
    monkeypatch.setattr(librenms, "get", fake.get)
    rebuilds = []
    monkeypatch.setattr(scheduler, "rebuild_after_push", lambda: rebuilds.append(True))
    fake.rebuilds = rebuilds
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            inventory.write_locations(conn, [_snapshot_location("loc-1", "Aarhus")])
            inventory.write_devices(
                conn,
                [
                    {**_snapshot_device(f"dev-{n}", "loc-1", "Access Switch", "Active"), "name": f"sw{n}"}
                    for n in range(3)
                ],
            )
            inventory.write_librenms_devices(conn, list(fake.devices.values()))
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_completed_at="2026-10-09T12:00:00Z",
                last_successful_sync="2026-10-09T12:00:00Z",
                cache_version=inventory.CACHE_VERSION,
            )
    finally:
        conn.close()
    caching.cache.clear()
    return fake


@pytest.fixture
def client(lnms):
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as test_client:
        yield test_client


def operator_token() -> dict:
    conn = db.get_conn()
    try:
        secret = tokens.create(conn, {"name": "librenms", "role": "operator"}, "admin")["token"]
    finally:
        conn.close()
    return {"Authorization": f"Bearer {secret}"}


def board_row() -> dict:
    (row,) = alerts.get_alert_board_data()["alerts"]
    return row


def pending() -> bool:
    conn = db.get_conn()
    try:
        return bool(conn.execute("SELECT 1 FROM librenms_device_status WHERE push_pending").fetchone())
    finally:
        conn.close()


class TestPush:
    def test_down_then_up_shows_on_the_board_at_once(self, client, lnms):
        auth_headers = operator_token()
        assert board_row()["down_device_count"] == 0
        lnms.devices[1001]["status"] = 0
        response = client.post("/api/librenms/alert", json={"device_id": 1001}, headers=auth_headers)
        assert response.status_code == 200
        assert response.get_json() == {
            "updated": True,
            "deduplicated": False,
            "device": {"device_id": 1001, "hostname": "sw1.corp.example", "status": "down"},
        }
        assert lnms.rebuilds == [True] and pending()
        row = board_row()
        assert row["down_device_count"] == 1 and row["down_devices"][0]["device_name"] == "sw1"

        lnms.devices[1001]["status"] = 1
        conn = db.get_conn()
        try:
            with db.transaction(conn):  # past the duplicate window
                conn.execute("UPDATE librenms_device_status SET pushed_at = now() - interval '1 minute'")
        finally:
            conn.close()
        recovered = client.post("/api/librenms/alert", data={"hostname": "sw1.corp.example"}, headers=auth_headers)
        assert recovered.get_json()["device"]["status"] == "up"
        assert board_row()["down_device_count"] == 0

    def test_the_push_names_the_device_its_status_comes_from_librenms(self, client, lnms):
        # A "recovered" push for a device LibreNMS still has down changes nothing wrong.
        lnms.devices[1002]["status"] = 0
        response = client.post("/api/librenms/alert?device_id=1002&state=0", headers=operator_token())
        assert response.get_json()["device"]["status"] == "down"

    def test_duplicates_within_seconds_ask_librenms_once(self, client, lnms):
        auth_headers = operator_token()
        lnms.devices[1000]["status"] = 0
        for _ in range(3):
            response = client.post("/api/librenms/alert", json={"device_id": 1000}, headers=auth_headers)
            assert response.status_code == 200
        assert lnms.calls == ["devices/1000"]
        assert response.get_json()["deduplicated"] is True and response.get_json()["device"]["status"] == "down"
        assert lnms.rebuilds == [True]
        # By hostname, the same device is a duplicate too.
        client.post("/api/librenms/alert", json={"hostname": "SW0.corp.example"}, headers=auth_headers)
        assert lnms.calls == ["devices/1000"]

    def test_unknown_device_is_not_an_error(self, client, lnms):
        response = client.post("/api/librenms/alert", json={"hostname": "nope.example"}, headers=operator_token())
        assert response.status_code == 200
        assert response.get_json() == {"updated": False, "device": None, "deduplicated": False}
        assert lnms.rebuilds == [] and not pending()

    @pytest.mark.parametrize(
        "body", [{}, {"device_id": ""}, {"device_id": True}, {"hostname": ["x"]}, {"hostname": "x" * 256}]
    )
    def test_missing_device(self, client, body):
        response = client.post("/api/librenms/alert", json=body, headers=operator_token())
        assert response.status_code == 400 and response.get_json()["error"] == "device_id or hostname is required"

    def test_librenms_failure_is_a_502_without_secrets(self, client, lnms, caplog):
        lnms.fail = requests.ConnectionError("https://librenms.test/api/v0/devices/1000 lnms-secret refused")
        response = client.post("/api/librenms/alert", json={"device_id": 1000}, headers=operator_token())
        assert response.status_code == 502
        text = response.get_data(as_text=True) + caplog.text
        assert "librenms.test" not in text and "lnms-secret" not in text and "ConnectionError" in caplog.text

    def test_not_configured(self, client, monkeypatch):
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        response = client.post("/api/librenms/alert", json={"device_id": 1000}, headers=operator_token())
        assert response.status_code == 409

    def test_needs_an_operator(self, client, lnms):
        assert client.post("/api/librenms/alert", json={"device_id": 1000}).status_code == 403  # disabled mode
        conn = db.get_conn()
        try:
            viewer = tokens.create(conn, {"name": "reader", "role": "viewer"}, "admin")["token"]
        finally:
            conn.close()
        refused = client.post(
            "/api/librenms/alert", json={"device_id": 1000}, headers={"Authorization": f"Bearer {viewer}"}
        )
        assert refused.status_code == 403
        with auth_config(mode="header", operator_groups={"ops"}):
            assert client.post("/api/librenms/alert", json={"device_id": 1000}).status_code == 401
        assert lnms.calls == []


class TestRebuild:
    def test_the_next_tick_rebuilds_for_a_pending_push(self, client, lnms, monkeypatch):
        lnms.devices[1001]["status"] = 0
        client.post("/api/librenms/alert", json={"device_id": 1001}, headers=operator_token())
        built = []
        monkeypatch.setattr(scheduler, "send_notifications", lambda: None)
        monkeypatch.setattr(scheduler, "rebuild_board", lambda: built.append(True))
        assert scheduler.tick() is True and built == [True]
        assert not pending()
        assert scheduler.tick() is False and built == [True], "nothing pending: no rebuild"

    def test_rebuild_after_push_builds_and_stores_the_board(self, lnms, monkeypatch):
        monkeypatch.setattr(scheduler, "rebuild_after_push", REAL_REBUILD_AFTER_PUSH)
        monkeypatch.setattr(scheduler, "send_notifications", lambda: None)

        class InlineThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        monkeypatch.setattr(scheduler.threading, "Thread", InlineThread)
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                conn.execute("UPDATE librenms_device_status SET status = 0, push_pending = TRUE WHERE device_id = 1000")
        finally:
            conn.close()
        caching.cache.clear()
        scheduler.rebuild_after_push()
        cached = caching.get("alert-board-data:v3")
        assert cached is not None and cached["alerts"][0]["down_device_count"] == 1
        assert not pending()

    def test_another_process_holding_the_lock_leaves_it_to_the_tick(self, lnms, monkeypatch):
        monkeypatch.setattr(scheduler, "rebuild_after_push", REAL_REBUILD_AFTER_PUSH)
        monkeypatch.setattr(db, "try_advisory_lock", lambda name: False)
        ran = []
        monkeypatch.setattr(scheduler, "rebuild_board", lambda: ran.append(True))

        class InlineThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        monkeypatch.setattr(scheduler.threading, "Thread", InlineThread)
        scheduler.rebuild_after_push()
        assert ran == []
