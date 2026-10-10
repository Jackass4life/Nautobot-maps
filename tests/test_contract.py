"""The data contract with Nautobot and LibreNMS (#309)."""

import logging
from pathlib import Path

import mock_nautobot  # provided via tests/conftest.py path injection
import pytest

import app as flask_app
from nautobot_maps import caching, contract, db, inventory, librenms, metrics, nautobot, settings

DOC = Path(__file__).resolve().parent.parent / "docs" / "data-contract.md"
DEVICES = contract.endpoint(contract.NAUTOBOT, "dcim/devices/")


def test_the_docs_are_generated_from_the_contract():
    assert DOC.read_text(encoding="utf-8") == contract.render_markdown(), (
        "run: python -m nautobot_maps contract-docs > docs/data-contract.md"
    )


class TestCheck:
    def test_a_matching_record(self):
        record = {
            "id": "d1",
            "name": "sw1",
            "status": {"id": "s1", "url": "…"},  # brief: no name
            "role": {"id": "r1", "name": "Access"},
            "location": {"id": "l1"},
            "primary_ip4": None,
            "device_type": {"id": "t1"},
            "last_updated": "2026-10-10T00:00:00Z",
        }
        assert contract.check(DEVICES, [record]) == {"records": 1, "mismatches": []}

    def test_missing_wrong_type_nested_and_non_objects(self):
        good = {
            "id": "d1",
            "name": None,
            "status": "Active",
            "role": None,
            "location": {"id": "l1"},
            "primary_ip4": {"address": "10.0.0.1/24"},
            "device_type": {"id": "t1"},
            "last_updated": "2026-10-10T00:00:00Z",
        }
        drifted = [
            {**good, "primary_ip4": 42},
            {**good, "primary_ip4": 43, "location": {"id": 7}},
            {key: value for key, value in good.items() if key != "last_updated"},
            {**good, "serial": None, "platform": None},  # optional and nullable: fine
            {**good, "location": None},  # nested fields are checked only under an object
            "not a record",
        ]
        result = contract.check(DEVICES, [good, *drifted])
        assert result["records"] == 7
        by_field = {(m["field"], m["problem"]): m["count"] for m in result["mismatches"]}
        assert by_field == {
            ("primary_ip4", "is integer, expected object, string or null"): 2,
            ("location.id", "is integer, expected string"): 1,
            ("last_updated", "missing"): 1,
            ("(record)", "is string, expected object"): 1,
        }

    def test_an_integer_is_a_number(self):
        locations = contract.endpoint(contract.NAUTOBOT, "dcim/locations/")
        record = {
            "id": "l1",
            "name": "A",
            "status": None,
            "location_type": None,
            "parent": None,
            "tenant": None,
            "latitude": 55,
            "longitude": "12.5",
            "last_updated": "x",
        }
        assert contract.check(locations, [record])["mismatches"] == []

    def test_every_contract_entry_is_well_formed(self):
        known = {"string", "number", "integer", "boolean", "object", "array", "null"}
        for item in contract.CONTRACT:
            assert item.fields, item.path
            paths = [f.path for f in item.fields]
            assert len(paths) == len(set(paths)), item.path
            for f in item.fields:
                assert set(f.types) <= known, (item.path, f.path)
                parent = f.path.rpartition(".")[0]
                assert not parent or parent in paths, f"{item.path}: {f.path} needs {parent}"


class TestOutputSide:
    """What a field "becomes" exists in what the normalizers return."""

    def _becomes(self, path):
        names = set()
        for f in contract.endpoint(contract.NAUTOBOT, path).fields:
            names |= {name.strip() for name in f.becomes.split(",") if name.strip()}
        return names

    def test_locations(self, monkeypatch):
        monkeypatch.setattr(nautobot, "id_name_map", lambda endpoint: {})
        monkeypatch.setattr(nautobot, "tenant_group_map", lambda: {})
        (location,) = inventory.normalize_locations([mock_nautobot.LOCATIONS[0]], include_without_coordinates=True)
        assert self._becomes("dcim/locations/") <= set(location)

    def test_devices(self):
        device = next(iter(mock_nautobot.DEVICES.values()))[0]
        (normalized,) = inventory.normalize_devices([device], lookup_maps={})
        assert self._becomes("dcim/devices/") <= set(normalized)


class TestMockNautobot:
    """The demo's mock Nautobot keeps to the contract, as a real one must."""

    @pytest.mark.parametrize(
        "path",
        [e.path for e in contract.CONTRACT if e.source == contract.NAUTOBOT and e.checked],
    )
    def test_endpoint(self, path):
        client = mock_nautobot.app.test_client()
        response = client.get(f"/api/{path}?limit=1000&depth=1", headers={"Authorization": "Token x"})
        if response.status_code == 404:
            pytest.skip(f"the mock has no {path}")
        records = response.get_json()["results"]
        assert records, path
        assert contract.check(contract.endpoint(contract.NAUTOBOT, path), records)["mismatches"] == []


class TestRecorded:
    @pytest.fixture
    def nautobot_with(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        pages = {}

        def fake_fetch(endpoint, params=None, **kwargs):
            return [dict(record) for record in pages.get(endpoint, [])]

        monkeypatch.setattr(nautobot, "fetch_all_pages", fake_fetch)
        return pages

    def _device(self, **changes):
        device = next(iter(mock_nautobot.DEVICES.values()))[0]
        return {**device, **changes}

    def test_a_sync_records_logs_and_counts_drift(self, nautobot_with, caplog):
        nautobot_with["dcim/locations/"] = [mock_nautobot.LOCATIONS[0]]
        nautobot_with["dcim/devices/"] = [self._device(), self._device(id="d2", primary_ip4="10.0.0.9/24", role=7)]
        caplog.set_level(logging.WARNING)
        inventory.sync_nautobot(force=True)

        warnings = [r.getMessage() for r in caplog.records if "Data contract" in r.getMessage()]
        assert warnings == [
            "Data contract: nautobot dcim/devices/ does not match in 2 records: "
            "role is integer, expected object, string or null (1) (see docs/data-contract.md)"
        ]
        conn = db.get_conn()
        try:
            checks = {(c["source"], c["endpoint"]): c for c in contract.latest_checks(conn)}
            scraped = metrics.collect(conn)
        finally:
            conn.close()
        assert checks[("nautobot", "dcim/devices/")]["mismatches"] == [
            {"field": "role", "problem": "is integer, expected object, string or null", "count": 1}
        ]
        assert checks[("nautobot", "dcim/locations/")]["mismatches"] == []
        assert (
            checks[("nautobot", "dcim/locations/")]["records"] == 1
            and checks[("nautobot", "dcim/locations/")]["checked_at"]
        )
        assert 'nautobot_maps_contract_mismatches{source="nautobot",endpoint="dcim/devices/",field="role"} 1' in scraped

        flask_app.app.config["TESTING"] = True
        caching.cache.clear()
        with flask_app.app.test_client() as client:
            body = client.get("/api/contract").get_json()
        assert any(e["endpoint"] == "dcim/devices/" for e in body["contract"])
        assert any(c["endpoint"] == "dcim/devices/" and c["mismatches"] for c in body["checks"])

        # Fixed upstream: the next sync clears it and says so.
        nautobot_with["dcim/devices/"] = [self._device()]
        caplog.clear()
        caplog.set_level(logging.INFO)
        inventory.sync_nautobot(force=True)
        assert any("dcim/devices/ matches again" in r.getMessage() for r in caplog.records)
        conn = db.get_conn()
        try:
            assert "nautobot_maps_contract_mismatches{" not in metrics.collect(conn)
        finally:
            conn.close()

    def test_librenms_sync_and_push_are_checked(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.test")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "t")
        monkeypatch.setattr(librenms, "fetch_inventory", lambda: [{"device_id": 1, "hostname": "a", "status": "1"}])
        inventory.sync_librenms(force=True)
        monkeypatch.setattr(librenms, "fetch_device", lambda device: {"device_id": 1, "hostname": "a", "status": 1})
        inventory.librenms_push_refresh("1")
        conn = db.get_conn()
        try:
            checks = {c["endpoint"]: c for c in contract.latest_checks(conn)}
        finally:
            conn.close()
        assert checks["devices?type=all"]["mismatches"] == [
            {"field": "status", "problem": "is string, expected integer or boolean", "count": 1}
        ]
        assert checks["devices/<id or hostname>"]["mismatches"] == []

    def test_a_broken_check_never_stops_a_sync(self, monkeypatch, caplog):
        monkeypatch.setattr(contract, "check", lambda spec, records: 1 / 0)
        assert contract.check_and_record(contract.NAUTOBOT, "dcim/devices/", [{}]) == {"records": 0, "mismatches": []}
        assert "Data contract check for nautobot dcim/devices/ failed" in caplog.text


class Tracked(dict):
    """A record that notes every key read from it (dotted for nested objects)."""

    def __init__(self, data, seen, prefix=""):
        super().__init__()
        self.seen, self.prefix = seen, prefix
        for key, value in data.items():
            dict.__setitem__(self, key, Tracked(value, seen, f"{prefix}{key}.") if isinstance(value, dict) else value)

    def _note(self, key):
        self.seen.add(f"{self.prefix}{key}")

    def get(self, key, default=None):
        self._note(key)
        return dict.get(self, key, default)

    def __getitem__(self, key):
        self._note(key)
        return dict.__getitem__(self, key)

    def __contains__(self, key):
        self._note(key)
        return dict.__contains__(self, key)


def brief(record: dict) -> dict:
    """Nested objects as Nautobot 3.x sends them at depth 0: only an id, so
    every fallback key gets read."""
    return {key: {"id": "x"} if isinstance(value, dict) else value for key, value in record.items()}


class TestEveryFieldReadIsDeclared:
    """Each key the sync code reads from an upstream record is in the contract,
    so drift in any of them is reported (and documented)."""

    def _assert_declared(self, source, path, seen):
        declared = {f.path for f in contract.endpoint(source, path).fields}
        assert seen - declared == set(), f"{path}: read but not in contract.py"

    def _records(self, data, seen):
        return [Tracked(record, seen) for record in data] + [Tracked(brief(record), seen) for record in data]

    def test_locations(self, monkeypatch):
        monkeypatch.setattr(nautobot, "id_name_map", lambda endpoint: {})
        monkeypatch.setattr(nautobot, "tenant_group_map", lambda: {})
        seen = set()
        extra = {**mock_nautobot.LOCATIONS[0], "country": {"id": "c"}, "physical_address": ""}
        inventory.normalize_locations(
            self._records([*mock_nautobot.LOCATIONS, extra], seen), include_without_coordinates=True
        )
        self._assert_declared(contract.NAUTOBOT, "dcim/locations/", seen)

    def test_devices(self):
        seen = set()
        devices = [d for group in mock_nautobot.DEVICES.values() for d in group]
        no_ip = {**devices[0], "primary_ip4": None}
        nested_manufacturer = {**devices[0], "device_type": {"id": "t", "manufacturer": {"id": "m"}}}
        inventory.normalize_devices(self._records([*devices, no_ip, nested_manufacturer], seen), lookup_maps={})
        self._assert_declared(contract.NAUTOBOT, "dcim/devices/", seen)

    def test_tenants_relationships_and_lookups(self, monkeypatch):
        seen = {}

        def fake_fetch(endpoint, params=None, **kwargs):
            records = {
                "tenancy/tenants/": [{"id": "t1", "name": "", "tenant_group": {"id": "g"}}, {"id": "t2", "name": "A"}],
                "extras/relationships/": [
                    {"id": "r1", "source_type": "dcim.location", "destination_type": "tenancy.tenant", "key": ""},
                ],
                "extras/relationship-associations/": [{"source_id": "l1", "destination_id": "t1"}],
                "dcim/device-types/": [{"id": "dt", "model": "", "manufacturer": {"id": "m"}}],
            }.get(endpoint, [{"id": "x"}])
            return [Tracked(record, seen.setdefault(endpoint, set())) for record in records]

        monkeypatch.setattr(nautobot, "fetch_all_pages", fake_fetch)
        monkeypatch.setattr(contract, "check_and_record", lambda *args: None)
        inventory.fetch_tenants()
        inventory.fetch_location_tenant_links()
        nautobot.tenant_group_map()
        nautobot.device_type_maps()
        for path in inventory.LOOKUP_ENDPOINTS:
            if path != "dcim/device-types/":  # read by device_type_maps above
                nautobot.id_name_map(path)
        for path, keys in seen.items():
            self._assert_declared(contract.NAUTOBOT, path, keys)

    def test_librenms_devices(self):
        seen = set()

        class FakeConn:
            def execute(self, sql, params=()):
                return type("Result", (), {"fetchone": lambda self: None})()

        devices = [{"device_id": 1, "hostname": "a", "status": 1, "ip": "10.0.0.1"}]
        tracked = [Tracked(device, seen) for device in devices]
        inventory.write_librenms_devices(FakeConn(), tracked, 1)
        inventory._pushed_device(tracked[0])
        self._assert_declared(contract.LIBRENMS, "devices?type=all", seen)


class TestCountry:
    """The location's country is stored and read back (#309: the contract
    showed it was worked out but dropped)."""

    def test_round_trip(self, pg_database):
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                inventory.write_locations(
                    conn,
                    [{"id": "l1", "name": "Oslo", "latitude": 59.9, "longitude": 10.7, "country": "Norway"}],
                )
        finally:
            conn.close()
        (location,) = inventory.read_locations()
        assert location["country"] == "Norway"


class TestStorage:
    def _check(self, path):
        conn = db.get_conn()
        try:
            return next(c for c in contract.latest_checks(conn) if c["endpoint"] == path)
        finally:
            conn.close()

    def test_an_empty_incremental_fetch_keeps_the_previous_result(self, pg_database):
        contract.check_and_record(contract.NAUTOBOT, "dcim/devices/", [{"id": 1}])
        contract.check_and_record(contract.NAUTOBOT, "dcim/devices/", [], incremental=True)  # nothing changed
        check = self._check("dcim/devices/")
        assert check["records"] == 1 and check["mismatches"]

    def test_an_empty_full_fetch_is_the_truth(self, pg_database):
        contract.check_and_record(contract.NAUTOBOT, "tenancy/tenants/", [{"id": 1}])
        contract.check_and_record(contract.NAUTOBOT, "tenancy/tenants/", [])  # the bad tenant was deleted
        check = self._check("tenancy/tenants/")
        assert check["records"] == 0 and check["mismatches"] == []

    def test_no_database_connection_never_stops_a_sync(self, monkeypatch, caplog):
        def refused(*args, **kwargs):
            raise OSError("connection refused")

        monkeypatch.setattr(db, "get_conn", refused)
        result = contract.check_and_record(contract.NAUTOBOT, "tenancy/tenants/", [{"id": 1}])
        assert result["mismatches"] and "Could not store the data contract check" in caplog.text

    def test_concurrent_writers_leave_one_result(self, pg_database):
        import threading

        threads = [
            threading.Thread(
                target=contract.check_and_record,
                args=(
                    contract.LIBRENMS,
                    "devices/<id or hostname>",
                    [{"device_id": "x", "hostname": "a", "status": 1}],
                ),
            )
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        conn = db.get_conn()
        try:
            rows = conn.execute("SELECT field FROM contract_checks ORDER BY field").fetchall()
        finally:
            conn.close()
        assert [db.row_to_dict(row)["field"] for row in rows] == ["", "device_id"]
