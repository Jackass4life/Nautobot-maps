"""Maintenance windows (#283): planned work is not an alarm."""

import csv
import json

import pytest

from nautobot_maps import alerts, caching, db, inventory, maintenance, notify, settings, timeutil
from tests import http_client
from tests.test_app import _snapshot_device, _snapshot_location, auth_config

NOW = "2026-10-09T12:00:00Z"


@pytest.fixture
def clock(monkeypatch):
    """A clock the test can move: ``clock["now"] = "..."``."""
    state = {"now": NOW}
    monkeypatch.setattr(timeutil, "iso_utc_now", lambda: state["now"])
    return state


@pytest.fixture
def site(pg_database, monkeypatch, clock):
    """London: a core router and two access switches; the core and one switch are down."""
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    monkeypatch.setattr(inventory, "ensure_snapshot", lambda *args, **kwargs: False)
    set_devices(core="Offline", acc1="Offline", acc2="Active")
    return "loc-lon"


def set_devices(**statuses):
    roles = {"core": "Core Router", "acc1": "Access Switch", "acc2": "Access Switch"}
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            inventory.write_locations(conn, [_snapshot_location("loc-lon", "London HQ")])
            # A completed sync of the current cache: alert history is written.
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_completed_at=NOW,
                last_successful_sync=NOW,
                cache_version=inventory.CACHE_VERSION,
            )
            inventory.write_devices(
                conn, [_snapshot_device(name, "loc-lon", roles[name], status) for name, status in statuses.items()]
            )
    finally:
        conn.close()


def build() -> dict:
    caching.cache.clear()
    payload = alerts.build_alert_board_payload(snapshot_only=True)
    (row,) = payload["alerts"]
    return {**row, "summary": payload["summary"]}


def open_alerts() -> dict:
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT device_id, status FROM alert_instances ORDER BY id").fetchall()
        return {row["device_id"]: row["status"] for row in map(db.row_to_dict, rows)}
    finally:
        conn.close()


def window(**body) -> list[dict]:
    conn = db.get_conn()
    try:
        return maintenance.create(conn, {"site_id": "loc-lon", "reason": "Core upgrade", **body}, "olga")
    finally:
        conn.close()


@pytest.mark.usefixtures("pg_database")
class TestWindows:
    @pytest.fixture(autouse=True)
    def _clock(self, clock):
        self.clock = clock

    @pytest.mark.parametrize(
        ("body", "error"),
        [
            ({"reason": "x", "duration_minutes": 60}, "site_id is required"),
            ({"site_id": "s", "duration_minutes": 60}, "reason is required"),
            ({"site_id": "s", "reason": "x"}, "ends_at or duration_minutes is required"),
            ({"site_id": "s", "reason": "x", "duration_minutes": 0}, "positive whole number"),
            ({"site_id": "s", "reason": "x", "ends_at": "2026-10-09T13:00:00"}, "needs a time zone"),
            ({"site_id": "s", "reason": "x", "ends_at": "tomorrow"}, "ISO-8601"),
            ({"site_id": "s", "reason": "x", "ends_at": "2026-10-09T11:00:00Z"}, "after starts_at"),
            (
                {"site_id": "s", "reason": "x", "starts_at": "2026-10-09T08:00:00Z", "ends_at": "2026-10-09T09:00Z"},
                "already ended",
            ),
            ({"site_id": "s", "reason": "x", "duration_minutes": 15 * 24 * 60}, "at most 14 days"),
            ({"site_id": "s", "reason": "x", "duration_minutes": 60, "device_ids": "core"}, "device_ids"),
            ({"site_id": "s", "reason": "x" * 201, "duration_minutes": 60}, "longer than 200"),
            # Would overflow a timedelta: a clean 400, not a 500 (Copilot on #303).
            ({"site_id": "s", "reason": "x", "duration_minutes": 10**100}, "at most 14 days"),
            # Several sites at once (#343).
            ({"site_ids": [], "reason": "x", "duration_minutes": 60}, "site_ids must be a list"),
            ({"site_ids": "s1", "reason": "x", "duration_minutes": 60}, "site_ids must be a list"),
            ({"site_ids": ["s1", " "], "reason": "x", "duration_minutes": 60}, "site_ids must be a list"),
            ({"site_ids": [f"s{i}" for i in range(201)], "reason": "x", "duration_minutes": 60}, "At most 200 sites"),
            (
                {"site_ids": ["s1", "s2"], "reason": "x", "duration_minutes": 60, "device_ids": ["d1"]},
                "one site at a time",
            ),
        ],
    )
    def test_invalid_requests(self, body, error):
        conn = db.get_conn()
        try:
            with pytest.raises(maintenance.WindowError, match=error):
                maintenance.create(conn, body, "olga")
        finally:
            conn.close()

    def test_now_planned_device_windows_and_their_states(self):
        (site_window,) = window(duration_minutes=120)
        assert site_window["device_id"] == "" and site_window["state"] == "active"
        assert site_window["starts_at"].startswith("2026-10-09T12:00:00") and site_window["created_by"] == "olga"
        planned = window(
            starts_at="2026-10-09T22:00:00+02:00", ends_at="2026-10-10T02:00:00+02:00", device_ids=["a", "b"]
        )
        assert [w["device_id"] for w in planned] == ["a", "b"] and {w["state"] for w in planned} == {"upcoming"}
        assert planned[0]["starts_at"].startswith("2026-10-09T20:00:00"), "stored in UTC"

        conn = db.get_conn()
        try:
            active = maintenance.read_active(conn, maintenance.now())
            assert set(active["sites"]) == {"loc-lon"} and active["devices"] == {}
            cancelled = maintenance.end(conn, planned[0]["id"], "ada")
            assert cancelled["state"] == "cancelled" and cancelled["ended_by"] == "ada"
            ended = maintenance.end(conn, site_window["id"], "ada")
            assert ended["state"] == "ended"
            assert maintenance.end(conn, 999, "ada") is None
            assert [w["id"] for w in maintenance.list_windows(conn)] == [planned[1]["id"]]
            assert len(maintenance.list_windows(conn, include_past=True)) == 3
            self.clock["now"] = "2026-10-09T19:30:00Z"  # before the planned start (20:00 UTC)
            assert maintenance.read_active(conn, maintenance.now())["devices"] == {}
            self.clock["now"] = "2026-10-09T21:00:00Z"
            assert set(maintenance.read_active(conn, maintenance.now())["devices"]) == {"b"}
        finally:
            conn.close()


class TestBoard:
    def test_site_in_maintenance_shows_maintenance_and_freezes_history(self, site):
        row = build()
        assert row["alert_level"] == "critical"
        assert open_alerts() == {"core": "open", "acc1": "open"}

        window(duration_minutes=120)
        set_devices(core="Active", acc1="Offline", acc2="Offline")  # core back, acc2 down
        row = build()
        assert row["alert_level"] == "maintenance"
        assert row["maintenance"] == {"until": "2026-10-09T14:00:00Z", "reason": "Core upgrade", "by": "olga"}
        assert row["summary"]["maintenance"] == 1 and row["summary"]["non_ok"] == 0
        # Frozen: the core's alert didn't resolve, acc2 didn't open one.
        assert open_alerts() == {"core": "open", "acc1": "open"}

    def test_device_in_maintenance_is_left_out_of_the_level(self, site):
        build()
        conn = db.get_conn()
        try:
            maintenance.create(
                conn, {"site_id": "loc-lon", "reason": "Swap", "duration_minutes": 60, "device_ids": ["core"]}, "olga"
            )
        finally:
            conn.close()
        set_devices(core="Active", acc1="Offline", acc2="Active")
        row = build()
        # Without the core: 1 of 2 down is Medium, not Critical.
        assert row["alert_level"] == "medium" and row["down_device_count"] == 1 and row["device_count"] == 2
        assert row["maintenance_device_count"] == 1, "so the board still offers the panel to end it"
        # The core's open alert is kept, not resolved, and listed as in maintenance.
        assert open_alerts() == {"core": "open", "acc1": "open"}
        core = next(d for d in row["down_devices"] if d["device_id"] == "core")
        assert core["maintenance_until"] == "2026-10-09T13:00:00Z" and core["maintenance_reason"] == "Swap"
        assert core["role"] == "Core Router" and core["device_ip"] == "10.0.0.1" and core["status"] == "Active"

    def test_a_device_going_down_in_maintenance_is_listed_not_counted(self, site):
        set_devices(core="Active", acc1="Active", acc2="Active")
        build()
        conn = db.get_conn()
        try:
            maintenance.create(
                conn, {"site_id": "loc-lon", "reason": "Swap", "duration_minutes": 60, "device_ids": ["acc2"]}, "olga"
            )
        finally:
            conn.close()
        set_devices(core="Active", acc1="Active", acc2="Offline")
        row = build()
        assert row["alert_level"] == "ok" and row["down_device_count"] == 0
        assert [d["device_id"] for d in row["down_devices"]] == ["acc2"] and open_alerts() == {}
        assert (
            row["down_devices"][0]["role"] == "Access Switch" and row["down_devices"][0]["maintenance_reason"] == "Swap"
        )

    def test_unreadable_windows_write_nothing(self, site, monkeypatch):
        """Copilot on #303: don't treat "couldn't read" as "no windows"."""
        build()
        build()

        def broken(conn, at):
            raise RuntimeError("permission denied")

        monkeypatch.setattr(maintenance, "read_active", broken)
        monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "https://hooks.example.com/x")
        set_devices(core="Active", acc1="Active", acc2="Active")  # everything recovered
        build()
        assert open_alerts() == {"core": "open", "acc1": "open"}, "nothing resolved"
        assert outbox_kinds() == [], "no level change recorded, nothing notified"

    def test_a_device_gone_from_the_inventory_keeps_its_frozen_alert(self, site):
        """Copilot on #303: frozen ids come from the windows, not the inventory."""
        build()
        conn = db.get_conn()
        try:
            maintenance.create(
                conn, {"site_id": "loc-lon", "reason": "Swap", "duration_minutes": 60, "device_ids": ["core"]}, "olga"
            )
            conn.execute("DELETE FROM nautobot_device_cache WHERE device_id = 'core'")
        finally:
            conn.close()
        build()
        assert open_alerts()["core"] == "open"

    def test_healthy_sites_write_no_history(self, site, monkeypatch):
        """The bulk read decides who needs a write (#149); a maintenance check once broke that."""
        set_devices(core="Active", acc1="Active", acc2="Active")
        build()
        calls = []
        monkeypatch.setattr(alerts, "upsert_alert_lifecycle_for_site", lambda *a, **k: calls.append(1) or True)
        build()
        assert calls == []

    def test_map_shows_maintenance(self, site):
        window(duration_minutes=30)
        levels = alerts.build_location_alert_levels()["levels"]
        assert levels["loc-lon"]["level"] == "maintenance" and levels["loc-lon"]["reason"] == "Core upgrade"


class TestNotifications:
    def test_quiet_into_maintenance_alarm_if_still_critical_after(self, site, clock, monkeypatch):
        monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "https://hooks.example.com/x")
        build()
        build()  # the first build only recorded the level
        (created,) = window(duration_minutes=60)
        build()
        assert outbox_kinds() == [], "going into maintenance sends nothing"
        conn = db.get_conn()
        try:
            maintenance.end(conn, created["id"], "olga")
        finally:
            conn.close()
        assert build()["alert_level"] == "critical"
        assert outbox_kinds() == [("alarm", "maintenance", "critical")], "still Critical when it ends"

    def test_a_device_window_that_lowers_the_level_is_quiet(self, site, monkeypatch):
        """Copilot on #303: the only failed devices going into maintenance is no "recovery"."""
        monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "https://hooks.example.com/x")
        set_devices(core="Offline", acc1="Active", acc2="Active")
        build()
        build()
        conn = db.get_conn()
        try:
            (created,) = maintenance.create(
                conn, {"site_id": "loc-lon", "reason": "Swap", "duration_minutes": 60, "device_ids": ["core"]}, "olga"
            )
        finally:
            conn.close()
        assert build()["alert_level"] == "ok"
        assert outbox_kinds() == [], "planned, not a recovery"
        conn = db.get_conn()
        try:
            maintenance.end(conn, created["id"], "olga")
        finally:
            conn.close()
        build()
        assert outbox_kinds() == [("alarm", "ok", "critical")], "still down when it ends"

    def test_rule(self, monkeypatch):
        monkeypatch.setattr(settings, "NOTIFY_MIN_LEVEL", "critical")
        assert notify.event_kind("critical", "maintenance") is None
        assert notify.event_kind("maintenance", "critical") == "alarm"
        assert notify.event_kind("maintenance", "ok") is None


def outbox_kinds() -> list:
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT kind, payload_json FROM notification_outbox ORDER BY id").fetchall()
        result = []
        for row in map(db.row_to_dict, rows):
            payload = json.loads(row["payload_json"])
            result.append((row["kind"], payload["previous_level"], payload["level"]))
        return result
    finally:
        conn.close()


class TestCache:
    def test_cache_expires_at_the_next_window_change(self, pg_database, clock):
        """Copilot on #303: a planned window shows when it starts, not up to CACHE_TTL later."""

        def next_change():
            conn = db.get_conn()
            try:
                return maintenance.read_active(conn, maintenance.now())["next_change"]
            finally:
                conn.close()

        assert maintenance.cache_seconds(next_change(), 300) == 300, "no windows"
        window(starts_at="2026-10-09T12:01:30Z", ends_at="2026-10-09T13:00:00Z")
        assert maintenance.cache_seconds(next_change(), 300) == 91
        clock["now"] = "2026-10-09T12:59:59Z"
        assert maintenance.cache_seconds(next_change(), 300) == 2, "until it ends"
        clock["now"] = "2026-10-09T14:00:00Z"
        assert maintenance.cache_seconds(next_change(), 300) == 300

    def test_the_board_carries_the_next_change(self, site):
        window(starts_at="2026-10-09T12:10:00Z", duration_minutes=30)
        payload = alerts.build_alert_board_payload(snapshot_only=True)
        assert payload["next_maintenance_change"].startswith("2026-10-09T12:10:00")


class TestSiteDevices:
    def test_monitored_devices_of_a_site(self, site, monkeypatch):
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                no_ip = {**_snapshot_device("no-ip", "loc-lon", "Access Switch", "Active"), "primary_ip": ""}
                inventory.write_devices(conn, [no_ip, _snapshot_device("old", "loc-lon", "Access Switch", "Planned")])
        finally:
            conn.close()
        monkeypatch.setattr(settings, "ALERT_BOARD_EXCLUDED_DEVICE_STATUSES", {"planned"})
        devices = alerts.site_monitored_devices("loc-lon")
        assert [d["id"] for d in devices] == ["acc1", "acc2", "core"], "no primary IP or excluded: not offered"
        assert devices[2] == {
            "id": "core",
            "name": "core",
            "role": "Core Router",
            "status": "Offline",
            "location_path": "",
        }

    def test_rolled_up_site_includes_devices_below_it(self, site, monkeypatch):
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                building = {**_snapshot_location("bld-a", "Bygning A", "Building"), "parent_id": "loc-lon"}
                inventory.write_locations(conn, [_snapshot_location("loc-lon", "London HQ", "Site"), building])
                inventory.write_devices(conn, [_snapshot_device("floor-sw", "bld-a", "Access Switch", "Active")])
        finally:
            conn.close()
        monkeypatch.setattr(settings, "ALERT_BOARD_SITE_LOCATION_TYPE", "site")
        devices = {d["id"]: d for d in alerts.site_monitored_devices("loc-lon")}
        assert "floor-sw" in devices and devices["floor-sw"]["location_path"] == "Bygning A"

    def test_not_below_an_excluded_location_unless_shown_on_the_board(self, site, monkeypatch):
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                closed = {**_snapshot_location("bld-old", "Old Building", "Building"), "parent_id": "loc-lon"}
                inventory.write_locations(conn, [_snapshot_location("loc-lon", "London HQ", "Site"), closed])
                inventory.write_devices(conn, [_snapshot_device("old-sw", "bld-old", "Access Switch", "Active")])
        finally:
            conn.close()
        monkeypatch.setattr(settings, "ALERT_BOARD_SITE_LOCATION_TYPE", "site")
        monkeypatch.setattr(settings, "ALERT_BOARD_EXCLUDED_LOCATION_NAMES", {"old building"})
        assert "old-sw" not in {d["id"] for d in alerts.site_monitored_devices("loc-lon")}
        with http_client.FlaskStyleClient() as client:
            body = client.get("/api/maintenance/devices?site_id=loc-lon&include_non_operational=1").get_json()
        assert "old-sw" in {d["id"] for d in body["devices"]}

    def test_endpoint(self, site):
        with http_client.FlaskStyleClient() as client:
            assert client.get("/api/maintenance/devices").status_code == 400
            body = client.get("/api/maintenance/devices?site_id=loc-lon").get_json()
        assert [d["id"] for d in body["devices"]] == ["acc1", "acc2", "core"]


@pytest.mark.usefixtures("pg_database")
class TestBatches:
    """A change across many sites is one request, one transaction, one batch (#343)."""

    @pytest.fixture(autouse=True)
    def _clock(self, clock):
        self.clock = clock

    def _create(self, body):
        conn = db.get_conn()
        try:
            return maintenance.create(conn, {"reason": "Core upgrade", "duration_minutes": 120, **body}, "olga")
        finally:
            conn.close()

    def test_thirty_sites_in_one_request(self):
        sites = [f"site-{i:02d}" for i in range(30)] + ["site-00"]  # a repeat counts once
        created = self._create({"site_ids": sites})
        assert [w["site_id"] for w in created] == sorted(set(sites))
        assert {w["device_id"] for w in created} == {""} and {w["state"] for w in created} == {"active"}
        # One request, one stored batch (and one transaction: the same created_at).
        assert len({w["created_at"] for w in created}) == 1
        assert len({w["batch_id"] for w in created}) == 1 and created[0]["batch_id"]
        # One site's devices, as before.
        devices = self._create({"site_id": "site-00", "device_ids": ["d1", "d2"]})
        assert [(w["site_id"], w["device_id"]) for w in devices] == [("site-00", "d1"), ("site-00", "d2")]
        assert devices[0]["batch_id"] == devices[1]["batch_id"] != created[0]["batch_id"]

    def test_end_or_cancel_a_batch(self):
        active = self._create({"site_ids": ["a", "b"]})
        planned = self._create({"site_ids": ["c"], "starts_at": "2026-10-10T08:00:00Z", "duration_minutes": 60})
        conn = db.get_conn()
        try:
            result = maintenance.end_many(conn, [w["id"] for w in active + planned] + [999], "ivan")
            ended = result["windows"]
            assert result["changed"] == 3
            # The ended time returned is the one stored.
            assert ended[0]["ended_at"] == "2026-10-09T12:00:00Z"
            assert [(w["site_id"], w["state"], w["ended_by"]) for w in ended] == [
                ("a", "ended", "ivan"),
                ("b", "ended", "ivan"),
                ("c", "cancelled", "ivan"),
            ]
            # Already over: returned as they are, not ended again.
            again = maintenance.end_many(conn, [active[0]["id"]], "someone")
            assert again["windows"][0]["ended_by"] == "ivan" and again["changed"] == 0
        finally:
            conn.close()

    def test_open_windows_are_never_crowded_out(self):
        # A week of ended 200-site batches must not push tonight's work past the limit (review on #343).
        conn = db.get_conn()
        try:
            for day in range(3):
                old = self._create({"site_ids": [f"old-{day}-{i}" for i in range(5)], "duration_minutes": 60})
                maintenance.end_many(conn, [w["id"] for w in old], "olga")
            fresh = self._create({"site_ids": ["tonight"], "starts_at": "2026-10-09T20:00:00Z", "duration_minutes": 60})
            listed = maintenance.list_windows(conn, recent_days=7, limit=3)
        finally:
            conn.close()
        assert listed[0]["id"] == fresh[0]["id"] and listed[0]["state"] == "upcoming"

    def test_recent_windows_and_names(self):
        old = self._create({"site_ids": ["gone"], "duration_minutes": 60})
        current = self._create({"site_ids": ["site-a"]})
        device = self._create({"site_id": "site-a", "device_ids": ["dev-1"]})
        conn = db.get_conn()
        try:
            maintenance.end_many(conn, [old[0]["id"]], "olga")
            self.clock["now"] = "2026-10-12T12:00:00Z"  # three days later: all over
            assert maintenance.list_windows(conn) == []
            recent = maintenance.list_windows(conn, recent_days=7)
            assert {w["site_id"] for w in recent} == {"gone", "site-a"}
            assert maintenance.list_windows(conn, recent_days=2) == []
            inventory.write_devices(conn, [_snapshot_device("dev-1", "site-a", "Core Router", "Active")])
            sites = [
                {
                    "id": "site-a",
                    "name": "Aarhus",
                    "ancestor_path": "EMEA › DNK",
                    "tenants": ["Acme"],
                    "tenant_descriptions": {"Acme": "Gold"},
                }
            ]
            described = maintenance.describe(conn, current + device, sites)
        finally:
            conn.close()
        whole, dev = described["windows"]
        assert (whole["site_name"], whole["site_path"], whole["tenants"], whole["device_name"]) == (
            "Aarhus",
            "EMEA › DNK",
            ["Acme"],
            "",
        )
        assert dev["device_name"] == "dev-1"
        assert described["tenant_descriptions"] == {"Acme": "Gold"}


@pytest.mark.usefixtures("pg_database")
class TestHistory:
    """Every window that overlapped a period, newest first, and its export (#353)."""

    @pytest.fixture(autouse=True)
    def _clock(self, clock):
        self.clock = clock

    def _create(self, **body):
        conn = db.get_conn()
        try:
            return maintenance.create(conn, {"reason": "Work", **body}, "olga")
        finally:
            conn.close()

    def _history(self, start, end, site_id=""):
        conn = db.get_conn()
        try:
            return maintenance.history(
                conn,
                timeutil.parse_iso_datetime(start),
                timeutil.parse_iso_datetime(end),
                [site_id] if site_id else None,
            )
        finally:
            conn.close()

    def test_overlap_state_and_minutes(self):
        # Now is 2026-10-09 12:00.
        ran = self._create(site_ids=["a", "b"], starts_at="2026-10-09T12:00:00Z", duration_minutes=60)
        cancelled = self._create(site_id="c", starts_at="2026-10-09T20:00:00Z", duration_minutes=60)
        self.clock["now"] = "2026-10-09T12:30:00Z"
        conn = db.get_conn()
        try:
            maintenance.end_many(conn, [ran[0]["id"], cancelled[0]["id"]], "ivan")  # a ends early, c is cancelled
        finally:
            conn.close()
        self.clock["now"] = "2026-10-10T12:00:00Z"
        later = self._create(site_id="a", device_ids=["d1"], starts_at="2026-10-12T08:00:00Z", duration_minutes=30)

        day = self._history("2026-10-09T00:00:00Z", "2026-10-10T00:00:00Z")
        assert [(w["site_id"], w["state"]) for w in day] == [("c", "cancelled"), ("b", "ended"), ("a", "ended")]
        at = maintenance.now()
        assert {w["site_id"]: maintenance.history_minutes(w, at) for w in day} == {"a": 30, "b": 60, "c": 0}
        # Ended early before a period: it didn't run in it; cancelled: shown where it was planned
        # (review on #353).
        late = self._history("2026-10-09T12:45:00Z", "2026-10-09T23:00:00Z")
        assert [(w["site_id"], w["state"]) for w in late] == [("c", "cancelled"), ("b", "ended")]
        # A window outside the period is not in it; one site's history.
        assert later[0]["id"] not in {w["id"] for w in day}
        assert [w["id"] for w in self._history("2026-10-01T00:00:00Z", "2026-10-20T00:00:00Z", "a")] == [
            later[0]["id"],
            ran[0]["id"],
        ]

    def test_export(self):
        self._create(site_id="=cmd", device_ids=["d1"], reason="=HYPERLINK()", duration_minutes=90)
        # Streamed from a server-side cursor, the device name joined in (#353).
        inventory_conn = db.get_conn()
        try:
            inventory.write_devices(inventory_conn, [_snapshot_device("d1", "=cmd", "Core Router", "Active")])
        finally:
            inventory_conn.close()
        conn = db.get_conn()
        try:
            text = "".join(
                maintenance.history_csv_lines(
                    conn,
                    timeutil.parse_iso_datetime("2026-10-09T00:00:00Z"),
                    timeutil.parse_iso_datetime("2026-10-10T00:00:00Z"),
                    None,
                    [{"id": "=cmd", "name": "Oslo", "tenants": ["Acme"]}],
                )
            )
            # Over the limit is a probe, not a read.
            assert maintenance.history_exceeds(
                conn,
                timeutil.parse_iso_datetime("2026-10-09T00:00:00Z"),
                timeutil.parse_iso_datetime("2026-10-10T00:00:00Z"),
                None,
                0,
            )
        finally:
            conn.close()
        lines = text.lstrip("\ufeff").splitlines()
        assert lines[0].split(",") == list(maintenance.HISTORY_COLUMNS)
        row = next(csv.reader(lines[1:]))
        values = dict(zip(maintenance.HISTORY_COLUMNS, row, strict=True))
        assert values["site"] == "Oslo" and values["tenants"] == "Acme" and values["device"] == "d1"
        assert values["state"] == "active" and values["planned_start_utc"] == "2026-10-09 12:00"
        assert (
            values["planned_end_utc"] == "2026-10-09 13:30"
            and values["ended_at_utc"] == ""
            and values["minutes"] == "0"
        )
        assert values["reason"] == "'=HYPERLINK()" and values["site_id"] == "'=cmd", "formula-safe"
        assert values["planned_by"] == "olga" and values["batch"]


class TestApi:
    @pytest.fixture
    def client(self, site):
        caching.cache.clear()
        with http_client.FlaskStyleClient() as test_client:
            yield test_client

    def test_operator_creates_lists_and_ends(self, client):
        body = {"site_id": "loc-lon", "reason": "Core upgrade", "duration_minutes": 90}
        assert client.post("/api/maintenance", json=body).status_code == 403  # AUTH_MODE=disabled
        with auth_config(mode="header", viewer_groups={"noc"}, operator_groups={"ops"}):
            viewer = {"X-Forwarded-User": "vera", "X-Forwarded-Groups": "noc"}
            operator = {"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"}
            assert client.post("/api/maintenance", json=body, headers=viewer).status_code == 403
            assert client.get("/api/alerts", headers=viewer).get_json()["alerts"][0]["alert_level"] == "critical"
            created = client.post("/api/maintenance", json=body, headers=operator)
            assert created.status_code == 201
            (window_row,) = created.get_json()["windows"]
            assert window_row["created_by"] == "olga" and window_row["state"] == "active"
            # The cached board was dropped: maintenance shows at once.
            assert client.get("/api/alerts", headers=viewer).get_json()["alerts"][0]["alert_level"] == "maintenance"
            listed = client.get("/api/maintenance?site_id=loc-lon", headers=viewer).get_json()["windows"]
            assert [w["id"] for w in listed] == [window_row["id"]]
            ended = client.post(f"/api/maintenance/{window_row['id']}/end", headers=operator)
            assert ended.status_code == 200 and ended.get_json()["window"]["state"] == "ended"
            assert client.get("/api/maintenance", headers=viewer).get_json()["windows"] == []
            assert len(client.get("/api/maintenance?all=1", headers=viewer).get_json()["windows"]) == 1
            assert client.post("/api/maintenance/999/end", headers=operator).status_code == 404

    def test_sites_batches_and_ending_many(self, client):
        sites = client.get("/api/maintenance/sites").get_json()["sites"]
        assert [(s["id"], s["name"], s["devices"], s["maintenance"]) for s in sites] == [
            ("loc-lon", "London HQ", 3, False)
        ]
        with auth_config(mode="header", viewer_groups={"noc"}, operator_groups={"ops"}):
            viewer = {"X-Forwarded-User": "vera", "X-Forwarded-Groups": "noc"}
            operator = {"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"}
            body = {"site_ids": ["loc-lon", "loc-x"], "reason": "Core upgrade", "duration_minutes": 60}
            windows = client.post("/api/maintenance", json=body, headers=operator).get_json()["windows"]
            listed = client.get("/api/maintenance", headers=viewer).get_json()
            assert {w["site_id"]: w["site_name"] for w in listed["windows"]} == {"loc-lon": "London HQ", "loc-x": ""}
            assert listed["truncated"] is False
            # One site (the board's panel): no names, so no board read.
            one = client.get("/api/maintenance?site_id=loc-lon", headers=viewer).get_json()
            assert "site_name" not in one["windows"][0]
            ids = [w["id"] for w in windows]
            assert client.post("/api/maintenance/end", json={"ids": ids}, headers=viewer).status_code == 403
            bad_bodies = (
                {"ids": []},
                {"ids": ["1"]},
                {"ids": [True]},
                {"ids": list(range(501))},
                [1],
                {"ids": [10**30]},
                {"ids": [0]},
            )
            for bad in bad_bodies:
                assert client.post("/api/maintenance/end", json=bad, headers=operator).status_code == 400, bad
            resp = client.post("/api/maintenance/end", json={"ids": [*ids, 999]}, headers=operator).get_json()
            assert {w["state"] for w in resp["windows"]} == {"ended"} and resp["not_found"] == [999]
            # Already over: nothing changes, so the cached board is kept.
            built = []
            original = alerts.build_alert_board_payload
            alerts.build_alert_board_payload = lambda *a, **k: built.append(1) or original(*a, **k)
            try:
                client.get("/api/alerts", headers=viewer)
                built.clear()
                client.post("/api/maintenance/end", json={"ids": ids}, headers=operator)
                client.get("/api/alerts", headers=viewer)
            finally:
                alerts.build_alert_board_payload = original
            assert built == [], "a no-op end must not drop the board"
            assert client.get("/api/maintenance", headers=viewer).get_json()["windows"] == []
            assert len(client.get("/api/maintenance?recent_days=7", headers=viewer).get_json()["windows"]) == 2
            assert client.get("/api/maintenance?recent_days=x", headers=viewer).status_code == 400

    def test_history_and_its_export(self, client, monkeypatch):
        body = {"site_ids": ["loc-lon", "loc-x"], "reason": "Core", "duration_minutes": 60}
        with auth_config(mode="header", viewer_groups={"noc"}, operator_groups={"ops"}):
            viewer = {"X-Forwarded-User": "vera", "X-Forwarded-Groups": "noc"}
            operator = {"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"}
            client.post("/api/maintenance", json=body, headers=operator)
            # Default: the last 30 days, today included, in UTC unless the browser says.
            data = client.get("/api/maintenance/history", headers=viewer).get_json()
            assert data["period"] == {
                "from": "2026-09-10",
                "to": "2026-10-09",
                "label": "2026-09-10 to 2026-10-09",
                "tz": "UTC",
            }
            # The browser's days (review on #353): at 12:00 UTC it is already the 10th in Auckland,
            # and the 9th there ends at 11:00 UTC, before the window started.
            nz = client.get("/api/maintenance/history?tz=Pacific/Auckland", headers=viewer).get_json()
            assert nz["period"]["to"] == "2026-10-10" and len(nz["windows"]) == 2
            nz9 = client.get(
                "/api/maintenance/history?tz=Pacific/Auckland&from=2026-10-09&to=2026-10-09", headers=viewer
            )
            assert nz9.get_json()["windows"] == []
            assert client.get("/api/maintenance/history?tz=Mars/Base", headers=viewer).status_code == 400
            assert {w["site_id"]: w["site_name"] for w in data["windows"]} == {"loc-lon": "London HQ", "loc-x": ""}
            assert data["truncated"] is False
            one = client.get("/api/maintenance/history?site_id=loc-lon&from=2026-10-09&to=2026-10-09", headers=viewer)
            assert [w["site_id"] for w in one.get_json()["windows"]] == ["loc-lon"]
            resp = client.get(
                "/api/maintenance/history.csv?site_id=loc-lon&from=2026-10-01&to=2026-10-09", headers=viewer
            )
            assert resp.status_code == 200 and resp.mimetype == "text/csv"
            assert 'filename="maintenance-2026-10-01_2026-10-09-loc-lon.csv"' in resp.headers["Content-Disposition"]
            assert "London HQ" in resp.get_data(as_text=True)
            # A tenant picks its sites before the limit, so its windows are never cut off for others'.
            monkeypatch.setattr(
                alerts,
                "cached_alert_board",
                lambda: {
                    "alerts": [
                        {"id": "loc-lon", "name": "London HQ", "tenants": ["Acme"]},
                        {"id": "loc-x", "name": "X", "tenants": ["Other"]},
                    ]
                },
            )
            monkeypatch.setattr(maintenance, "HISTORY_LIMIT", 1)
            only = client.get("/api/maintenance/history?tenant=Acme", headers=viewer).get_json()
            assert [w["site_id"] for w in only["windows"]] == ["loc-lon"] and only["truncated"] is False
            # An export is complete or refused, never silently short.
            monkeypatch.setattr(maintenance, "HISTORY_EXPORT_LIMIT", 1)
            refused = client.get("/api/maintenance/history.csv", headers=viewer)
            assert refused.status_code == 400 and "shorter period" in refused.get_json()["error"]
            for bad in ("from=2026-10-09", "from=2026-02-30&to=2026-03-01", "from=2025-01-01&to=2026-10-01"):
                assert client.get(f"/api/maintenance/history?{bad}", headers=viewer).status_code == 400, bad
            # The viewer role when the board needs it (AUTH_REQUIRE_VIEWER).
            monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
            assert client.get("/api/maintenance/history").status_code == 401
            assert client.get("/api/maintenance/history.csv").status_code == 401

    def test_bad_requests(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)
        resp = client.post("/api/maintenance", json={"site_id": "loc-lon", "duration_minutes": 60})
        assert resp.status_code == 400 and resp.get_json()["error"] == "reason is required"
        assert client.post("/api/maintenance", data="[1]", content_type="application/json").status_code == 400
