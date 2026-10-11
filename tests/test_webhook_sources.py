"""Alertmanager and a generic webhook as device status sources (#292)."""

import pytest

import app as flask_app
from nautobot_maps import alerts, caching, db, inventory, settings, timeutil, webhooks
from tests.test_app import _snapshot_device, _snapshot_location
from tests.test_tokens import bearer, make

NOW = "2026-10-09T12:00:00Z"


@pytest.fixture
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(timeutil, "iso_utc_now", lambda: state["now"])
    return state


@pytest.fixture
def site(pg_database, monkeypatch, clock):
    """London: a core router and two access switches, all up in Nautobot."""
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    monkeypatch.setattr(inventory, "ensure_snapshot", lambda *args, **kwargs: False)
    devices = [
        {**_snapshot_device("core", "loc-lon", "Core Router", "Active"), "primary_ip": "10.0.0.1/24"},
        {**_snapshot_device("acc1", "loc-lon", "Access Switch", "Active"), "primary_ip": "10.0.0.2/24"},
        {**_snapshot_device("acc2", "loc-lon", "Access Switch", "Offline"), "primary_ip": "2001:db8::3/64"},
    ]
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
            inventory.write_devices(conn, devices)
    finally:
        conn.close()


@pytest.fixture
def client(site):
    flask_app.app.config["TESTING"] = True
    caching.cache.clear()
    with flask_app.app.test_client() as test_client:
        yield test_client


def board_row() -> dict:
    caching.cache.clear()
    (row,) = alerts.build_alert_board_payload(snapshot_only=True)["alerts"]
    return row


def am_alert(instance="acc1.example.com:9100", status="firing", **extra) -> dict:
    return {
        "status": status,
        "labels": {"alertname": "InstanceDown", "instance": instance, **extra.pop("labels", {})},
        "annotations": {"summary": "Target down"},
        "startsAt": "2026-10-09T11:50:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "fingerprint": extra.pop("fingerprint", "fp-" + instance),
        **extra,
    }


class TestParsing:
    @pytest.mark.parametrize(
        ("value", "key"),
        [
            ("sw1:9100", "sw1"),
            ("SW1.Example.com", "sw1.example.com"),
            ("10.0.0.2:9100", "10.0.0.2"),
            ("[2001:db8::3]:9100", "2001:db8::3"),
            ("2001:db8::3", "2001:db8::3"),
            ("https://sw1.example.com/metrics", "sw1.example.com"),
            ("", ""),
            (None, ""),
            (42, ""),
        ],
    )
    def test_device_key(self, value, key):
        assert webhooks.device_key(value) == key

    def test_alertmanager(self, monkeypatch):
        body = {
            "version": "4",
            "alerts": [
                am_alert(),
                am_alert("x", labels={"hostname": "sw9"}),  # instance comes first
                am_alert("", labels={"hostname": "sw9"}, fingerprint=""),  # then hostname; key from labels
                am_alert("sw2", status="resolved"),
                {"status": "firing", "labels": {"alertname": "NoDevice"}},
                "junk",
            ],
        }
        events, skipped = webhooks.parse_alertmanager(body)
        assert skipped == 2
        assert [(e["device"], e["status"]) for e in events] == [
            ("acc1.example.com", "down"),
            ("x", "down"),
            ("sw9", "down"),
            ("sw2", "up"),
        ]
        assert (
            events[0]["summary"] == "Target down" and events[0]["started_at"].isoformat() == "2026-10-09T11:50:00+00:00"
        )
        assert len(events[2]["key"]) == 64
        monkeypatch.setattr(settings, "ALERTMANAGER_ALERTNAMES", {"hostdown"})
        assert webhooks.parse_alertmanager(body)[0] == []
        monkeypatch.setattr(settings, "ALERTMANAGER_DEVICE_LABELS", ["hostname", "instance"])
        monkeypatch.setattr(settings, "ALERTMANAGER_ALERTNAMES", set())
        assert webhooks.parse_alertmanager(body)[0][1]["device"] == "sw9"

    @pytest.mark.parametrize("body", [None, [], {"alerts": "x"}, {"alerts": [{}] * 1001}])
    def test_alertmanager_refuses(self, body):
        with pytest.raises(webhooks.WebhookError):
            webhooks.parse_alertmanager(body)

    def test_generic(self):
        events = webhooks.parse_generic(
            [{"device": "sw1", "status": "down", "source": "Zabbix", "id": 7, "started_at": "2026-10-09T11:00:00Z"}]
        )
        assert events[0]["source"] == "zabbix" and events[0]["key"] == "7"
        assert webhooks.parse_generic({"device": "sw1", "status": "up"})[0]["key"] == "sw1"

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ({"status": "down"}, "device"),
            ({"device": "sw1", "status": "sideways"}, "status"),
            ({"device": "sw1", "status": "down", "source": "Bad Source!"}, "source"),
            ({"device": "sw1", "status": "down", "id": True}, "id"),
            ({"device": "sw1", "status": "down", "started_at": "yesterday"}, "started_at"),
            ([], "Expected"),
            (["x"], "event 0"),
        ],
    )
    def test_generic_refuses(self, body, message):
        with pytest.raises(webhooks.WebhookError, match=message):
            webhooks.parse_generic(body)


class TestTheBoard:
    def test_firing_marks_a_device_down_until_resolved(self, client):
        token = make("am", "operator")["token"]
        assert board_row()["alert_level"] == "medium", "acc2 is Offline in Nautobot: 1 of 3"
        response = client.post(
            "/api/alertmanager", json={"alerts": [am_alert("core.example.com:9100")]}, headers=bearer(token)
        )
        assert response.get_json() == {"firing": 1, "resolved": 0, "skipped": 0, "matched": 1}
        row = board_row()
        assert row["alert_level"] == "critical", "the core router is down"
        core = next(d for d in row["down_devices"] if d["device_name"] == "core")
        assert core["down_started_at"].startswith("2026-10-09T11:50:00")
        assert core["down_reason"] == "alertmanager: Target down"
        resolved = {"alerts": [am_alert("core.example.com:9100", status="resolved")]}
        assert client.post("/api/alertmanager", json=resolved, headers=bearer(token)).get_json()["resolved"] == 1
        assert board_row()["alert_level"] == "medium"

    def test_generic_by_ip_and_an_unknown_device(self, client):
        token = make("hook", "operator")["token"]
        body = [{"device": "10.0.0.2", "status": "down", "source": "kuma"}, {"device": "nobody", "status": "down"}]
        assert client.post("/api/device-status", json=body, headers=bearer(token)).get_json()["matched"] == 1
        row = board_row()
        assert row["down_device_count"] == 2
        assert {d["device_name"] for d in row["down_devices"]} == {"acc1", "acc2"}

    def test_nautobot_down_is_not_overridden_and_ipv6_matches(self, site):
        conn = db.get_conn()
        try:
            webhooks.store(conn, webhooks.parse_generic({"device": "2001:db8::3", "status": "down"}))
            index = webhooks.read_index(conn)
        finally:
            conn.close()
        devices = alerts.enrich_with_webhooks(
            [{"name": "acc2", "status": "Offline", "primary_ip": "2001:db8::3/64"}], index
        )
        assert devices[0]["status"] == "Offline" and "down_source" not in devices[0]

    def test_an_alert_that_is_not_repeated_expires(self, site, clock):
        conn = db.get_conn()
        try:
            webhooks.store(conn, webhooks.parse_generic({"device": "acc1", "status": "down"}))
            assert "acc1" in webhooks.read_index(conn)["by_host"]
            clock["now"] = "2026-10-10T12:00:01Z"
            assert webhooks.read_index(conn)["by_host"] == {}
        finally:
            conn.close()


class TestAccess:
    def test_operator_only(self, client, monkeypatch):
        viewer = make("reader", "viewer")["token"]
        body = {"alerts": [am_alert()]}
        assert client.post("/api/alertmanager", json=body).status_code == 403, "no token, AUTH_MODE=disabled"
        assert client.post("/api/alertmanager", json=body, headers=bearer(viewer)).status_code == 403
        assert client.post("/api/device-status", json={}, headers=bearer(viewer)).status_code == 403

    def test_bad_bodies(self, client):
        token = make("am", "operator")["token"]
        assert client.post("/api/alertmanager", data="x", headers=bearer(token)).status_code == 400
        response = client.post("/api/device-status", json={"status": "down"}, headers=bearer(token))
        assert response.status_code == 400 and "device" in response.get_json()["error"]
