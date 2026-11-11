"""Alertmanager and a generic webhook as device status sources (#292)."""

import asyncio

import pytest

from app.clients import caching
from app.core.legacy_settings import settings
from app.db import connection as db
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from app.services import webhooks_service as webhooks
from app.utils import timeutil
from tests import db as adb
from tests import http_client
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
        {
            **_snapshot_device("core", "loc-lon", "Core Router", "Active"),
            "primary_ip": "10.0.0.1/24",
        },
        {
            **_snapshot_device("acc1", "loc-lon", "Access Switch", "Active"),
            "primary_ip": "10.0.0.2/24",
        },
        {
            **_snapshot_device("acc2", "loc-lon", "Access Switch", "Offline"),
            "primary_ip": "2001:db8::3/64",
        },
    ]
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            adb.call(inventory.write_locations, [_snapshot_location("loc-lon", "London HQ")])
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_completed_at=NOW,
                last_successful_sync=NOW,
                cache_version=inventory.CACHE_VERSION,
            )
            adb.call(inventory.write_devices, devices)
    finally:
        conn.close()


@pytest.fixture
def flask_client(site):
    caching.cache.clear()
    with http_client.FlaskStyleClient() as test_client:
        yield test_client


def board_row() -> dict:
    caching.cache.clear()
    (row,) = asyncio.run(alerts.build_alert_board_payload(snapshot_only=True))["alerts"]
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
                am_alert(
                    "", labels={"hostname": "sw9"}, fingerprint=""
                ),  # then hostname; key from labels
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
            events[0]["summary"] == "Target down"
            and events[0]["started_at"].isoformat() == "2026-10-09T11:50:00+00:00"
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
            [
                {
                    "device": "sw1",
                    "status": "down",
                    "source": "Zabbix",
                    "id": 7,
                    "started_at": "2026-10-09T11:00:00Z",
                }
            ]
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
    def test_firing_marks_a_device_down_until_resolved(self, flask_client):
        token = make("am", "operator")["token"]
        assert board_row()["alert_level"] == "medium", "acc2 is Offline in Nautobot: 1 of 3"
        response = flask_client.post(
            "/api/v1/alertmanager",
            json={"alerts": [am_alert("core.example.com:9100")]},
            headers=bearer(token),
        )
        assert response.get_json() == {"firing": 1, "resolved": 0, "skipped": 0, "matched": 1}
        row = board_row()
        assert row["alert_level"] == "critical", "the core router is down"
        core = next(d for d in row["down_devices"] if d["device_name"] == "core")
        assert core["down_started_at"].startswith("2026-10-09T11:50:00")
        assert core["down_reason"] == "alertmanager: Target down"
        resolved = {"alerts": [am_alert("core.example.com:9100", status="resolved")]}
        assert (
            flask_client.post(
                "/api/v1/alertmanager", json=resolved, headers=bearer(token)
            ).get_json()["resolved"]
            == 1
        )
        assert board_row()["alert_level"] == "medium"

    def test_generic_by_ip_and_an_unknown_device(self, flask_client):
        token = make("hook", "operator")["token"]
        body = [
            {"device": "10.0.0.2", "status": "down", "source": "kuma"},
            {"device": "nobody", "status": "down"},
        ]
        assert (
            flask_client.post("/api/v1/device-status", json=body, headers=bearer(token)).get_json()[
                "matched"
            ]
            == 1
        )
        row = board_row()
        assert row["down_device_count"] == 2
        assert {d["device_name"] for d in row["down_devices"]} == {"acc1", "acc2"}

    def test_nautobot_down_is_not_overridden_and_ipv6_matches(self, site):
        adb.call(
            webhooks.store, webhooks.parse_generic({"device": "2001:db8::3", "status": "down"})
        )
        index = adb.call(webhooks.read_index)
        devices = alerts.enrich_with_webhooks(
            [{"name": "acc2", "status": "Offline", "primary_ip": "2001:db8::3/64"}], index
        )
        assert devices[0]["status"] == "Offline" and "down_source" not in devices[0]

    def test_an_alert_that_is_not_repeated_expires(self, site, clock):
        adb.call(webhooks.store, webhooks.parse_generic({"device": "acc1", "status": "down"}))
        assert "acc1" in adb.call(webhooks.read_index)["by_host"]
        clock["now"] = "2026-10-10T12:00:01Z"
        assert adb.call(webhooks.read_index)["by_host"] == {}


class TestRebuild:
    def test_a_webhook_rebuilds_once_without_a_librenms_push(self, flask_client, monkeypatch):
        from app.services import scheduler

        calls = []
        monkeypatch.setattr(scheduler, "rebuild_after_push", lambda **kwargs: calls.append(kwargs))
        token = make("am", "operator")["token"]
        flask_client.post(
            "/api/v1/device-status",
            json={"device": "acc1", "status": "down"},
            headers=bearer(token),
        )
        assert calls == [{"always": True}]

    def test_always_rebuilds_once(self, site, monkeypatch):
        from app.services import scheduler

        ran = []
        monkeypatch.setattr(
            scheduler, "rebuild_board", lambda trigger="scheduler": ran.append(trigger)
        )
        monkeypatch.setattr(scheduler, "pending_librenms_pushes", lambda: [])
        monkeypatch.setattr(db, "try_advisory_lock", lambda name: lambda: None)

        class InlineThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        monkeypatch.setattr(scheduler.threading, "Thread", InlineThread)
        scheduler.rebuild_after_push()
        assert ran == [], "no push pending: nothing to do"
        scheduler.rebuild_after_push(always=True)
        assert ran == ["push"]


class TestAccess:
    def test_operator_only(self, flask_client, monkeypatch):
        viewer = make("reader", "viewer")["token"]
        body = {"alerts": [am_alert()]}
        assert flask_client.post("/api/v1/alertmanager", json=body).status_code == 403, (
            "no token, AUTH_MODE=disabled"
        )
        assert (
            flask_client.post("/api/v1/alertmanager", json=body, headers=bearer(viewer)).status_code
            == 403
        )
        assert (
            flask_client.post("/api/v1/device-status", json={}, headers=bearer(viewer)).status_code
            == 403
        )

    def test_bad_bodies(self, flask_client):
        token = make("am", "operator")["token"]
        assert (
            flask_client.post("/api/v1/alertmanager", data="x", headers=bearer(token)).status_code
            == 400
        )
        response = flask_client.post(
            "/api/v1/device-status", json={"status": "down"}, headers=bearer(token)
        )
        assert response.status_code == 400 and "device" in response.get_json()["error"]


class TestReviewFixes:
    def test_an_overlong_id_is_refused_not_cut(self):
        with pytest.raises(webhooks.WebhookError, match="at most 255"):
            webhooks.parse_generic({"device": "sw1", "status": "down", "id": "x" * 256})
        long_fingerprint = am_alert(fingerprint="f" * 300)
        (event,), _ = webhooks.parse_alertmanager({"alerts": [long_fingerprint]})
        assert len(event["key"]) == 64, "a hash of the labels instead"

    def test_an_expired_alert_firing_again_starts_anew_and_is_pruned(self, site, clock):
        conn = db.get_conn()
        try:
            adb.call(webhooks.store, webhooks.parse_generic({"device": "acc1", "status": "down"}))
            clock["now"] = "2026-10-11T12:00:00Z"  # past the 24 h TTL
            adb.call(webhooks.store, webhooks.parse_generic({"device": "other", "status": "down"}))
            keys = [
                r["alert_key"]
                for r in conn.execute("SELECT alert_key FROM webhook_device_alerts").fetchall()
            ]
            assert keys == ["other"], "the expired one is gone"
            adb.call(webhooks.store, webhooks.parse_generic({"device": "acc1", "status": "down"}))
            assert (
                adb.call(webhooks.read_index)["by_host"]["acc1"]["started_at"]
                == "2026-10-11T12:00:00Z"
            )
        finally:
            conn.close()

    def test_a_rebuild_that_lost_the_lock_is_done_by_the_tick(self, site, clock, monkeypatch):
        from app.services import scheduler

        conn = db.get_conn()
        try:
            assert webhooks.pending_rebuild(conn) is None
            adb.call(webhooks.store, webhooks.parse_generic({"device": "acc1", "status": "down"}))
            received = webhooks.pending_rebuild(conn)
            assert received is not None
        finally:
            conn.close()
        built = []
        monkeypatch.setattr(scheduler, "rebuild_board", lambda *args: built.append(True))
        monkeypatch.setattr(scheduler, "send_notifications", lambda: None)
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda **kwargs: False)
        assert adb.worker(scheduler.tick) is True and built == [True]
        assert scheduler.pending_webhook_alerts() is None
        assert adb.worker(scheduler.tick) is False and built == [True], "taken in once"
