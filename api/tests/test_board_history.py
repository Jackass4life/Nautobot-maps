"""TODO docstring (#786)."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from app.clients import caching, nautobot
from app.core.settings import settings
from app.db import connection as db
from app.repositories import board_repository as board_store
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from app.services import scheduler
from app.utils import timeutil
from app.v1.alert_board import controller as api_board
from tests import db as adb
from tests.fakes import _full_locations, auth_config


# ---------------------------------------------------------------------------
# Tests: alert lifecycle history and cases
# ---------------------------------------------------------------------------
class TestAlertLifecycleTracking:
    def test_api_alerts_contains_lifecycle_fields(self, client):
        with (
            patch.object(
                inventory,
                "get_locations",
                return_value=_full_locations(
                    [
                        {
                            "id": "loc-1",
                            "name": "Site One",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": "",
                            "tags": [],
                            "url": "",
                        }
                    ]
                ),
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=(
                    [
                        {
                            "id": "dev-1",
                            "name": "router01",
                            "role": "Core Router",
                            "status": "offline",
                        }
                    ],
                    {"level": "critical", "reason": "Core device(s) offline: router01"},
                ),
            ),
        ):
            resp = client.get("/api/v1/alerts")
        assert resp.status_code == 200
        entry = resp.json()["alerts"][0]
        assert "current_downtime_seconds" in entry
        assert "historical_downtime_seconds" in entry
        assert "active_cases" in entry
        assert entry["active_alert_instance_count"] == 1
        assert entry["down_devices"] == [
            {
                "device_id": "dev-1",
                "device_name": "router01",
                "device_ip": "",
                "status": "offline",
                "role": "Core Router",
                "core": True,  # the likely cause of the site's other down devices (#285)
                "case_numbers": [],
            }
        ]

    def test_alert_history_tracks_open_and_resolve(self, client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        t1 = datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        asyncio.run(
            alerts.upsert_alert_lifecycle_for_site(
                site,
                devices_down,
                {"level": "critical", "reason": "Core device(s) offline: router01"},
                t0,
            )
        )
        asyncio.run(
            alerts.upsert_alert_lifecycle_for_site(
                site,
                [{"id": "dev-1", "name": "router01", "status": "active"}],
                {"level": "ok", "reason": ""},
                t1,
            )
        )
        resp = client.get("/api/v1/alert-history?site_id=loc-1")
        assert resp.status_code == 200
        instances = resp.json()["instances"]
        assert len(instances) == 1
        assert instances[0]["status"] == "resolved"
        assert instances[0]["total_downtime_seconds"] == 300
        event_types = [event["event_type"] for event in instances[0]["events"]]
        assert "opened" in event_types
        assert "resolved" in event_types
        resolved_event = next(
            event for event in instances[0]["events"] if event["event_type"] == "resolved"
        )
        assert resolved_event["snapshot"]["site_id"] == "loc-1"
        assert resolved_event["snapshot"]["device_id"] == "dev-1"
        assert resolved_event["snapshot"]["status"] == "resolved"

    def test_severity_change_keeps_the_alert_and_its_downtime(self):
        """Medium → Critical must not restart the device's downtime (#163)."""
        site = {"id": "loc-1", "name": "Site One"}
        t0 = "2026-01-01T00:00:00Z"
        t1 = "2026-01-01T01:00:00Z"
        asyncio.run(
            alerts.upsert_alert_lifecycle_for_site(
                site,
                [{"id": "dev-1", "name": "sw01", "status": "offline"}],
                {"level": "medium", "reason": "1/3 devices offline (33%)"},
                t0,
            )
        )
        asyncio.run(
            alerts.upsert_alert_lifecycle_for_site(
                site,
                [
                    {"id": "dev-1", "name": "sw01", "status": "offline"},
                    {"id": "dev-2", "name": "core01", "status": "offline"},
                ],
                {"level": "critical", "reason": "Core device(s) offline: core01"},
                t1,
            )
        )

        rows = adb.sql(
            "SELECT device_id, status, alert_level, down_started_at FROM alert_instances ORDER BY device_id, id"
        )
        dev1 = [row for row in rows if row["device_id"] == "dev-1"]
        assert len(dev1) == 1, dev1  # one alert, not resolved and reopened
        assert dev1[0]["status"] == "open"
        assert dev1[0]["alert_level"] == "critical"
        assert db.serialize_value(dev1[0]["down_started_at"]) == t0
        events = adb.sql(
            "SELECT e.event_type, e.alert_level FROM alert_events e "
            "JOIN alert_instances i ON i.id = e.alert_instance_id WHERE i.device_id = 'dev-1' ORDER BY e.id"
        )
        assert events == [
            {"event_type": "opened", "alert_level": "medium"},
            {"event_type": "updated", "alert_level": "critical"},
        ]
        context = asyncio.run(alerts.get_alert_context_for_site("loc-1", t1))
        assert context["current_downtime_seconds"] == 3600

    def test_add_case_number_to_active_alert(self, client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        asyncio.run(
            alerts.upsert_alert_lifecycle_for_site(
                site,
                devices_down,
                {"level": "critical", "reason": "Core device(s) offline: router01"},
                t0,
            )
        )
        created = client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_id": "dev-1", "case_number": "INC-1001"},
        )
        assert created.status_code == 200
        history = client.get("/api/v1/alert-history?site_id=loc-1&device_id=dev-1")
        assert history.status_code == 200
        instances = history.json()["instances"]
        assert instances[0]["cases"][0]["case_number"] == "INC-1001"

    def test_api_alerts_preserves_case_numbers_on_current_down_devices(self, client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        asyncio.run(
            alerts.upsert_alert_lifecycle_for_site(
                site,
                devices_down,
                {"level": "critical", "reason": "Core device(s) offline: router01"},
                t0,
            )
        )
        created = client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_id": "dev-1", "case_number": "INC-1001"},
        )
        assert created.status_code == 200
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at="2026-01-01T00:00:00Z",
            last_completed_at="2026-01-01T00:05:00Z",
            last_successful_sync="2026-01-01T00:05:00Z",
            cache_version=inventory.CACHE_VERSION,
            status="idle",
            error_message="",
        )

        with (
            patch.object(
                inventory,
                "get_locations",
                return_value=_full_locations(
                    [
                        {
                            "id": "loc-1",
                            "name": "Site One",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": "",
                            "tags": [],
                            "url": "",
                        }
                    ]
                ),
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=(
                    [
                        {
                            "id": "dev-1",
                            "name": "router01",
                            "role": "Core Router",
                            "status": "offline",
                        }
                    ],
                    {"level": "critical", "reason": "Core device(s) offline: router01"},
                ),
            ),
        ):
            resp = client.get("/api/v1/alerts")

        assert resp.status_code == 200
        entry = resp.json()["alerts"][0]
        assert entry["active_cases"] == ["INC-1001"]
        assert entry["down_devices"] == [
            {
                "device_id": "dev-1",
                "device_name": "router01",
                "device_ip": "",
                "status": "offline",
                "role": "Core Router",
                "core": True,  # the likely cause of the site's other down devices (#285)
                "case_numbers": ["INC-1001"],
                "down_started_at": "2026-01-01T00:00:00Z",
            }
        ]

    def test_add_case_uses_authenticated_user_for_created_by(self, client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        asyncio.run(
            alerts.upsert_alert_lifecycle_for_site(
                site,
                devices_down,
                {"level": "critical", "reason": "Core device(s) offline: router01"},
                t0,
            )
        )
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            created = client.post(
                "/api/v1/alert-cases",
                json={
                    "site_id": "loc-1",
                    "device_id": "dev-1",
                    "case_number": "INC-1002",
                    "created_by": "mallory",
                },
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
            assert created.status_code == 200
            history = client.get(
                "/api/v1/alert-history?site_id=loc-1&device_id=dev-1",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
        assert history.status_code == 200
        instances = history.json()["instances"]
        assert instances[0]["cases"][0]["created_by"] == "alice"

    def test_alert_history_rejects_invalid_timestamp_filters(self, client):
        resp = client.get("/api/v1/alert-history?start_at=not-a-timestamp")
        assert resp.status_code == 400
        assert "start_at" in resp.json()["error"]
        resp = client.get("/api/v1/alert-history?end_at=still-not-a-timestamp")
        assert resp.status_code == 400
        assert "end_at" in resp.json()["error"]

    def test_failed_alert_observation_does_not_resolve_open_incident(self):
        site = {"id": "loc-1", "name": "Site One"}
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        asyncio.run(
            alerts.upsert_alert_lifecycle_for_site(
                site,
                [{"id": "dev-1", "name": "router01", "status": "offline"}],
                {"level": "critical", "reason": "Core device(s) offline: router01"},
                t0,
            )
        )
        sample_locations = [{"id": "loc-1", "name": "Site One", "latitude": 1.0, "longitude": 2.0}]
        with (
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts, "get_location_devices_and_alert", side_effect=RuntimeError("lookup failed")
            ),
        ):
            data = asyncio.run(alerts.get_alert_board_data(force_refresh=True))
        assert data["alerts"][0]["alert_level"] == "no_data"
        history = asyncio.run(alerts.get_alert_context_for_site("loc-1", timeutil.iso_utc_now()))
        assert history["active_alert_instance_count"] == 1
        rows = adb.sql(
            "SELECT status FROM alert_instances WHERE site_id = %s ORDER BY id DESC",
            ("loc-1",),
        )
        assert db.row_to_dict(rows[0])["status"] == "open"

    def test_get_alert_board_data_uses_cache_when_persistence_enabled(self):
        sample_locations = [{"id": "loc-1", "name": "Site One", "latitude": 1.0, "longitude": 2.0}]
        devices_return = (
            [{"id": "dev-1", "name": "router01", "role": "Core Router", "status": "offline"}],
            {"level": "critical", "reason": "Core device(s) offline: router01"},
        )
        with (
            patch.object(
                inventory, "get_locations", return_value=sample_locations
            ) as get_locations,
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts, "get_location_devices_and_alert", return_value=devices_return
            ) as get_alert,
        ):
            first = asyncio.run(alerts.get_alert_board_data(force_refresh=True))
            second = asyncio.run(alerts.get_alert_board_data())
        assert first["alerts"] == second["alerts"]
        assert get_locations.call_count == 1
        assert get_alert.call_count == 1

    def test_get_alert_board_data_sets_ttl_and_enqueues_sync_on_force_refresh(self):
        first_payload = {
            "checked_at": "2026-01-01T00:00:00Z",
            "stale_after_seconds": settings.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        second_payload = {
            "checked_at": "2026-01-01T00:05:00Z",
            "stale_after_seconds": settings.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        caching.cache.clear()
        with (
            patch.object(
                alerts,
                "build_alert_board_payload",
                side_effect=[first_payload, second_payload],
            ) as build_payload,
            patch.object(caching, "set", wraps=caching.set) as cache_set,
            patch.object(
                inventory,
                "ensure_snapshot",
            ) as ensure_snapshot,
            patch.object(
                inventory,
                "board_snapshot",
                return_value={"version": None, "initialized": True, "nautobot": {}},
            ),
        ):
            first = asyncio.run(alerts.get_alert_board_data(force_refresh=True))
            second = asyncio.run(alerts.get_alert_board_data())
            refreshed = asyncio.run(alerts.get_alert_board_data(force_refresh=True))
        assert first["checked_at"] == second["checked_at"] == "2026-01-01T00:00:00Z"
        assert refreshed["checked_at"] == "2026-01-01T00:00:00Z"
        assert build_payload.call_count == 1
        assert cache_set.call_count == 1
        assert ensure_snapshot.call_count == 2
        assert all(
            call.args == ()
            and call.kwargs
            == {
                "force": True,
                "full": False,
                "wait": False,
                "cooldown_seconds": inventory.FORCED_SYNC_COOLDOWN_SECONDS,
            }
            for call in ensure_snapshot.call_args_list
        )
        # The stored board outlives the sync interval a little (#295).
        assert all(
            call.kwargs.get("timeout") == settings.CACHE_TTL + alerts.BOARD_TTL_SLACK_SECONDS
            for call in cache_set.call_args_list
        )

    def test_get_alert_board_data_builds_snapshot_only_payload(self):
        caching.cache.clear()
        payload = {
            "checked_at": "2026-01-01T00:00:00Z",
            "stale_after_seconds": settings.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        with (
            patch.object(
                alerts, "build_alert_board_payload", return_value=payload
            ) as build_payload,
            patch.object(inventory, "ensure_snapshot") as ensure_snapshot,
        ):
            result = asyncio.run(alerts.get_alert_board_data(force_refresh=True))

        assert result["checked_at"] == "2026-01-01T00:00:00Z"
        build_payload.assert_called_once_with(snapshot_only=True)
        ensure_snapshot.assert_called_once_with(
            force=True,
            full=False,
            wait=False,
            cooldown_seconds=inventory.FORCED_SYNC_COOLDOWN_SECONDS,
        )

    def test_get_alert_board_data_does_not_cache_empty_payload_before_snapshot_init(self):
        caching.cache.clear()
        payload = {
            "checked_at": "2026-01-01T00:00:00Z",
            "stale_after_seconds": settings.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        with (
            patch.object(
                alerts,
                "build_alert_board_payload",
                return_value=payload,
            ) as build_payload,
            patch.object(
                caching,
                "set",
                wraps=caching.set,
            ) as cache_set,
            patch.object(
                inventory,
                "board_snapshot",
                return_value={"version": None, "initialized": False, "nautobot": {}},
            ),
            patch.object(inventory, "ensure_snapshot"),
        ):
            first = asyncio.run(alerts.get_alert_board_data(force_refresh=True))
            second = asyncio.run(alerts.get_alert_board_data())

        assert first["alerts"] == []
        assert second["alerts"] == []
        assert build_payload.call_count == 2
        assert cache_set.call_count == 0

    def test_postgres_lifecycle_path_uses_postgres_sql(self):
        class _FakeResult:
            def __init__(self, rows=None, rowcount=0):
                self._rows = rows or []
                self.rowcount = rowcount

            def fetchone(self):
                return self._rows[0] if self._rows else None

            def fetchall(self):
                return self._rows

        class _FakeTransaction:
            def __init__(self, conn):
                self.conn = conn

            async def __aenter__(self):
                self.conn.transaction_entries += 1
                return self.conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _FakeConn:
            def __init__(self):
                self.queries = []
                self.closed = False
                self.connection_context_entries = 0
                self.transaction_entries = 0

            def __enter__(self):
                self.connection_context_entries += 1
                return self

            def __exit__(self, exc_type, exc, tb):
                self.closed = True
                return False

            def close(self):
                self.closed = True

            def transaction(self):
                return _FakeTransaction(self)

            async def execute(self, query, params=()):
                if self.closed:
                    raise RuntimeError("the connection is closed")
                self.queries.append((query, params))
                if "SELECT id, status, down_started_at, alert_level, alert_reason" in query:
                    return _FakeResult([])
                if "INSERT INTO alert_instances" in query:
                    return _FakeResult([{"id": 101}])
                if "INSERT INTO alert_events" in query:
                    return _FakeResult([])
                if (
                    "SELECT id, alert_key, site_id, site_name, device_id, device_name, down_started_at"
                    in query
                ):
                    return _FakeResult(
                        [
                            {
                                "id": 101,
                                "alert_key": "k",
                                "site_id": "loc-1",
                                "site_name": "Site One",
                                "device_id": "dev-1",
                                "device_name": "router01",
                                "down_started_at": "2026-01-01T00:00:00Z",
                            }
                        ]
                    )
                if "UPDATE alert_instances" in query:
                    return _FakeResult([], rowcount=1)
                return _FakeResult([])

        fake_conn = _FakeConn()
        checked_at = "2026-01-01T00:00:00Z"
        with patch.object(db, "dialect", return_value="postgres"):
            asyncio.run(
                alerts.upsert_alert_lifecycle_for_site(
                    {"id": "loc-1", "name": "Site One"},
                    [{"id": "dev-1", "name": "router01", "status": "offline"}],
                    {"level": "critical", "reason": "offline"},
                    checked_at,
                    conn=fake_conn,
                )
            )
            asyncio.run(
                alerts.resolve_open_alert_instances_for_site(fake_conn, "loc-1", set(), checked_at)
            )
        insert_sql = next(
            query for query, _ in fake_conn.queries if "INSERT INTO alert_instances" in query
        )
        assert "ON CONFLICT (alert_key) WHERE status = 'open' DO NOTHING" in insert_sql
        assert "%s" in insert_sql
        update_sql = next(
            query for query, _ in fake_conn.queries if "UPDATE alert_instances" in query
        )
        assert "CURRENT_TIMESTAMP" in update_sql
        assert "AND status = 'open'" in update_sql
        assert "AND down_started_at <=" in update_sql
        event_params = [
            params for query, params in fake_conn.queries if "INSERT INTO alert_events" in query
        ]
        assert any(param == checked_at for params in event_params for param in params)
        assert fake_conn.connection_context_entries == 0
        assert fake_conn.transaction_entries == 1


# ---------------------------------------------------------------------------
# Tests: one case number on multiple devices (#133)
# ---------------------------------------------------------------------------
class TestMultiDeviceCases:
    @pytest.fixture(autouse=True)
    def _database_with_open_alerts(self):
        asyncio.run(
            alerts.upsert_alert_lifecycle_for_site(
                {"id": "loc-1", "name": "Site One"},
                [
                    {"id": "dev-1", "name": "sw01", "status": "offline"},
                    {"id": "dev-2", "name": "sw02", "status": "offline"},
                    {"id": "dev-3", "name": "sw03", "status": "offline"},
                ],
                {"level": "medium", "reason": "3/4 devices offline (75%)"},
                timeutil.iso_utc_now(),
            )
        )
        yield
        caching.cache.clear()

    def _cases_for(self, client, device_id):
        history = client.get(f"/api/v1/alert-history?site_id=loc-1&device_id={device_id}").json()
        return [case["case_number"] for case in history["instances"][0]["cases"]]

    def test_links_one_case_to_every_selected_device(self, client):
        resp = client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_ids": ["dev-1", "dev-2"], "case_number": "INC-42"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert [item["device_id"] for item in data["linked"]] == ["dev-1", "dev-2"]
        assert "device_id" not in data  # single-device fields only for one device
        assert self._cases_for(client, "dev-1") == ["INC-42"]
        assert self._cases_for(client, "dev-2") == ["INC-42"]
        assert self._cases_for(client, "dev-3") == []

    def test_is_all_or_nothing_when_a_device_has_no_open_alert(self, client):
        resp = client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_ids": ["dev-1", "dev-9"], "case_number": "INC-43"},
        )
        assert resp.status_code == 404
        assert resp.json()["missing_device_ids"] == ["dev-9"]
        assert self._cases_for(client, "dev-1") == []

    def test_duplicate_ids_are_linked_once(self, client):
        resp = client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_ids": ["dev-1", " dev-1 "], "case_number": "INC-44"},
        )
        assert resp.status_code == 200
        assert len(resp.json()["linked"]) == 1
        assert self._cases_for(client, "dev-1") == ["INC-44"]

    def test_single_device_id_keeps_original_response_fields(self, client):
        resp = client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_id": "dev-3", "case_number": "INC-45"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["device_id"] == "dev-3"
        assert isinstance(data["alert_instance_id"], int)
        assert self._cases_for(client, "dev-3") == ["INC-45"]

    @pytest.mark.parametrize(
        "device_ids",
        ["dev-1", [], ["dev-1", 7], [f"dev-{i}" for i in range(api_board.MAX_CASE_DEVICES + 1)]],
        ids=["string-not-list", "empty", "non-string-id", "too-many"],
    )
    def test_rejects_invalid_device_ids(self, client, device_ids):
        resp = client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_ids": device_ids, "case_number": "INC-46"},
        )
        assert resp.status_code == 400
        assert self._cases_for(client, "dev-1") == []


# ---------------------------------------------------------------------------
# Tests: alerts start when Nautobot's status changed, not when first seen (#166)
# ---------------------------------------------------------------------------
class TestRealStartTimes:
    CHECKED_AT = "2026-09-28T12:00:00+00:00"

    def test_down_since(self):
        now = self.CHECKED_AT
        # Nautobot status changed three days earlier -> that is when it went down.
        assert (
            alerts.down_since({"last_updated": "2026-09-25T12:00:00Z"}, now)
            == "2026-09-25T12:00:00+00:00"
        )
        # A later edit (clock skew, or an edit after we saw it) never moves the start later.
        assert alerts.down_since({"last_updated": "2026-09-28T13:00:00Z"}, now) == now
        # Unknown last change -> when we saw it.
        assert alerts.down_since({"last_updated": ""}, now) == now
        assert alerts.down_since({}, now) == now
        # Only LibreNMS says down: Nautobot's last_updated says nothing about this outage.
        assert (
            alerts.down_since(
                {"last_updated": "2026-09-25T12:00:00Z", "down_source": "librenms"}, now
            )
            == now
        )

    def test_librenms_only_down_is_marked(self, override_settings):
        override_settings(LIBRENMS_URL="https://librenms.example.com", LIBRENMS_API_TOKEN="token")
        enriched = asyncio.run(
            alerts.enrich_with_librenms(
                [
                    {
                        "id": "d1",
                        "name": "sw01",
                        "status": "active",
                        "last_updated": "2026-01-01T00:00:00Z",
                    },
                    {
                        "id": "d2",
                        "name": "sw02",
                        "status": "offline",
                        "last_updated": "2026-01-01T00:00:00Z",
                    },
                ],
                lnms_devices=[
                    {"device_id": 1, "hostname": "sw01", "status": 0},
                    {"device_id": 2, "hostname": "sw02", "status": 0},
                ],
                lnms_id_map={1: {}, 2: {}},
                snapshot_only=True,
            )
        )
        assert enriched[0]["status"] == "offline" and enriched[0]["down_source"] == "librenms"
        # Already down in Nautobot: Nautobot is the source.
        assert "down_source" not in enriched[1]

    @pytest.fixture
    def board(self, monkeypatch, override_settings):
        override_settings(
            NAUTOBOT_URL="https://nautobot.example.com",
            NAUTOBOT_TOKEN="token",
            LIBRENMS_URL="",
            LIBRENMS_API_TOKEN="",
        )
        monkeypatch.setattr(inventory, "ensure_snapshot", adb.async_of(lambda *a, **k: False))
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: self.CHECKED_AT)
        adb.sql(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, status) "
            "VALUES ('loc-1', 'Site One', 'Office', 'Active')"
        )
        adb.many(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip, last_updated) "
            "VALUES (%s, 'loc-1', %s, 'access', %s, %s, %s)",
            [
                ("d-old", "sw-old", "Offline", "10.0.0.1/32", "2026-09-25T12:00:00Z"),
                ("d-up", "sw-up", "Active", "10.0.0.2/32", "2026-09-01T00:00:00Z"),
                ("d-up2", "sw-up2", "Active", "10.0.0.3/32", "2026-09-01T00:00:00Z"),
                ("d-up3", "sw-up3", "Active", "10.0.0.4/32", "2026-09-01T00:00:00Z"),
            ],
        )
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at="2026-09-28T11:00:00+00:00",
            last_completed_at="2026-09-28T11:00:00+00:00",
            last_successful_sync="2026-09-28T11:00:00+00:00",
            cache_version=inventory.CACHE_VERSION,
            status="idle",
        )

        def build():
            caching.cache.clear()
            return {row["id"]: row for row in asyncio.run(alerts.get_alert_board_data())["alerts"]}

        return build

    def _start(self):
        rows = adb.sql("SELECT down_started_at FROM alert_instances WHERE device_id = 'd-old'")
        return [db.serialize_value(row["down_started_at"]) for row in rows]

    def test_new_alert_starts_when_nautobot_status_changed(self, board):
        site = board()["loc-1"]
        assert self._start() == ["2026-09-25T12:00:00Z"]
        assert site["current_downtime_seconds"] == 3 * 24 * 3600

    def test_open_alert_is_moved_earlier_once_and_never_later(self, board):
        # An alert opened before #166: started when the app first saw it.
        adb.sql(
            "INSERT INTO alert_instances (alert_key, site_id, site_name, device_id, device_name, alert_level, "
            "alert_reason, status, down_started_at, last_seen_down_at, total_downtime_seconds) "
            "VALUES (%s, 'loc-1', 'Site One', 'd-old', 'sw-old', 'low', '', 'open', "
            "'2026-09-27T08:00:00Z', '2026-09-27T08:00:00Z', 0)",
            (db.build_alert_key("loc-1", "d-old"),),
        )
        board()
        assert self._start() == ["2026-09-25T12:00:00Z"]
        # A later edit in Nautobot must not move it back.
        adb.sql("UPDATE nautobot_device_cache SET last_updated = '2026-09-28T11:30:00Z'")
        board()
        assert self._start() == ["2026-09-25T12:00:00Z"]


# ---------------------------------------------------------------------------
# Tests: alert feed of devices going down and up and site severity changes (#180)
# ---------------------------------------------------------------------------
class TestAlertFeed:
    @pytest.fixture
    def board(self, monkeypatch, override_settings):
        """A site with two switches; returns (build, set_status, clock)."""
        override_settings(
            NAUTOBOT_URL="https://nautobot.example.com",
            NAUTOBOT_TOKEN="token",
            LIBRENMS_URL="",
            LIBRENMS_API_TOKEN="",
        )
        monkeypatch.setattr(inventory, "ensure_snapshot", adb.async_of(lambda *a, **k: False))
        clock = {"now": "2026-09-28T12:00:00+00:00"}
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: clock["now"])
        adb.sql(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, status) "
            "VALUES ('loc-1', 'Site One', 'Office', 'Active')"
        )
        adb.many(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES (%s, 'loc-1', %s, 'access', 'Active', %s)",
            [("d-1", "sw01", "10.0.0.1/32"), ("d-2", "sw02", "10.0.0.2/32")],
        )
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at="2026-09-28T11:00:00+00:00",
            last_completed_at="2026-09-28T11:00:00+00:00",
            last_successful_sync="2026-09-28T11:00:00+00:00",
            cache_version=inventory.CACHE_VERSION,
            status="idle",
        )

        def build(at):
            clock["now"] = at
            caching.cache.clear()
            return {row["id"]: row for row in asyncio.run(alerts.get_alert_board_data())["alerts"]}

        def set_status(device_id, status):
            adb.sql(
                "UPDATE nautobot_device_cache SET status = %s WHERE device_id = %s",
                (status, device_id),
            )

        return build, set_status

    def _feed(self, client, query=""):
        resp = client.get(f"/api/v1/alert-feed{query}")
        assert resp.status_code == 200, resp.json()
        return resp.json()

    def test_level_changes_are_logged_but_not_first_sightings(self):
        at = "2026-09-28T12:00:00+00:00"
        assert (
            asyncio.run(
                alerts.record_site_level_changes(
                    {"s1": ("One", "ok"), "s2": ("Two", "critical")}, at
                )
            )
            == 0
        )
        assert (
            asyncio.run(
                alerts.record_site_level_changes(
                    {"s1": ("One", "ok"), "s2": ("Two", "critical")}, at
                )
            )
            == 0
        )
        assert (
            asyncio.run(
                alerts.record_site_level_changes(
                    {"s1": ("One", "low"), "s2": ("Two", "critical")}, at
                )
            )
            == 1
        )
        rows = adb.sql("SELECT site_id, from_level, to_level FROM site_level_changes")
        assert rows == [{"site_id": "s1", "from_level": "ok", "to_level": "low"}]
        levels = adb.sql("SELECT site_id, alert_level FROM site_alert_levels ORDER BY site_id")
        assert levels == [
            {"site_id": "s1", "alert_level": "low"},
            {"site_id": "s2", "alert_level": "critical"},
        ]

    def test_board_builds_fill_the_feed(self, board, client):
        build, set_status = board
        assert build("2026-09-28T12:00:00+00:00")["loc-1"]["alert_level"] == "ok"
        assert self._feed(client)["events"] == []  # first sighting: nothing changed

        set_status("d-1", "Offline")
        down_level = build("2026-09-28T12:05:00+00:00")["loc-1"]["alert_level"]
        assert down_level != "ok"
        set_status("d-1", "Active")
        build("2026-09-28T12:10:00+00:00")

        feed = self._feed(client)
        assert feed["persistence_configured"] is True
        assert [(e["kind"], e["at"]) for e in feed["events"]] == [
            ("severity", "2026-09-28T12:10:00Z"),
            ("up", "2026-09-28T12:10:00Z"),
            ("severity", "2026-09-28T12:05:00Z"),
            ("down", "2026-09-28T12:05:00Z"),
        ]
        back_to_ok, up, went_down, down = feed["events"]
        assert down["site_name"] == "Site One" and down["device_name"] == "sw01"
        assert down["down_since"] == "2026-09-28T12:05:00Z" and "from_level" not in down
        assert up["device_id"] == "d-1" and up["level"] == "ok" and "down_since" not in up
        assert (went_down["from_level"], went_down["to_level"]) == ("ok", down_level)
        assert (back_to_ok["from_level"], back_to_ok["to_level"]) == (down_level, "ok")
        assert back_to_ok["device_id"] == ""

        # Filters.
        assert [e["kind"] for e in self._feed(client, "?kinds=severity")["events"]] == [
            "severity",
            "severity",
        ]
        assert [e["kind"] for e in self._feed(client, "?kinds=down,up")["events"]] == [
            "up",
            "down",
        ]
        assert [e["kind"] for e in self._feed(client, "?limit=1")["events"]] == ["severity"]
        since = self._feed(client, "?since=2026-09-28T12:05:00Z")["events"]
        assert [e["kind"] for e in since] == ["severity", "up"]

    def test_a_board_for_some_sites_reads_their_feed(self, board, client):
        """?tenant= / ?q= (#369): only those sites' entries, and the limit counts them."""
        build, set_status = board
        adb.sql(
            "UPDATE nautobot_location_cache SET tenant = 'Acme Corp' WHERE location_id = 'loc-1'"
        )
        adb.sql(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, status, tenant) "
            "VALUES ('loc-2', 'Site Two', 'Office', 'Active', 'Nordic Net')"
        )
        adb.sql(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES ('d-3', 'loc-2', 'sw03', 'access', 'Active', '10.0.0.3/32')"
        )
        build("2026-09-28T12:00:00+00:00")
        set_status("d-1", "Offline")
        build("2026-09-28T12:05:00+00:00")
        set_status("d-3", "Offline")  # newer: Site Two's events come first in the whole feed
        build("2026-09-28T12:10:00+00:00")

        every = self._feed(client)["events"]
        assert {e["site_name"] for e in every} == {"Site One", "Site Two"}
        acme = self._feed(client, "?tenant=acme%20corp")["events"]
        assert acme and {e["site_name"] for e in acme} == {"Site One"}
        assert {e["site_name"] for e in self._feed(client, "?q=two")["events"]} == {"Site Two"}
        # The limit counts the matching entries, not the whole feed's newest.
        one = self._feed(client, "?tenant=Acme%20Corp&limit=1")["events"]
        assert len(one) == 1 and one[0]["site_name"] == "Site One"
        # since and the scope together.
        later = self._feed(client, "?q=two&since=2026-09-28T12:05:00Z")["events"]
        assert later and {(e["site_name"], e["at"]) for e in later} == {
            ("Site Two", "2026-09-28T12:10:00Z")
        }
        assert self._feed(client, "?tenant=Acme%20Corp&since=2026-09-28T12:05:00Z")["events"] == []
        assert self._feed(client, "?tenant=nobody") == {
            "events": [],
            "persistence_configured": True,
            "matching_sites": 0,
        }
        assert self._feed(client, "?q=site")["matching_sites"] == 2
        assert "matching_sites" not in self._feed(client)

    def test_a_scoped_feed_without_stored_sites_says_so(self, client):
        """No stored locations (not synced yet, or the read failed): an error,
        not an empty feed that looks like an all-clear (#369)."""
        resp = client.get("/api/v1/alert-feed?tenant=Acme")
        assert resp.status_code == 503
        assert "not stored yet" in resp.json()["error"]
        assert self._feed(client)["events"] == []  # the whole network's feed still answers

    def test_bad_parameters_are_rejected(self, client):
        assert client.get("/api/v1/alert-feed?limit=many").status_code == 400
        assert client.get("/api/v1/alert-feed?since=yesterday").status_code == 400
        resp = client.get("/api/v1/alert-feed?kinds=down,cases")
        assert resp.status_code == 400 and "cases" in resp.json()["error"]
        # Out-of-range limits are clamped, not rejected.
        assert client.get("/api/v1/alert-feed?limit=0").status_code == 200
        assert client.get("/api/v1/alert-feed?limit=100000").status_code == 200

    def test_failed_observation_is_not_a_level_change(self, board, monkeypatch):
        build, _ = board
        build("2026-09-28T12:00:00+00:00")

        def fail(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(alerts, "get_location_devices_and_alert", adb.async_of(fail))
        assert build("2026-09-28T12:05:00+00:00")["loc-1"]["alert_level"] == "no_data"
        assert adb.sql("SELECT count(*) AS n FROM site_level_changes") == [{"n": 0}]


# ---------------------------------------------------------------------------
# Tests: alert history retention (#194)
# ---------------------------------------------------------------------------
class TestHistoryRetention:
    def _alert(self, device, status, resolved_days_ago=None):
        row = adb.sql(
            "INSERT INTO alert_instances (alert_key, site_id, site_name, device_id, device_name, alert_level, "
            "status, down_started_at, last_seen_down_at, resolved_at) VALUES "
            "(%s, 'loc-1', 'Site', %s, %s, 'low', %s, now() - interval '400 days', now() - interval '400 days', "
            "CASE WHEN %s::int IS NULL THEN NULL ELSE now() - make_interval(days => %s::int) END) RETURNING id",
            (f"key-{device}", device, device, status, resolved_days_ago, resolved_days_ago),
        )[0]["id"]
        adb.sql(
            "INSERT INTO alert_events (alert_instance_id, event_type, event_at) VALUES (%s, 'opened', now())",
            (row,),
        )
        adb.sql(
            "INSERT INTO alert_cases (alert_instance_id, case_number) VALUES (%s, 'INC-1')", (row,)
        )
        return row

    def _change(self, days_ago):
        adb.sql(
            "INSERT INTO site_level_changes (site_id, site_name, from_level, to_level, changed_at) "
            "VALUES ('loc-1', 'Site', 'ok', 'low', now() - make_interval(days => %s))",
            (days_ago,),
        )

    def _devices(self):
        return sorted(r["device_id"] for r in adb.sql("SELECT device_id FROM alert_instances"))

    def test_deletes_only_old_resolved_history(self):
        self._alert("old-resolved", "resolved", resolved_days_ago=100)
        self._alert("new-resolved", "resolved", resolved_days_ago=5)
        self._alert("old-open", "open")  # started 400 days ago, still down
        self._change(100)
        self._change(5)
        deleted = adb.call(board_store.prune_alert_history, 30)
        assert deleted == {"alert_instances": 1, "site_level_changes": 1}
        assert self._devices() == ["new-resolved", "old-open"]
        # Its events and cases went with it.
        assert adb.sql("SELECT count(*) AS n FROM alert_events") == [{"n": 2}]
        assert adb.sql("SELECT count(*) AS n FROM alert_cases") == [{"n": 2}]

    def test_deletes_in_batches(self, monkeypatch):
        monkeypatch.setattr(board_store, "RETENTION_BATCH_SIZE", 2)
        for i in range(5):
            self._alert(f"d{i}", "resolved", resolved_days_ago=100)
        assert adb.call(board_store.prune_alert_history, 30)["alert_instances"] == 5
        assert self._devices() == []

    def test_off_by_default_and_once_a_day(self, override_settings):
        self._alert("old-resolved", "resolved", resolved_days_ago=100)
        override_settings(ALERT_HISTORY_RETENTION_DAYS=0)
        assert adb.call(alerts.maybe_prune_alert_history) is None
        assert self._devices() == ["old-resolved"]
        override_settings(ALERT_HISTORY_RETENTION_DAYS=30)
        assert adb.call(alerts.maybe_prune_alert_history)["alert_instances"] == 1
        self._alert("later", "resolved", resolved_days_ago=100)
        assert adb.call(alerts.maybe_prune_alert_history) is None  # already ran today
        assert self._devices() == ["later"]

    def test_scheduler_tick_runs_retention(self, monkeypatch, override_settings):
        override_settings(ALERT_HISTORY_RETENTION_DAYS=30)
        monkeypatch.setattr(inventory, "ensure_snapshot", adb.async_of(lambda *a, **k: False))
        self._alert("old-resolved", "resolved", resolved_days_ago=100)
        asyncio.run(scheduler.tick())
        assert self._devices() == []


class TestAlertHistoryCsv:
    """A site's alert history as CSV (#277)."""

    NOW = "2026-10-07T12:00:00Z"

    @pytest.fixture(autouse=True)
    def _history(self, monkeypatch):
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: self.NOW)
        with adb.connect() as conn:
            with conn.transaction():
                for device_id, name, role, ip in (
                    ("d1", "lon-sw01", "Access Switch", "10.0.8.4/24"),
                    ("d2", "lon-core", "Core Router", ""),
                ):
                    conn.execute(
                        "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, primary_ip) "
                        "VALUES (%s, 'loc-lon', %s, %s, %s)",
                        (device_id, name, role, ip),
                    )
                # d2 has no primary IP: the address LibreNMS polls is used.
                conn.execute("INSERT INTO librenms_device_map VALUES ('d2', 7, 'lon-core')")
                conn.execute(
                    "INSERT INTO librenms_device_status (device_id, hostname, ip) VALUES (7, 'lon-core', '10.9.9.9')"
                )
                # "gone" was deleted from Nautobot, but its LibreNMS mapping lingers.
                conn.execute("INSERT INTO librenms_device_map VALUES ('gone', 8, 'gone-host')")
                conn.execute(
                    "INSERT INTO librenms_device_status (device_id, hostname, ip) VALUES (8, 'gone-host', '10.6.6.6')"
                )
                incidents = [
                    # id, device, name, status, down, resolved, seconds, level
                    (
                        1,
                        "d1",
                        "lon-sw01",
                        "resolved",
                        "2026-10-01T08:12:00Z",
                        "2026-10-01T08:40:00Z",
                        1680,
                        "low",
                    ),
                    (2, "d1", "lon-sw01", "open", "2026-10-07T11:30:00Z", None, 0, "medium"),
                    (
                        3,
                        "d2",
                        "lon-core",
                        "resolved",
                        "2026-08-20T10:00:00Z",
                        "2026-09-27T10:00:00Z",
                        3283200,
                        "critical",
                    ),
                    (
                        4,
                        "d2",
                        "lon-core",
                        "resolved",
                        "2026-07-01T10:00:00Z",
                        "2026-07-01T11:00:00Z",
                        3600,
                        "critical",
                    ),
                    # Deleted from Nautobot since; a name that Excel would run as a formula.
                    (
                        5,
                        "gone",
                        "=HYPERLINK(1)",
                        "resolved",
                        "2026-10-05T09:00:00Z",
                        "2026-10-05T09:10:00Z",
                        600,
                        "low",
                    ),
                ]
                for row in incidents:
                    conn.execute(
                        "INSERT INTO alert_instances (id, alert_key, site_id, site_name, device_id, device_name, "
                        "status, down_started_at, last_seen_down_at, resolved_at, total_downtime_seconds, alert_level, "
                        "alert_reason) VALUES (%s, %s, 'loc-lon', 'London HQ', %s, %s, %s, %s, %s, %s, %s, %s, 'x')",
                        (
                            row[0],
                            f"k{row[0]}",
                            row[1],
                            row[2],
                            row[3],
                            row[4],
                            row[4],
                            row[5],
                            row[6],
                            row[7],
                        ),
                    )
                conn.execute(
                    "INSERT INTO alert_cases (alert_instance_id, case_number, created_by, created_at) VALUES "
                    "(1, 'INC-881', '', now()), (1, 'INC-1', '', now()), (2, 'INC-900', '', now())"
                )

    @staticmethod
    def _rows(resp):
        import csv as csv_module

        text = resp.text
        assert text.startswith("﻿"), "BOM for Excel"
        return list(csv_module.DictReader(text[1:].splitlines()))

    def test_incidents_in_the_last_30_days(self, client):
        resp = client.get("/api/v1/alert-history.csv?site_id=loc-lon")
        assert resp.status_code == 200 and resp.headers["content-type"].split(";")[0] == "text/csv"
        assert (
            resp.headers["Content-Disposition"]
            == 'attachment; filename="london-hq-incidents-30d-2026-10-07.csv"'
        )
        rows = self._rows(resp)
        # Newest first; #3 started before the period but was still down in it; #4 is older.
        assert [row["device"] for row in rows] == [
            "lon-sw01",
            "'=HYPERLINK(1)",
            "lon-sw01",
            "lon-core",
        ]
        open_row, gone, resolved, long_one = rows
        assert (
            open_row["up_at_utc"] == ""
            and open_row["status"] == "open"
            and open_row["duration_min"] == "30"
        )
        assert (
            open_row["cases"] == "INC-900"
            and open_row["ip"] == "10.0.8.4"
            and open_row["role"] == "Access Switch"
        )
        assert resolved == {
            "site": "London HQ",
            "device": "lon-sw01",
            "role": "Access Switch",
            "ip": "10.0.8.4",
            "down_at_utc": "2026-10-01 08:12",
            "up_at_utc": "2026-10-01 08:40",
            "duration_min": "28",
            "status": "resolved",
            "level": "low",
            "reason": "x",
            "cases": "INC-1;INC-881",
            "probable_cause": "",  # no core device was down (#285)
            "case_urls": "",  # no CASE_URL_TEMPLATE (#289)
        }
        assert long_one["ip"] == "10.9.9.9" and long_one["duration_min"] == "54720"
        # A deleted device exports no role and no IP, even with a lingering LibreNMS mapping.
        assert gone["role"] == "" and gone["ip"] == ""

    def test_incidents_are_streamed_in_batches(self, client, monkeypatch):
        """A site's whole history is never held in memory (Copilot on #278)."""
        from app.services import export_service as export

        monkeypatch.setattr(export, "STREAM_BATCH", 2)
        resp = client.get("/api/v1/alert-history.csv?site_id=loc-lon&days=all")
        assert "content-length" not in resp.headers
        assert len(self._rows(resp)) == 5

    def test_all_and_seven_days(self, client):
        everything = self._rows(client.get("/api/v1/alert-history.csv?site_id=loc-lon&days=all"))
        assert len(everything) == 5
        week = self._rows(client.get("/api/v1/alert-history.csv?site_id=loc-lon&days=7"))
        assert [row["down_at_utc"] for row in week] == [
            "2026-10-07 11:30",
            "2026-10-05 09:00",
            "2026-10-01 08:12",
        ]

    def test_per_device(self, client):
        resp = client.get("/api/v1/alert-history.csv?site_id=loc-lon&view=devices&days=all")
        assert (
            'filename="london-hq-devices-all-2026-10-07.csv"' in resp.headers["Content-Disposition"]
        )
        rows = {row["device"]: row for row in self._rows(resp)}
        assert rows["lon-sw01"] == {
            "site": "London HQ",
            "device": "lon-sw01",
            "role": "Access Switch",
            "ip": "10.0.8.4",
            "times_down": "2",
            "total_down_min": "58",
            "longest_min": "30",
            "last_down_at_utc": "2026-10-07 11:30",
            "down_now": "yes",
        }
        assert rows["lon-core"]["times_down"] == "2" and rows["lon-core"]["down_now"] == "no"
        assert rows["lon-core"]["longest_min"] == "54720"

    def test_history_panel_is_newest_down_first(self, client):
        """By when the device went down, not by internal id (#279); like the CSV."""
        instances = client.get("/api/v1/alert-history?site_id=loc-lon").json()["instances"]
        # Inserted as ids 1-5 in a different order than their start times.
        assert [row["id"] for row in instances] == [2, 5, 1, 3, 4]

    def test_bad_requests(self, client):
        assert client.get("/api/v1/alert-history.csv").status_code == 400
        assert client.get("/api/v1/alert-history.csv?site_id=loc-lon&days=14").status_code == 400
        assert client.get("/api/v1/alert-history.csv?site_id=loc-lon&view=json").status_code == 400
        empty = self._rows(client.get("/api/v1/alert-history.csv?site_id=nowhere"))
        assert empty == []

    def test_needs_the_operator_role(self, client):
        with auth_config(mode="header", viewer_groups={"noc"}, operator_groups={"ops"}):
            viewer = {"X-Forwarded-User": "vera", "X-Forwarded-Groups": "noc"}
            assert (
                client.get("/api/v1/alert-history.csv?site_id=loc-lon", headers=viewer).status_code
                == 403
            )
            operator = {"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"}
            assert (
                client.get(
                    "/api/v1/alert-history.csv?site_id=loc-lon", headers=operator
                ).status_code
                == 200
            )


class TestAlertHistoryCsvCells:
    def test_formula_guard(self):
        from app.services import export_service as export

        assert [export.safe_cell(v) for v in ("=1+1", "+x", "-3", "@a", "ok", None, 5)] == [
            "'=1+1",
            "'+x",
            "'-3",
            "'@a",
            "ok",
            "",
            "5",
        ]

    def test_file_name_is_safe(self):
        from datetime import UTC, datetime

        from app.services import export_service as export

        now = datetime(2026, 10, 7, tzinfo=UTC)
        assert (
            export.file_name('København "DC" / 2', "devices", "90", now)
            == "k-benhavn-dc-2-devices-90d-2026-10-07.csv"
        )
        assert export.file_name("", "incidents", "all", now) == "site-incidents-all-2026-10-07.csv"
