"""Alertmanager and a generic webhook as device status sources (#292)."""

import pytest

from app import db
from app.clients import caching
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from app.services import webhooks_service as webhooks
from app.utils import timeutil
from app.utils.legacy_settings import settings
from tests.test_app import _snapshot_device, _snapshot_location

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
    pass  # its tests are held back (the end of the file says why)

    def test_nautobot_down_is_not_overridden_and_ipv6_matches(self, site):
        conn = db.get_conn()
        try:
            webhooks.store(
                conn, webhooks.parse_generic({"device": "2001:db8::3", "status": "down"})
            )
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


class TestRebuild:
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
    pass  # its tests are held back (the end of the file says why)


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
            webhooks.store(conn, webhooks.parse_generic({"device": "acc1", "status": "down"}))
            clock["now"] = "2026-10-11T12:00:00Z"  # past the 24 h TTL
            webhooks.store(conn, webhooks.parse_generic({"device": "other", "status": "down"}))
            keys = [
                r["alert_key"]
                for r in conn.execute("SELECT alert_key FROM webhook_device_alerts").fetchall()
            ]
            assert keys == ["other"], "the expired one is gone"
            webhooks.store(conn, webhooks.parse_generic({"device": "acc1", "status": "down"}))
            assert (
                webhooks.read_index(conn)["by_host"]["acc1"]["started_at"] == "2026-10-11T12:00:00Z"
            )
        finally:
            conn.close()

    def test_a_rebuild_that_lost_the_lock_is_done_by_the_tick(self, site, clock, monkeypatch):
        from app.services import scheduler

        conn = db.get_conn()
        try:
            assert webhooks.pending_rebuild(conn) is None
            webhooks.store(conn, webhooks.parse_generic({"device": "acc1", "status": "down"}))
            received = webhooks.pending_rebuild(conn)
            assert received is not None
        finally:
            conn.close()
        built = []
        monkeypatch.setattr(scheduler, "rebuild_board", lambda *args: built.append(True))
        monkeypatch.setattr(scheduler, "send_notifications", lambda: None)
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda **kwargs: False)
        assert scheduler.tick() is True and built == [True]
        assert scheduler.pending_webhook_alerts() is None
        assert scheduler.tick() is False and built == [True], "taken in once"


# Held back until what they need is ported (#587):
#   client: HTTP routes (step 7)
#   TestTheBoard.test_firing_marks_a_device_down_until_resolved: HTTP routes (step 7)
#   TestTheBoard.test_generic_by_ip_and_an_unknown_device: HTTP routes (step 7)
#   TestRebuild.test_a_webhook_rebuilds_once_without_a_librenms_push: HTTP routes (step 7)
#   TestAccess.test_operator_only: HTTP routes (step 7)
#   TestAccess.test_bad_bodies: HTTP routes (step 7)
