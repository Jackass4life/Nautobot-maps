"""Notifications when a site's alert level changes (#282)."""

import hashlib
import hmac
import json
import logging

import pytest

import app as flask_app
from nautobot_maps import alerts, caching, db, metrics, notify, scheduler, settings, timeutil
from tests.test_app import auth_config

NOW = "2026-10-09T12:00:00Z"
HOOK = "https://hooks.example.com/secret-token-123"


def site(site_id="loc-1", name="Aarhus HQ", tenants=("Acme",), **extra):
    return {
        "id": site_id,
        "name": name,
        "ancestor_path": "EMEA › DNK",
        "physical_address": "Havnegade 1",
        "tenants": list(tenants),
        "alert_reason": "Core device(s) offline: core01",
        "device_count": 10,
        "down_device_count": 2,
        "down_devices": [
            {
                "device_name": "core01",
                "device_ip": "10.0.0.1",
                "role": "Core Router",
                "down_started_at": "2026-10-09T11:55:00Z",
                "case_numbers": ["INC-7"],
            },
            {"device_name": "acc01", "device_ip": "", "role": "Access", "case_numbers": []},
        ],
        **extra,
    }


@pytest.fixture
def channels(monkeypatch):
    """Webhook + Teams + email configured; their sends recorded instead of made."""
    monkeypatch.setattr(timeutil, "iso_utc_now", lambda: NOW)
    monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", HOOK)
    monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setattr(settings, "NOTIFY_TEAMS_WEBHOOK_URL", "https://teams.example.com/workflow?sig=abc")
    monkeypatch.setattr(settings, "NOTIFY_EMAIL_TO", ["noc@example.com", "oncall@example.com"])
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "maps")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "pw")
    monkeypatch.setattr(settings, "NOTIFY_BOARD_URL", "https://maps.example.com")
    sent = {"posts": [], "mails": [], "smtp": []}

    class Response:
        status_code = 202

    def post(url, data=None, headers=None, timeout=None):
        sent["posts"].append({"url": url, "body": data, "headers": headers})
        return Response()

    class SMTP:
        def __init__(self, host, port, timeout=None):
            sent["smtp"].append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            sent["smtp"].append(("starttls",))

        def login(self, user, password):
            sent["smtp"].append(("login", user))

        def send_message(self, message):
            sent["mails"].append(message)

    monkeypatch.setattr(notify.requests, "post", post)
    monkeypatch.setattr(notify.smtplib, "SMTP", SMTP)
    return sent


def outbox():
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT * FROM notification_outbox ORDER BY id").fetchall()
        return [db.row_to_dict(row) for row in rows]
    finally:
        conn.close()


def change(levels_before, levels_after, sites=None):
    """Record two board builds: the first sets the levels, the second changes them."""
    alerts.record_site_level_changes({k: ("", v) for k, v in levels_before.items()}, "2026-10-09T11:00:00Z")
    alerts.record_site_level_changes({k: ("", v) for k, v in levels_after.items()}, NOW, sites or {})


class TestRules:
    @pytest.mark.parametrize(
        ("minimum", "before", "after", "kind"),
        [
            ("critical", "ok", "critical", "alarm"),
            ("critical", "medium", "critical", "alarm"),
            ("critical", "critical", "medium", "recovery"),
            ("critical", "critical", "ok", "recovery"),
            ("critical", "low", "medium", None),
            ("critical", "no_data", "ok", None),
            ("medium", "low", "medium", "alarm"),
            ("medium", "medium", "critical", "alarm"),
            ("medium", "critical", "medium", None),
            ("medium", "medium", "low", "recovery"),
        ],
    )
    def test_event_kind(self, monkeypatch, minimum, before, after, kind):
        monkeypatch.setattr(settings, "NOTIFY_MIN_LEVEL", minimum)
        assert notify.event_kind(before, after) == kind

    def test_tenant_filter(self, monkeypatch):
        assert notify.tenant_matches(["Anyone"])
        monkeypatch.setattr(settings, "NOTIFY_TENANTS", {"acme"})
        assert notify.tenant_matches(["Other", "ACME"])
        assert not notify.tenant_matches(["Other"]) and not notify.tenant_matches([])

    def test_message_text(self, channels):
        event = notify.build_event("alarm", "ok", "critical", site(), NOW)
        assert notify.title(event) == "🔴 Aarhus HQ is Critical"
        lines = notify.detail_lines(event)
        assert "Level: OK → Critical" in lines and "Down: 2 of 10 monitored devices" in lines
        assert "- core01 | 10.0.0.1 | Core Router | down since 2026-10-09 11:55 UTC | case INC-7" in lines
        assert notify.readable_time("2026-10-09T08:09:25.077905Z") == "2026-10-09 08:09 UTC"
        assert notify.readable_time("") == "" and notify.readable_time("soon") == "soon"
        assert "- acc01 | Access" in lines and "Cases: INC-7" in lines
        assert lines[-1] == "Board: https://maps.example.com/alerts"
        recovery = notify.build_event("recovery", "critical", "ok", site(), NOW)
        assert notify.title(recovery) == "✅ Aarhus HQ recovered (now OK)"
        assert not any(line.startswith("Down:") for line in notify.detail_lines(recovery))


@pytest.mark.usefixtures("pg_database")
class TestOutbox:
    def test_nothing_without_a_channel(self, monkeypatch):
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: NOW)
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        assert outbox() == []

    def test_a_change_queues_one_row_per_channel_in_the_same_transaction(self, channels):
        alerts.record_site_level_changes({"loc-9": ("New site", "critical")}, NOW, {"loc-9": site("loc-9")})
        assert outbox() == [], "a site's first level is not a change"
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        rows = outbox()
        assert sorted(row["channel"] for row in rows) == ["email", "teams", "webhook"]
        assert {row["kind"] for row in rows} == {"alarm"} and {row["status"] for row in rows} == {"pending"}
        payload = json.loads(rows[0]["payload_json"])
        assert payload["site"]["name"] == "Aarhus HQ" and payload["down_devices"][0]["ip"] == "10.0.0.1"

    def test_tenant_filter_and_non_crossing_changes_queue_nothing(self, channels, monkeypatch):
        change({"loc-1": "low"}, {"loc-1": "medium"}, {"loc-1": site()})
        assert outbox() == []
        monkeypatch.setattr(settings, "NOTIFY_TENANTS", {"other"})
        change({"loc-2": "ok"}, {"loc-2": "critical"}, {"loc-2": site("loc-2")})
        assert outbox() == []

    def test_sent_once_on_every_channel(self, channels):
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        assert notify.send_pending() == 3
        assert notify.send_pending() == 0, "exactly once"
        assert {row["status"] for row in outbox()} == {"sent"}

        webhook = next(post for post in channels["posts"] if post["url"] == HOOK)
        body = json.loads(webhook["body"])
        assert body["type"] == "alarm" and body["site"]["id"] == "loc-1" and body["level"] == "critical"
        expected = hmac.new(b"s3cret", webhook["body"], hashlib.sha256).hexdigest()
        assert webhook["headers"]["X-Nautobot-Maps-Signature"] == f"sha256={expected}"

        teams = json.loads(next(post for post in channels["posts"] if "teams" in post["url"])["body"])
        card = teams["attachments"][0]
        assert teams["type"] == "message" and card["contentType"] == "application/vnd.microsoft.card.adaptive"
        assert card["content"]["body"][0]["text"] == "🔴 Aarhus HQ is Critical"
        assert card["content"]["actions"][0]["url"] == "https://maps.example.com/alerts"

        (mail,) = channels["mails"]
        assert mail["Subject"] == "🔴 Aarhus HQ is Critical" and mail["To"] == "noc@example.com, oncall@example.com"
        assert ("starttls",) in channels["smtp"] and ("login", "maps") in channels["smtp"]
        assert "core01 | 10.0.0.1" in mail.get_body(("plain",)).get_content()

    def test_a_locked_row_is_not_sent_twice(self, channels):
        """Two senders at once: the second skips what the first holds (FOR UPDATE SKIP LOCKED)."""
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        holder = db.get_conn()
        try:
            with holder.transaction():
                holder.execute("SELECT id FROM notification_outbox FOR UPDATE").fetchall()
                assert notify.send_pending() == 0
        finally:
            holder.close()
        assert notify.send_pending() == 3

    def test_a_burst_is_one_summary(self, channels, monkeypatch):
        monkeypatch.setattr(settings, "NOTIFY_SUMMARY_THRESHOLD", 2)
        sites = {f"loc-{n}": site(f"loc-{n}", f"Site {n}") for n in range(3)}
        change({k: "ok" for k in sites}, {k: "critical" for k in sites}, sites)
        assert notify.send_pending() == 9
        webhook = [json.loads(post["body"]) for post in channels["posts"] if post["url"] == HOOK]
        assert len(webhook) == 1 and webhook[0]["type"] == "summary" and len(webhook[0]["events"]) == 3
        assert channels["mails"][0]["Subject"] == "Nautobot Maps: 3 sites in alarm"

    def test_failures_retry_then_give_up_without_leaking_secrets(self, channels, monkeypatch, caplog):
        monkeypatch.setattr(settings, "NOTIFY_TEAMS_WEBHOOK_URL", "")
        monkeypatch.setattr(settings, "NOTIFY_EMAIL_TO", [])
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})

        def broken(url, **kwargs):
            raise notify.requests.ConnectionError(f"Max retries exceeded with url: {url}")

        monkeypatch.setattr(notify.requests, "post", broken)
        caplog.set_level(logging.WARNING)
        assert notify.send_pending() == 0
        (row,) = outbox()
        assert row["status"] == "pending" and row["attempts"] == 1 and row["last_error"] == "ConnectionError"
        assert row["next_attempt_at"].startswith("2026-10-09T12:01:00")
        assert "secret-token-123" not in caplog.text
        assert notify.send_pending() == 0, "not due again yet"

        conn = db.get_conn()
        try:
            conn.execute(
                "UPDATE notification_outbox SET attempts = %s, next_attempt_at = %s",
                (notify.MAX_ATTEMPTS - 1, NOW),
            )
        finally:
            conn.close()
        notify.send_pending()
        (row,) = outbox()
        assert row["status"] == "failed" and row["attempts"] == notify.MAX_ATTEMPTS
        conn = db.get_conn()
        try:
            text = metrics.collect(conn)
        finally:
            conn.close()
        assert 'nautobot_maps_notifications_failed{channel="webhook"} 1' in text

    def test_http_error_status(self, channels, monkeypatch):
        class Bad:
            status_code = 500

        monkeypatch.setattr(notify.requests, "post", lambda url, **kwargs: Bad())
        monkeypatch.setattr(settings, "NOTIFY_EMAIL_TO", [])
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        notify.send_pending()
        assert {row["last_error"] for row in outbox()} == {"HTTP 500"}

    def test_pending_metric(self, channels):
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        conn = db.get_conn()
        try:
            text = metrics.collect(conn)
        finally:
            conn.close()
        assert 'nautobot_maps_notifications_pending{channel="webhook"} 1' in text


class TestScheduler:
    def test_every_tick_sends_even_without_a_sync(self, monkeypatch):
        calls = []
        monkeypatch.setattr(scheduler.db, "try_advisory_lock", lambda name: lambda: None)
        monkeypatch.setattr(scheduler, "prune_alert_history_if_due", lambda: None)
        monkeypatch.setattr(scheduler.inventory, "ensure_snapshot", lambda **kwargs: False)
        monkeypatch.setattr(scheduler.notify, "send_pending", lambda: calls.append("sent"))
        assert scheduler.tick() is False
        assert calls == ["sent"]

    def test_a_sending_error_does_not_stop_the_tick(self, monkeypatch, caplog):
        monkeypatch.setattr(scheduler.notify, "send_pending", lambda: 1 / 0)
        caplog.set_level(logging.WARNING)
        scheduler.send_notifications()
        assert "Sending notifications failed" in caplog.text


class TestTestEndpoint:
    @pytest.fixture
    def client(self):
        flask_app.app.config["TESTING"] = True
        caching.cache.clear()
        with flask_app.app.test_client() as test_client:
            yield test_client

    def test_needs_the_admin_role(self, client, channels):
        assert client.post("/api/notifications/test").status_code == 403  # AUTH_MODE=disabled
        with auth_config(mode="header", operator_groups={"ops"}, admin_groups={"admins"}):
            operator = {"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"}
            assert client.post("/api/notifications/test", headers=operator).status_code == 403
            admin = {"X-Forwarded-User": "ada", "X-Forwarded-Groups": "admins"}
            resp = client.post("/api/notifications/test", headers=admin)
        assert resp.status_code == 200
        assert resp.get_json() == {"results": {"webhook": "ok", "teams": "ok", "email": "ok"}}
        assert json.loads(channels["posts"][0]["body"])["type"] == "test"

    def test_reports_errors_without_secrets(self, client, channels, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)

        def broken(url, **kwargs):
            raise notify.requests.ConnectionError(f"cannot reach {url}")

        monkeypatch.setattr(notify.requests, "post", broken)
        resp = client.post("/api/notifications/test")
        assert resp.status_code == 502
        results = resp.get_json()["results"]
        assert results["webhook"] == "error: ConnectionError" and results["email"] == "ok"
        assert "secret-token" not in resp.get_data(as_text=True)

    def test_no_channel(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)
        for name in ("NOTIFY_WEBHOOK_URL", "NOTIFY_TEAMS_WEBHOOK_URL", "SMTP_HOST"):
            monkeypatch.setattr(settings, name, "")
        assert client.post("/api/notifications/test").status_code == 400
