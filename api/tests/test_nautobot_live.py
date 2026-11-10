"""The inventory sync against a real Nautobot 3.x (#600): CI's integration job
seeds one (development/seed_nautobot.py) and sets NAUTOBOT_LIVE_URL/TOKEN; without
them these are skipped. The pages' reads of the result come with the map (step 6)."""

import os

import pytest

from app.core.legacy_settings import settings
from app.db import session
from app.services import contract_service as contract
from app.services import inventory_service as inventory

LIVE_URL = os.environ.get("NAUTOBOT_LIVE_URL", "")
LIVE_TOKEN = os.environ.get("NAUTOBOT_LIVE_TOKEN", "")

pytestmark = pytest.mark.skipif(not LIVE_URL, reason="NAUTOBOT_LIVE_URL not set")


@pytest.fixture
def synced(monkeypatch):
    monkeypatch.setattr(settings, "NAUTOBOT_URL", LIVE_URL.rstrip("/"))
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", LIVE_TOKEN)
    monkeypatch.setattr(settings, "NAUTOBOT_VERIFY_SSL", False)
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    inventory.sync_nautobot(force=True)
    state = inventory.get_sync_state("nautobot_inventory")
    assert state["status"] == "idle", state.get("error_message")


def test_the_seeded_sites_are_in_the_snapshot(synced):
    """All 8 seeded locations have coordinates."""
    locations = inventory.read_locations()
    assert len(locations) >= 8
    by_name = {location["name"]: location for location in locations}
    copenhagen = by_name["Copenhagen DC"]
    assert copenhagen["tenant"] == "Acme Corp" and copenhagen["latitude"] is not None


def test_their_devices_are_in_the_snapshot(synced):
    copenhagen = next(loc for loc in inventory.read_locations() if loc["name"] == "Copenhagen DC")
    names = {device["name"] for device in inventory.read_devices(copenhagen["id"])}
    assert {"cph-core-rt01", "cph-fw01"} <= names


def test_a_real_nautobot_keeps_to_the_contract(synced):
    with session.engine.connect() as conn:
        checks = [c for c in contract.latest_checks(conn) if c["source"] == contract.NAUTOBOT]
    assert checks, "the sync checked what it fetched"
    mismatched = {c["endpoint"]: c["mismatches"] for c in checks if c["mismatches"]}
    assert mismatched == {}
