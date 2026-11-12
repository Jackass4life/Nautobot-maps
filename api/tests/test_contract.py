"""The data contract (#309, #598), ported from the current app's tests/test_contract.py.
The parts that run a sync come with the inventory sync (step 5c of #587)."""

import logging
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import session
from app.services import contract_service as contract
from tests import db as adb

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "data-contract.md"
DEVICES = contract.endpoint(contract.NAUTOBOT, "dcim/devices/")
sys.path.insert(0, str(REPO_ROOT / "demo"))

import mock_nautobot  # noqa: E402 - the demo's mock Nautobot, from demo/


def test_it_renders_the_docs_of_the_current_app():
    """docs/data-contract.md is generated from the current app's contract; this one
    renders the same, so the two declarations are the same."""
    assert DOC.read_text(encoding="utf-8") == contract.render_markdown()


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


class TestMockNautobot:
    """The demo's mock Nautobot keeps to the contract, as a real one must."""

    @pytest.mark.parametrize(
        "path",
        [e.path for e in contract.CONTRACT if e.source == contract.NAUTOBOT and e.checked],
    )
    def test_endpoint(self, path):
        client = TestClient(mock_nautobot.app)
        response = client.get(
            f"/api/{path}?limit=1000&depth=1", headers={"Authorization": "Token x"}
        )
        if response.status_code == 404:
            pytest.skip(f"the mock has no {path}")
        records = response.json()["results"]
        assert records, path
        spec = contract.endpoint(contract.NAUTOBOT, path)
        assert contract.check(spec, records)["mismatches"] == []


def latest(path: str) -> dict:
    return next(c for c in adb.call(contract.latest_checks) if c["endpoint"] == path)


class TestStorage:
    def test_logged_once_and_stored(self, caplog):
        caplog.set_level(logging.WARNING)
        contract.check_and_record(contract.NAUTOBOT, "dcim/devices/", [{"id": 1}, {"id": 2}])
        warnings = [r.getMessage() for r in caplog.records if "Data contract" in r.getMessage()]
        assert len(warnings) == 1
        assert warnings[0].startswith(
            "Data contract: nautobot dcim/devices/ does not match in 2 records: id is integer"
        )
        check = latest("dcim/devices/")
        assert check["records"] == 2 and check["checked_at"].endswith("Z")
        assert {"field": "id", "problem": "is integer, expected string", "count": 2} in check[
            "mismatches"
        ]

    def test_matches_again_is_logged(self, caplog):
        contract.check_and_record(contract.NAUTOBOT, "tenancy/tenants/", [{"id": 1}])
        caplog.set_level(logging.INFO)
        contract.check_and_record(
            contract.NAUTOBOT, "tenancy/tenants/", [{"id": "t1", "name": "Acme"}]
        )
        assert "Data contract: nautobot tenancy/tenants/ matches again" in caplog.text

    def test_an_empty_incremental_fetch_keeps_the_previous_result(self):
        contract.check_and_record(contract.NAUTOBOT, "dcim/devices/", [{"id": 1}])
        contract.check_and_record(contract.NAUTOBOT, "dcim/devices/", [], incremental=True)
        check = latest("dcim/devices/")
        assert check["records"] == 1 and check["mismatches"]

    def test_an_empty_full_fetch_is_the_truth(self):
        contract.check_and_record(contract.NAUTOBOT, "tenancy/tenants/", [{"id": 1}])
        contract.check_and_record(contract.NAUTOBOT, "tenancy/tenants/", [])  # deleted
        check = latest("tenancy/tenants/")
        assert check["records"] == 0 and check["mismatches"] == []

    def test_no_database_never_stops_a_sync(self, monkeypatch, caplog):
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        unreachable = create_async_engine(
            "postgresql+asyncpg://x:y@127.0.0.1:1/none",
            poolclass=NullPool,
            connect_args={"timeout": 1},
        )
        monkeypatch.setattr(session, "async_engine", unreachable)
        result = contract.check_and_record(contract.NAUTOBOT, "tenancy/tenants/", [{"id": 1}])
        assert result["mismatches"] and "Could not store the data contract check" in caplog.text

    def test_a_broken_check_never_stops_a_sync(self, monkeypatch, caplog):
        monkeypatch.setattr(contract, "check", lambda spec, records: 1 / 0)
        result = contract.check_and_record(contract.NAUTOBOT, "dcim/devices/", [{}])
        assert result == {"records": 0, "mismatches": []}
        assert "Data contract check for nautobot dcim/devices/ failed" in caplog.text

    def test_concurrent_writers_leave_one_result(self):
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
        rows = adb.sql("SELECT field FROM contract_checks ORDER BY field")
        assert [row["field"] for row in rows] == ["", "device_id"]


def test_the_api_answers_the_contract_and_the_checks(client):
    contract.check_and_record(contract.NAUTOBOT, "dcim/devices/", [{"id": 1}])
    body = client.get("/api/v1/contract").json()
    assert len(body["contract"]) == len(contract.CONTRACT)
    devices = next(e for e in body["contract"] if e["endpoint"] == "dcim/devices/")
    assert devices["source"] == "nautobot" and devices["fields"][0]["path"]
    (check,) = body["checks"]
    assert check["endpoint"] == "dcim/devices/" and check["mismatches"]
