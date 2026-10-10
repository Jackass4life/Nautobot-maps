"""LibreNMS alert pushes (#284): a device's status within seconds, not minutes."""

import threading

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
        return bool(conn.execute("SELECT 1 FROM librenms_device_status WHERE push_seq IS NOT NULL").fetchone())
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
            "device": {"device_id": 1001, "hostname": "sw1.corp.example", "status": "down"},
        }
        assert lnms.rebuilds == [True] and pending()
        row = board_row()
        assert row["down_device_count"] == 1 and row["down_devices"][0]["device_name"] == "sw1"

        # A recovery right after the outage is not swallowed as a duplicate.
        lnms.devices[1001]["status"] = 1
        recovered = client.post("/api/librenms/alert", data={"hostname": "sw1.corp.example"}, headers=auth_headers)
        assert recovered.get_json()["device"]["status"] == "up"
        assert board_row()["down_device_count"] == 0

    def test_the_push_names_the_device_its_status_comes_from_librenms(self, client, lnms):
        # A "recovered" push for a device LibreNMS still has down changes nothing wrong.
        lnms.devices[1002]["status"] = 0
        response = client.post("/api/librenms/alert?device_id=1002&state=0", headers=operator_token())
        assert response.get_json()["device"]["status"] == "down"

    def test_repeated_pushes_refresh_again(self, client, lnms):
        auth_headers = operator_token()
        for _ in range(2):
            assert client.post("/api/librenms/alert", json={"device_id": 1000}, headers=auth_headers).status_code == 200
        assert lnms.calls == ["devices/1000", "devices/1000"]

    def test_an_older_answer_never_overwrites_a_newer_one(self, lnms, monkeypatch):
        """The first push's LibreNMS answer (down) is slow; meanwhile the device
        recovers and a second push (by hostname) stores up.  The first one's
        late answer is older, so it is not written: up stays."""
        first_asked, release_first = threading.Event(), threading.Event()
        real_get = lnms.get

        def slow_first(path, params=None):
            answer = real_get(path, params)
            if not first_asked.is_set():
                first_asked.set()
                release_first.wait(10)
            return answer

        lnms.devices[1000]["status"] = 0
        monkeypatch.setattr(librenms, "get", slow_first)
        results = {}
        first = threading.Thread(target=lambda: results.update(first=inventory.librenms_push_refresh("1000")))
        first.start()
        assert first_asked.wait(10)
        lnms.devices[1000]["status"] = 1
        second = inventory.librenms_push_refresh("sw0.corp.example")
        assert second["updated"] and second["device"]["status"] == "up"
        release_first.set()
        first.join(10)
        assert results["first"]["updated"] is False
        assert inventory.read_librenms_devices()[0]["status"] == 1

    def test_a_sync_does_not_overwrite_a_newer_push(self, lnms, monkeypatch):
        """The sync fetched its inventory (sw0 down) before a push stored sw0 up."""
        stale = [dict(device) for device in lnms.devices.values()]
        stale[0]["status"] = 0

        def fetch_inventory_while_a_push_comes():
            inventory.librenms_push_refresh("1000")  # LibreNMS says up
            return stale

        monkeypatch.setattr(librenms, "fetch_inventory", fetch_inventory_while_a_push_comes)
        # The sync's observation number is taken before its fetch, so the push is newer.
        inventory.sync_librenms(force=True)
        statuses = {d["device_id"]: d["status"] for d in inventory.read_librenms_devices()}
        assert statuses == {1000: 1, 1001: 1, 1002: 1}
        assert pending(), "and its rebuild is still pending"

    def test_a_device_not_cached_yet_is_left_to_the_sync(self, lnms):
        lnms.devices[1003] = {"device_id": 1003, "hostname": "new.corp.example", "status": 0}
        assert inventory.librenms_push_refresh("1003")["updated"] is False
        assert 1003 not in {d["device_id"] for d in inventory.read_librenms_devices()}

    def test_a_slow_push_does_not_bring_back_a_device_a_newer_sync_removed(self, lnms, monkeypatch):
        asked, release = threading.Event(), threading.Event()
        real_get = lnms.get

        def slow(path, params=None):
            answer = real_get(path, params)
            asked.set()
            release.wait(10)
            return answer

        monkeypatch.setattr(librenms, "get", slow)
        results = {}
        push = threading.Thread(target=lambda: results.update(push=inventory.librenms_push_refresh("1002")))
        push.start()
        assert asked.wait(10)
        monkeypatch.setattr(librenms, "fetch_inventory", lambda: [lnms.devices[1000], lnms.devices[1001]])
        inventory.sync_librenms(force=True)  # 1002 is gone from LibreNMS
        release.set()
        push.join(10)
        assert results["push"]["updated"] is False
        assert sorted(d["device_id"] for d in inventory.read_librenms_devices()) == [1000, 1001]

    def test_a_sync_removes_devices_gone_from_librenms(self, lnms, monkeypatch):
        monkeypatch.setattr(librenms, "fetch_inventory", lambda: [lnms.devices[1000], lnms.devices[1001]])
        inventory.sync_librenms(force=True)
        assert sorted(d["device_id"] for d in inventory.read_librenms_devices()) == [1000, 1001]

    def test_hostname_lookups_have_an_index(self, lnms):
        conn = db.get_conn()
        try:
            row = conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'librenms_device_status_hostname_lower'"
            ).fetchone()
        finally:
            conn.close()
        assert "lower(hostname)" in db.row_to_dict(row)["indexdef"]

    def test_unknown_device_is_not_an_error(self, client, lnms):
        response = client.post("/api/librenms/alert", json={"hostname": "nope.example"}, headers=operator_token())
        assert response.status_code == 200
        assert response.get_json() == {"updated": False, "device": None}
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

    def test_a_failed_rebuild_keeps_the_push_for_the_next_tick(self, client, lnms, monkeypatch):
        client.post("/api/librenms/alert", json={"device_id": 1001}, headers=operator_token())

        def broken():
            raise RuntimeError("board build failed")

        monkeypatch.setattr(scheduler, "rebuild_board", broken)
        with pytest.raises(RuntimeError):
            scheduler.tick()
        assert pending()

    def test_a_push_during_the_rebuild_stays_pending(self, client, lnms, monkeypatch):
        auth_headers = operator_token()
        client.post("/api/librenms/alert", json={"device_id": 1001}, headers=auth_headers)

        def rebuild_while_another_push_comes():
            inventory.librenms_push_refresh("1002")

        monkeypatch.setattr(scheduler, "rebuild_board", rebuild_while_another_push_comes)
        assert scheduler.tick() is True
        conn = db.get_conn()
        try:
            rows = conn.execute("SELECT device_id FROM librenms_device_status WHERE push_seq IS NOT NULL").fetchall()
        finally:
            conn.close()
        assert [db.row_to_dict(row)["device_id"] for row in rows] == [1002]

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
                conn.execute(
                    "UPDATE librenms_device_status SET status = 0, push_seq = nextval('librenms_push_seq') "
                    "WHERE device_id = 1000"
                )
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


def test_migration_9_keeps_pending_pushes(pg_database):
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            conn.execute("ALTER TABLE librenms_device_status ADD COLUMN push_pending BOOLEAN NOT NULL DEFAULT FALSE")
            conn.execute(
                "INSERT INTO librenms_device_status (device_id, hostname, push_pending) VALUES (1, 'a', TRUE), (2, 'b', FALSE)"
            )
            db.librenms_push_seq(conn)
        rows = conn.execute("SELECT device_id FROM librenms_device_status WHERE push_seq IS NOT NULL").fetchall()
    finally:
        conn.close()
    assert [db.row_to_dict(row)["device_id"] for row in rows] == [1]
