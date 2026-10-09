"""Maintenance windows (#283): planned work is not an alarm."""

import json

import pytest

import app as flask_app
from nautobot_maps import alerts, caching, db, inventory, maintenance, notify, settings, timeutil
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
        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as client:
            body = client.get("/api/maintenance/devices?site_id=loc-lon&include_non_operational=1").get_json()
        assert "old-sw" in {d["id"] for d in body["devices"]}

    def test_endpoint(self, site):
        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as client:
            assert client.get("/api/maintenance/devices").status_code == 400
            body = client.get("/api/maintenance/devices?site_id=loc-lon").get_json()
        assert [d["id"] for d in body["devices"]] == ["acc1", "acc2", "core"]


class TestApi:
    @pytest.fixture
    def client(self, site):
        flask_app.app.config["TESTING"] = True
        caching.cache.clear()
        with flask_app.app.test_client() as test_client:
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

    def test_bad_requests(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)
        resp = client.post("/api/maintenance", json={"site_id": "loc-lon", "duration_minutes": 60})
        assert resp.status_code == 400 and resp.get_json()["error"] == "reason is required"
        assert client.post("/api/maintenance", data="[1]", content_type="application/json").status_code == 400
