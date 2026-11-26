"""Notifications when a site's alert level changes (#282)."""

import asyncio
import hashlib
import hmac
import json
import logging

import httpx
import pytest

from app.db import connection as db
from app.db import session
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from app.services import metrics_service as metrics
from app.services import notify_service as notify
from app.services import scheduler
from app.services import status_service as status
from app.utils import timeutil
from tests import db as adb
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
def channels(monkeypatch, override_settings):
    """Webhook + Teams + email configured; their sends recorded instead of made."""
    monkeypatch.setattr(timeutil, "iso_utc_now", lambda: NOW)
    override_settings(
        NOTIFY_WEBHOOK_URL=HOOK,
        NOTIFY_WEBHOOK_SECRET="s3cret",
        NOTIFY_TEAMS_WEBHOOK_URL="https://teams.example.com/workflow?sig=abc",
        NOTIFY_EMAIL_TO=["noc@example.com", "oncall@example.com"],
        SMTP_HOST="smtp.example.com",
        SMTP_USERNAME="maps",
        SMTP_PASSWORD="pw",
        NOTIFY_BOARD_URL="https://maps.example.com",
    )
    sent = {"posts": [], "mails": [], "smtp": []}

    class Response:
        status_code = 202

    def post(url, content=None, headers=None, timeout=None):
        sent["posts"].append({"url": url, "body": content, "headers": headers})
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

    monkeypatch.setattr(notify, "_post", post)
    monkeypatch.setattr(notify.smtplib, "SMTP", SMTP)
    return sent


def outbox():
    rows = adb.sql("SELECT * FROM notification_outbox ORDER BY id")
    return [db.row_to_dict(row) for row in rows]


def change(levels_before, levels_after, sites=None):
    """Record two board builds: the first sets the levels, the second changes them."""
    asyncio.run(
        alerts.record_site_level_changes(
            {k: ("", v) for k, v in levels_before.items()}, "2026-10-09T11:00:00Z"
        )
    )
    asyncio.run(
        alerts.record_site_level_changes(
            {k: ("", v) for k, v in levels_after.items()}, NOW, sites or {}
        )
    )


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
    def test_event_kind(self, minimum, before, after, kind, override_settings):
        override_settings(NOTIFY_MIN_LEVEL=minimum)
        assert notify.event_kind(before, after) == kind

    def test_tenant_filter(self, override_settings):
        assert notify.tenant_matches(["Anyone"])
        override_settings(NOTIFY_TENANTS={"acme"})
        assert notify.tenant_matches(["Other", "ACME"])
        assert not notify.tenant_matches(["Other"]) and not notify.tenant_matches([])

    def test_message_text(self, channels):
        event = notify.build_event("alarm", "ok", "critical", site(), NOW)
        assert notify.title(event) == "🔴 Aarhus HQ is Critical"
        lines = notify.detail_lines(event)
        assert "Level: OK → Critical" in lines and "Down: 2 of 10 monitored devices" in lines
        assert (
            "- core01 | 10.0.0.1 | Core Router | down since 2026-10-09 11:55 UTC | case INC-7"
            in lines
        )
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
        asyncio.run(
            alerts.record_site_level_changes(
                {"loc-9": ("New site", "critical")}, NOW, {"loc-9": site("loc-9")}
            )
        )
        assert outbox() == [], "a site's first level is not a change"
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        rows = outbox()
        assert sorted(row["channel"] for row in rows) == ["email", "teams", "webhook"]
        assert {row["kind"] for row in rows} == {"alarm"} and {row["status"] for row in rows} == {
            "pending"
        }
        payload = json.loads(rows[0]["payload_json"])
        assert (
            payload["site"]["name"] == "Aarhus HQ"
            and payload["down_devices"][0]["ip"] == "10.0.0.1"
        )

    def test_tenant_filter_and_non_crossing_changes_queue_nothing(
        self, channels, override_settings
    ):
        change({"loc-1": "low"}, {"loc-1": "medium"}, {"loc-1": site()})
        assert outbox() == []
        override_settings(NOTIFY_TENANTS={"other"})
        change({"loc-2": "ok"}, {"loc-2": "critical"}, {"loc-2": site("loc-2")})
        assert outbox() == []

    def test_sent_once_on_every_channel(self, channels):
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        assert asyncio.run(notify.send_pending()) == 3
        assert asyncio.run(notify.send_pending()) == 0, "exactly once"
        assert {row["status"] for row in outbox()} == {"sent"}

        webhook = next(post for post in channels["posts"] if post["url"] == HOOK)
        body = json.loads(webhook["body"])
        assert (
            body["type"] == "alarm"
            and body["site"]["id"] == "loc-1"
            and body["level"] == "critical"
        )
        expected = hmac.new(b"s3cret", webhook["body"], hashlib.sha256).hexdigest()
        assert webhook["headers"]["X-Nautobot-Maps-Signature"] == f"sha256={expected}"

        teams = json.loads(
            next(post for post in channels["posts"] if "teams" in post["url"])["body"]
        )
        card = teams["attachments"][0]
        assert (
            teams["type"] == "message"
            and card["contentType"] == "application/vnd.microsoft.card.adaptive"
        )
        assert card["content"]["body"][0]["text"] == "🔴 Aarhus HQ is Critical"
        assert card["content"]["actions"][0]["url"] == "https://maps.example.com/alerts"

        (mail,) = channels["mails"]
        assert (
            mail["Subject"] == "🔴 Aarhus HQ is Critical"
            and mail["To"] == "noc@example.com, oncall@example.com"
        )
        assert ("starttls",) in channels["smtp"] and ("login", "maps") in channels["smtp"]
        assert "core01 | 10.0.0.1" in mail.get_body(("plain",)).get_content()

    def test_a_locked_row_is_not_sent_twice(self, channels):
        """Two senders at once: the second skips what the first holds (FOR UPDATE SKIP LOCKED)."""
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        holder = adb.connect()
        try:
            with holder.transaction():
                holder.execute("SELECT id FROM notification_outbox FOR UPDATE").fetchall()
                assert asyncio.run(notify.send_pending()) == 0
        finally:
            holder.close()
        assert asyncio.run(notify.send_pending()) == 3

    def test_a_burst_is_one_summary(self, channels, override_settings):
        override_settings(NOTIFY_SUMMARY_THRESHOLD=2)
        sites = {f"loc-{n}": site(f"loc-{n}", f"Site {n}") for n in range(3)}
        change({k: "ok" for k in sites}, {k: "critical" for k in sites}, sites)
        assert asyncio.run(notify.send_pending()) == 9
        webhook = [json.loads(post["body"]) for post in channels["posts"] if post["url"] == HOOK]
        assert (
            len(webhook) == 1 and webhook[0]["type"] == "summary" and len(webhook[0]["events"]) == 3
        )
        assert channels["mails"][0]["Subject"] == "Nautobot Maps: 3 sites in alarm"

    def test_summaries_are_bounded(self, channels, monkeypatch, override_settings):
        """A backlog becomes several summaries of at most SUMMARY_MAX_EVENTS (Copilot on #302)."""
        override_settings(NOTIFY_SUMMARY_THRESHOLD=2)
        monkeypatch.setattr(notify, "SUMMARY_MAX_EVENTS", 3)
        override_settings(NOTIFY_TEAMS_WEBHOOK_URL="", NOTIFY_EMAIL_TO=[])
        sites = {f"loc-{n}": site(f"loc-{n}", f"Site {n}") for n in range(7)}
        change({k: "ok" for k in sites}, {k: "critical" for k in sites}, sites)
        assert asyncio.run(notify.send_pending()) == 7
        bodies = [json.loads(post["body"]) for post in channels["posts"]]
        # A single site left over goes out as a normal, detailed message.
        assert [len(body["events"]) if body["type"] == "summary" else 1 for body in bodies] == [
            3,
            3,
            1,
        ]
        assert [body["type"] for body in bodies] == ["summary", "summary", "alarm"]

    def test_each_row_counts_its_own_attempts(self, channels, monkeypatch, override_settings):
        """A fresh row in a summary with an old one keeps its retries (Copilot on #302)."""
        override_settings(
            NOTIFY_SUMMARY_THRESHOLD=1, NOTIFY_TEAMS_WEBHOOK_URL="", NOTIFY_EMAIL_TO=[]
        )
        sites = {f"loc-{n}": site(f"loc-{n}") for n in range(2)}
        change({k: "ok" for k in sites}, {k: "critical" for k in sites}, sites)
        adb.sql(
            "UPDATE notification_outbox SET attempts = %s WHERE site_id = 'loc-0'",
            (notify.MAX_ATTEMPTS - 1,),
        )

        class Bad:
            status_code = 503

        monkeypatch.setattr(notify, "_post", lambda url, **kwargs: Bad())
        asyncio.run(notify.send_pending())
        by_site = {row["site_id"]: row for row in outbox()}
        assert (
            by_site["loc-0"]["status"] == "failed"
            and by_site["loc-0"]["attempts"] == notify.MAX_ATTEMPTS
        )
        assert by_site["loc-1"]["status"] == "pending" and by_site["loc-1"]["attempts"] == 1

    def test_event_id_is_stable_across_channels(self, channels):
        """Delivery is at least once; receivers drop duplicates by id (Copilot on #302)."""
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        ids = {json.loads(row["payload_json"])["id"] for row in outbox()}
        assert len(ids) == 1 and len(next(iter(ids))) == 32
        same = notify.build_event("alarm", "ok", "critical", site(), NOW)["id"]
        assert ids == {same}
        assert notify.build_event("recovery", "critical", "ok", site(), NOW)["id"] != same

    def test_failures_retry_then_give_up_without_leaking_secrets(
        self, channels, monkeypatch, caplog, override_settings
    ):
        override_settings(NOTIFY_TEAMS_WEBHOOK_URL="", NOTIFY_EMAIL_TO=[])
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})

        def broken(url, **kwargs):
            raise httpx.ConnectError(f"Max retries exceeded with url: {url}")

        monkeypatch.setattr(notify, "_post", broken)
        caplog.set_level(logging.WARNING)
        assert asyncio.run(notify.send_pending()) == 0
        (row,) = outbox()
        assert (
            row["status"] == "pending"
            and row["attempts"] == 1
            and row["last_error"] == "ConnectError"
        )
        assert row["next_attempt_at"].startswith("2026-10-09T12:01:00")
        assert "secret-token-123" not in caplog.text
        assert asyncio.run(notify.send_pending()) == 0, "not due again yet"

        adb.sql(
            "UPDATE notification_outbox SET attempts = %s, next_attempt_at = %s",
            (notify.MAX_ATTEMPTS - 1, NOW),
        )
        asyncio.run(notify.send_pending())
        (row,) = outbox()
        assert row["status"] == "failed" and row["attempts"] == notify.MAX_ATTEMPTS
        text = adb.call(metrics.collect)
        assert 'nautobot_maps_notifications_failed{channel="webhook"} 1' in text

    def test_http_error_status(self, channels, monkeypatch, override_settings):
        class Bad:
            status_code = 500

        monkeypatch.setattr(notify, "_post", lambda url, **kwargs: Bad())
        override_settings(NOTIFY_EMAIL_TO=[])
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        asyncio.run(notify.send_pending())
        assert {row["last_error"] for row in outbox()} == {"HTTP 500"}

    def test_pending_metric(self, channels):
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        text = adb.call(metrics.collect)
        assert 'nautobot_maps_notifications_pending{channel="webhook"} 1' in text


class TestScheduler:
    def test_the_tick_sends_on_the_event_loop(self, channels):
        """From the tick's worker thread, the async sending runs on the loop (#655)."""
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        asyncio.run(scheduler.send_notifications())
        assert {row["status"] for row in outbox()} == {"sent"}

    def test_a_background_rebuild_sends_on_the_apps_loop(self, channels, client):
        """A push or change rebuild is a task on the loop the app's lifespan bound (#655,
        #663), also when started from another thread."""
        from tests.conftest import join_background_threads

        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        session.spawn(scheduler.PUSH_REBUILD_TASK_NAME, scheduler.send_notifications)
        assert join_background_threads() == []
        assert {row["status"] for row in outbox()} == {"sent"}

    def test_every_tick_sends_even_without_a_sync(self, monkeypatch):
        calls = []
        monkeypatch.setattr(scheduler.aconnection, "try_advisory_lock", adb.always_locked)
        monkeypatch.setattr(scheduler, "prune_alert_history_if_due", adb.async_of(lambda: None))
        monkeypatch.setattr(
            scheduler.inventory, "ensure_snapshot", adb.async_of(lambda **kwargs: False)
        )

        async def sent():
            calls.append("sent")

        monkeypatch.setattr(scheduler.notify, "send_pending", sent)
        assert asyncio.run(scheduler.tick()) is False
        assert calls == ["sent"]

    def test_sends_before_a_slow_or_failing_sync_and_after_a_rebuild(self, monkeypatch):
        """Copilot on #302: a sync must not hold messages up."""
        calls = []
        monkeypatch.setattr(scheduler.aconnection, "try_advisory_lock", adb.always_locked)
        monkeypatch.setattr(scheduler, "prune_alert_history_if_due", adb.async_of(lambda: None))

        async def send():
            calls.append("send")

        monkeypatch.setattr(scheduler.notify, "send_pending", send)

        def failing_sync(**kwargs):
            calls.append("sync")
            raise RuntimeError("Nautobot down")

        monkeypatch.setattr(scheduler.inventory, "ensure_snapshot", adb.async_of(failing_sync))
        with pytest.raises(RuntimeError):
            asyncio.run(scheduler.tick())
        assert calls == ["send", "sync"]

        calls.clear()
        monkeypatch.setattr(
            scheduler.inventory,
            "ensure_snapshot",
            adb.async_of(lambda **kwargs: calls.append("sync") or True),
        )
        monkeypatch.setattr(
            scheduler.alerts,
            "build_alert_board_payload",
            adb.async_of(lambda **kwargs: calls.append("build") or {}),
        )
        assert asyncio.run(scheduler.tick()) is True
        assert calls == ["send", "sync", "build", "send"]

    def test_a_sending_error_does_not_stop_the_tick(self, monkeypatch, caplog):
        async def failing():
            return 1 / 0

        monkeypatch.setattr(scheduler.notify, "send_pending", failing)
        caplog.set_level(logging.WARNING)
        asyncio.run(scheduler.send_notifications())
        assert "Sending notifications failed" in caplog.text


class TestTestEndpoint:
    def test_needs_the_admin_role(self, client, channels):
        assert client.post("/api/v1/notifications/test").status_code == 403  # AUTH_MODE=disabled
        with auth_config(mode="header", operator_groups={"ops"}, admin_groups={"admins"}):
            operator = {"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"}
            assert client.post("/api/v1/notifications/test", headers=operator).status_code == 403
            admin = {"X-Forwarded-User": "ada", "X-Forwarded-Groups": "admins"}
            resp = client.post("/api/v1/notifications/test", headers=admin)
        assert resp.status_code == 200
        assert resp.json() == {"results": {"webhook": "ok", "teams": "ok", "email": "ok"}}
        assert json.loads(channels["posts"][0]["body"])["type"] == "test"

    def test_reports_errors_without_secrets(self, client, channels, monkeypatch, override_settings):
        override_settings(ALLOW_UNAUTHENTICATED_WRITES=True)

        def broken(url, **kwargs):
            raise httpx.ConnectError(f"cannot reach {url}")

        monkeypatch.setattr(notify, "_post", broken)
        resp = client.post("/api/v1/notifications/test")
        assert resp.status_code == 502
        results = resp.json()["results"]
        assert results["webhook"] == "error: ConnectError" and results["email"] == "ok"
        assert "secret-token" not in resp.text

    def test_no_channel(self, client, override_settings):
        override_settings(ALLOW_UNAUTHENTICATED_WRITES=True)
        override_settings(NOTIFY_WEBHOOK_URL="", NOTIFY_TEAMS_WEBHOOK_URL="", SMTP_HOST="")
        resp = client.post("/api/v1/notifications/test")
        assert (
            resp.status_code == 400
            and "NOTIFY_EMAIL_TO together with SMTP_HOST" in resp.json()["error"]
        )
        override_settings(NOTIFY_EMAIL_TO=["noc@example.com"])
        resp = client.post("/api/v1/notifications/test")
        assert (
            resp.json()["error"]
            == "NOTIFY_EMAIL_TO is set, but SMTP_HOST is not: email needs a mail server"
        )


class TestCustomerMail:
    """A status token's addresses get a customer-safe mail about its tenant's sites (#707)."""

    @pytest.fixture
    def smtp_only(self, channels, override_settings):
        """Mail on, no NOC channel: customers' mails need SMTP_HOST alone."""
        override_settings(NOTIFY_WEBHOOK_URL="", NOTIFY_TEAMS_WEBHOOK_URL="", NOTIFY_EMAIL_TO=[])
        return channels

    @staticmethod
    def token(name, tenant, emails, revoked=False, expires="2027-01-01T00:00:00Z", after=None):
        adb.sql(
            "INSERT INTO api_tokens (name, token_hash, prefix, role, tenant, notify_email, "
            "expires_at, revoked_at, notify_after_minutes) "
            "VALUES (%s, %s, 'nmt_x', 'status', %s, %s, %s, %s, %s)",
            (name, f"hash-{name}", tenant, emails, expires, NOW if revoked else None, after),
        )

    def test_only_the_active_tokens_of_the_sites_tenants(self, smtp_only):
        self.token("acme", "ACME", "it@acme.example, ops@acme.example")
        self.token("acme-2", "acme", "IT@acme.example")  # the same address: one mail
        self.token("old", "Acme", "old@acme.example", revoked=True)
        self.token("gone", "Acme", "gone@acme.example", expires="2026-01-01T00:00:00Z")
        self.token("other", "Globex", "it@globex.example")
        self.token("quiet", "Acme", "")
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        rows = outbox()
        assert {row["channel"] for row in rows} == {"customer_email"}
        assert sorted(row["recipient"] for row in rows) == ["it@acme.example", "ops@acme.example"]

    def test_noc_tenant_filter_is_not_the_customers(self, channels, override_settings):
        override_settings(NOTIFY_TENANTS={"other"})
        self.token("acme", "Acme", "it@acme.example")
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        assert [row["channel"] for row in outbox()] == ["customer_email"]

    def test_nothing_without_smtp(self, monkeypatch):
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: NOW)
        self.token("acme", "Acme", "it@acme.example")
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        assert outbox() == []

    def test_the_mail_is_customer_safe(self, smtp_only):
        self.token("acme", "Acme", "it@acme.example, ops@acme.example")
        adb.sql("INSERT INTO status_links (tenant_key, link_id) VALUES ('acme', 'lnk123')")
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": site()})
        assert asyncio.run(notify.send_pending()) == 2
        mails = sorted(smtp_only["mails"], key=lambda mail: mail["To"])
        assert [mail["To"] for mail in mails] == ["it@acme.example", "ops@acme.example"]
        mail = mails[0]
        assert mail["Subject"] == "🔴 Aarhus HQ: Outage"
        body = mail.get_body(("plain",)).get_content()
        assert "Havnegade 1" in body and "Customer: Acme" in body and "Status: Outage" in body
        assert "https://maps.example.com/status/lnk123" in body
        for internal in ("core01", "10.0.0.1", "INC-7", "/alerts", "Core device", "EMEA"):
            assert internal not in body, internal
        payload = outbox()[0]["payload_json"]
        assert "10.0.0.1" not in payload and "core01" not in payload

    def test_the_recovery_and_a_burst_per_address(self, smtp_only, override_settings):
        self.token("acme", "Acme", "it@acme.example")
        self.token("globex", "Globex", "it@globex.example")
        override_settings(NOTIFY_SUMMARY_THRESHOLD=2)
        sites = {f"loc-{n}": site(f"loc-{n}", f"Site {n}") for n in range(3)}
        sites["loc-g"] = site("loc-g", "Globex HQ", tenants=("Globex",))
        change({k: "ok" for k in sites}, {k: "critical" for k in sites}, sites)
        assert asyncio.run(notify.send_pending()) == 4
        by_to = {mail["To"]: mail for mail in smtp_only["mails"]}
        assert by_to["it@acme.example"]["Subject"] == "Your sites: 3 sites in alarm"
        assert by_to["it@globex.example"]["Subject"] == "🔴 Globex HQ: Outage"
        assert "Site 0" not in by_to["it@globex.example"].get_body(("plain",)).get_content()


class TestCustomerMailIsTheStatusPage:
    """A customer's mail says what their status page says about the site (#763)."""

    token = staticmethod(TestCustomerMail.token)

    @pytest.fixture
    def mail(self, channels, override_settings):
        override_settings(
            NOTIFY_WEBHOOK_URL="",
            NOTIFY_TEAMS_WEBHOOK_URL="",
            NOTIFY_EMAIL_TO=[],
            # The page names the tenant's devices (#555): so does the mail.
            STATUS_PAGE_TENANT_DEVICES=True,
        )
        return channels

    @staticmethod
    def shared_site():
        """Acme's and Globex's site: each with its own devices and counts."""
        return site(
            tenants=("Acme", "Globex"),
            description="Main office",
            network_device_count=10,
            down_network_device_count=2,
            access_point_count=4,
            tenant_alerts={
                "acme": {
                    "alert_level": "critical",
                    "device_count": 3,
                    "network_device_count": 3,
                    "down_network_device_count": 1,
                    "current_downtime_seconds": 300,
                },
                "globex": {"alert_level": "ok", "device_count": 7, "network_device_count": 7},
            },
            tenant_devices={
                "acme": [{"name": "acme-rtr", "state": "down", "since": "2026-10-09T11:55:00Z"}],
                "globex": [{"name": "globex-sw", "state": "up", "since": None}],
            },
        )

    @staticmethod
    def body(mails, to):
        mail = next(mail for mail in mails if mail["To"] == to)
        return mail["Subject"], mail.get_body(("plain",)).get_content()

    def test_the_entry_is_the_pages(self, mail):
        self.token("acme", "Acme", "it@acme.example")
        row = self.shared_site()
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": row})
        event = json.loads(outbox()[0]["payload_json"])
        page = status.tenant_site({**row, "alert_level": "critical"}, "Acme", NOW)
        assert event["status"] == [{"tenant": "Acme", **page}]
        assert page["state"] == "outage" and page["devices"][0]["name"] == "acme-rtr"

    def test_the_tenants_devices_by_name_never_anothers(self, mail):
        self.token("acme", "Acme", "it@acme.example")
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": self.shared_site()})
        assert asyncio.run(notify.send_pending()) == 1
        subject, body = self.body(mail["mails"], "it@acme.example")
        assert subject == "🔴 Aarhus HQ: Outage"
        assert "Status: Outage since 2026-10-09 11:55 UTC" in body
        assert "About: Main office" in body
        assert "Devices: 1 of 3 network devices down" in body
        assert "acme-rtr: down since 2026-10-09 11:55 UTC" in body
        for other in ("globex-sw", "Globex", "10 network devices", "core01", "10.0.0.1", "INC-7"):
            assert other not in body, other

    def test_no_names_where_the_page_shows_none(self, mail, override_settings):
        override_settings(STATUS_PAGE_TENANT_DEVICES=False)
        self.token("acme", "Acme", "it@acme.example")
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": self.shared_site()})
        assert asyncio.run(notify.send_pending()) == 1
        _, body = self.body(mail["mails"], "it@acme.example")
        assert "Devices: 1 of 3 network devices down" in body and "acme-rtr" not in body

    def test_the_sites_circuits(self, mail):
        self.token("acme", "Acme", "it@acme.example")
        circuit = {
            "circuit_id": "c1",
            "cid": "DK-TEL-1",
            "provider": "Telia Carrier",
            "circuit_type": "Internet Transit",
            "status": "Active",
            "tenant": "Acme",
            "commit_rate": 1_000_000,
            "description": "",
            "address_key": "",
            "terminated": True,
            "location_ids": ["loc-1"],
        }
        elsewhere = {**circuit, "circuit_id": "c2", "cid": "DK-TEL-2", "location_ids": ["loc-9"]}
        adb.call(inventory.write_circuits, [circuit, elsewhere])
        change({"loc-1": "ok"}, {"loc-1": "critical"}, {"loc-1": self.shared_site()})
        assert asyncio.run(notify.send_pending()) == 1
        _, body = self.body(mail["mails"], "it@acme.example")
        assert "Circuit: DK-TEL-1 · Internet Transit · 1 Gbps" in body
        assert "DK-TEL-2" not in body and "Telia" not in body

    def test_the_summary_has_the_counts(self, mail, override_settings):
        override_settings(NOTIFY_SUMMARY_THRESHOLD=2)
        self.token("acme", "Acme", "it@acme.example")
        sites = {
            f"loc-{n}": site(
                f"loc-{n}", f"Site {n}", network_device_count=4, down_network_device_count=4
            )
            for n in range(3)
        }
        change({k: "ok" for k in sites}, {k: "critical" for k in sites}, sites)
        assert asyncio.run(notify.send_pending()) == 3  # rows, in one mail
        assert len(mail["mails"]) == 1
        _, body = self.body(mail["mails"], "it@acme.example")
        assert "🔴 Site 0: Outage (2026-10-09 12:00 UTC) · 4 of 4 network devices down" in body

    def test_a_mail_queued_before_still_reads(self):
        """No "status" in its payload (#763): the board's level, in the page's words."""
        event = {
            "type": "alarm",
            "level": "medium",
            "previous_level": "ok",
            "changed_at": NOW,
            "site": {"id": "loc-1", "name": "Aarhus HQ", "address": ""},
            "tenants": ["Acme"],
            "status_pages": [],
        }
        assert notify.customer_title(event) == "🔴 Aarhus HQ: Degraded"
        assert "Status: Degraded" in notify.customer_lines(event)


class TestCustomerMailWait:
    """A customer's alarm mail waits the token's minutes (#729); one per outage, and a
    recovery only after an alarm that went out."""

    token = staticmethod(TestCustomerMail.token)

    @pytest.fixture
    def mail(self, channels, monkeypatch, override_settings):
        override_settings(NOTIFY_WEBHOOK_URL="", NOTIFY_TEAMS_WEBHOOK_URL="", NOTIFY_EMAIL_TO=[])
        self.monkeypatch = monkeypatch
        self.level("ok", "2026-10-09T11:00:00Z")
        return channels

    @staticmethod
    def level(level, at):
        asyncio.run(alerts.record_site_level_changes({"loc-1": ("", level)}, at, {"loc-1": site()}))

    def send_at(self, at):
        self.monkeypatch.setattr(timeutil, "iso_utc_now", lambda: at)
        return asyncio.run(notify.send_pending())

    @staticmethod
    def mails():
        return [(row["recipient"], row["kind"], row["status"]) for row in outbox()]

    def test_back_up_within_the_wait_no_mail(self, mail):
        self.token("acme", "Acme", "it@acme.example", after=15)
        self.level("critical", "2026-10-09T12:00:00Z")
        assert self.send_at("2026-10-09T12:14:00Z") == 0
        self.level("ok", "2026-10-09T12:10:00Z")
        assert self.send_at("2026-10-09T13:00:00Z") == 0
        assert mail["mails"] == []
        assert self.mails() == [("it@acme.example", "alarm", "cancelled")]

    def test_into_maintenance_within_the_wait_no_mail(self, mail):
        self.token("acme", "Acme", "it@acme.example", after=15)
        self.level("critical", "2026-10-09T12:00:00Z")
        self.level("maintenance", "2026-10-09T12:05:00Z")
        assert self.send_at("2026-10-09T13:00:00Z") == 0
        assert self.mails() == [("it@acme.example", "alarm", "cancelled")]

    def test_down_longer_one_alarm_then_the_recovery(self, mail, override_settings):
        override_settings(NOTIFY_MIN_LEVEL="medium")
        self.token("acme", "Acme", "it@acme.example", after=15)
        self.level("medium", "2026-10-09T12:00:00Z")
        assert self.send_at("2026-10-09T12:14:59Z") == 0
        assert self.send_at("2026-10-09T12:15:00Z") == 1
        assert mail["mails"][0]["Subject"].endswith("Aarhus HQ: Degraded")
        # Worse while down: no second alarm.
        self.level("critical", "2026-10-09T12:20:00Z")
        self.level("ok", "2026-10-09T13:00:00Z")
        assert self.send_at("2026-10-09T13:00:00Z") == 1
        assert self.mails() == [
            ("it@acme.example", "alarm", "sent"),
            ("it@acme.example", "recovery", "sent"),
        ]
        # Down again after the recovery: a new alarm, waiting again.
        self.level("critical", "2026-10-09T14:00:00Z")
        assert self.mails()[-1] == ("it@acme.example", "alarm", "pending")
        assert outbox()[-1]["next_attempt_at"] == "2026-10-09T14:15:00Z"

    def test_still_waiting_when_it_gets_worse(self, mail, override_settings):
        """A worse level while the alarm waits: still the one alarm, at its time."""
        override_settings(NOTIFY_MIN_LEVEL="medium")
        self.token("acme", "Acme", "it@acme.example", after=30)
        self.level("medium", "2026-10-09T12:00:00Z")
        self.level("critical", "2026-10-09T12:10:00Z")
        assert self.mails() == [("it@acme.example", "alarm", "pending")]
        assert self.send_at("2026-10-09T12:30:00Z") == 1

    def test_each_address_waits_its_own(self, mail):
        self.token("fast", "Acme", "noc@acme.example, both@acme.example", after=5)
        self.token("slow", "Acme", "boss@acme.example, Both@acme.example", after=30)
        self.token("desk", "Acme", "desk@acme.example", after=10)
        self.level("critical", "2026-10-09T12:00:00Z")
        due = {row["recipient"].lower(): row["next_attempt_at"] for row in outbox()}
        assert due == {
            "noc@acme.example": "2026-10-09T12:05:00Z",
            # On both tokens: the shorter wait.
            "both@acme.example": "2026-10-09T12:05:00Z",
            "boss@acme.example": "2026-10-09T12:30:00Z",
            "desk@acme.example": "2026-10-09T12:10:00Z",
        }

    def test_at_once_by_default(self, mail):
        self.token("acme", "Acme", "it@acme.example")
        self.level("critical", "2026-10-09T12:00:00Z")
        assert self.send_at("2026-10-09T12:00:00Z") == 1

    def test_at_once_beside_a_wait(self, mail):
        """Saved without a wait (null) on one token, 30 minutes on another: at once."""
        self.token("old", "Acme", "it@acme.example")
        self.token("slow", "Acme", "it@acme.example", after=30)
        self.level("critical", "2026-10-09T12:00:00Z")
        assert outbox()[-1]["next_attempt_at"] == "2026-10-09T12:00:00Z"

    def test_no_recovery_for_an_alarm_never_sent(self, mail):
        """Back up before the alarm went out at once (the next send hadn't run): neither."""
        self.token("acme", "Acme", "it@acme.example")
        self.level("critical", "2026-10-09T12:00:00Z")
        self.level("ok", "2026-10-09T12:00:30Z")
        assert self.send_at("2026-10-09T12:01:00Z") == 0
        assert self.mails() == [("it@acme.example", "alarm", "cancelled")]

    def test_staff_channels_dont_wait(self, mail, override_settings):
        override_settings(NOTIFY_WEBHOOK_URL=HOOK)
        self.token("acme", "Acme", "it@acme.example", after=15)
        self.level("critical", "2026-10-09T12:00:00Z")
        self.level("ok", "2026-10-09T12:05:00Z")
        assert self.send_at("2026-10-09T12:06:00Z") == 2
        assert [r for r in self.mails() if r[0] == ""] == [
            ("", "alarm", "sent"),
            ("", "recovery", "sent"),
        ]
