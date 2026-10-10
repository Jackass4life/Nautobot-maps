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
