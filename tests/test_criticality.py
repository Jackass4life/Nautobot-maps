"""Device and site criticality from Nautobot: a device custom field and the site tier (#377)."""

import os
import subprocess
import sys

import pytest

from nautobot_maps import alerts, caching, db, inventory, settings
from tests.test_app import _snapshot_device, _snapshot_location

NOW = "2026-10-18T12:00:00Z"


def device(name, status="Active", role="Access Switch", criticality="", device_id=None):
    return {"id": device_id or name, "name": name, "role": role, "status": status, "criticality": criticality}


def level(devices, tier="", override_map=None, location_type=None):
    return alerts.compute_alert_level(devices, location_type, override_map=override_map or {}, tier=tier)


@pytest.fixture
def tiers(monkeypatch):
    monkeypatch.setattr(settings, "TIER_DEVICE_LEVEL", {"gold": "critical", "silver": "medium"})


class TestDeviceLevel:
    def test_the_first_that_applies_wins(self, tiers):
        keywords = ("core",)
        core = device("core01", role="Core Router")
        assert alerts.device_level(core, keywords, {}) == ("critical", "role")
        assert alerts.device_level(core, keywords, {}, "silver") == ("critical", "role"), "a tier never lowers a core"
        assert alerts.device_level(device("sw1"), keywords, {}, "silver") == ("medium", "tier")
        # Not critical by override: the role no longer counts, the tier still does.
        assert alerts.device_level(core, keywords, {"core01": False}) == ("", "")
        assert alerts.device_level(core, keywords, {"core01": False}, "silver") == ("medium", "tier")
        assert alerts.device_level(device("sw1"), keywords, {"sw1": True}, "silver") == ("critical", "override")
        # The Nautobot field wins over everything, in either direction.
        low_core = {**core, "criticality": "low"}
        assert alerts.device_level(low_core, keywords, {"core01": True}, "gold") == ("low", "nautobot")
        assert alerts.device_level(device("sw1", criticality="critical"), keywords, {}, "") == (
            "critical",
            "nautobot",
        )
        assert alerts.device_level(device("sw1"), keywords, {}, "bronze") == ("", ""), "a tier not listed"

    def test_a_low_tier_keeps_core_devices_critical(self, monkeypatch):
        monkeypatch.setattr(settings, "TIER_DEVICE_LEVEL", {"bronze": "low"})
        devices = [device("core01", "Offline", role="Core Router")] + [device(f"sw{n}") for n in range(9)]
        assert level(devices, "bronze") == {"level": "critical", "reason": "Core device(s) offline: core01"}

    def test_read_from_the_custom_field(self, monkeypatch):
        monkeypatch.setattr(settings, "DEVICE_CRITICALITY_FIELD", "criticality")
        read = inventory.device_criticality
        assert read({"custom_fields": {"criticality": "Critical"}}) == "critical"
        assert read({"custom_fields": {"criticality": {"value": "low", "label": "Low"}}}) == "low"
        assert read({"custom_fields": {"criticality": "urgent"}}) == "", "not a level: ignored"
        assert read({"custom_fields": None}) == ""
        monkeypatch.setattr(settings, "DEVICE_CRITICALITY_FIELD", "")
        assert read({"custom_fields": {"criticality": "critical"}}) == ""

    def test_the_tier_setting_keeps_known_levels(self):
        code = "from nautobot_maps import settings as s; print(s.TIER_DEVICE_LEVEL)"
        env = {**os.environ, "TIER_DEVICE_LEVEL": "Gold:Critical, silver:medium,bronze:urgent,:low"}
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        assert out.stdout.strip() == "{'gold': 'critical', 'silver': 'medium'}"
        assert "'bronze:urgent' ignored" in out.stderr and "':low' ignored" in out.stderr, "a typo is logged"

    def test_setting_a_field_forces_a_full_resync(self, monkeypatch):
        """Not a wait for the daily reconcile: the stored devices don't have the field yet."""
        state = {"cache_version": inventory.cache_version()}
        assert not inventory.cache_version_mismatch(state)
        monkeypatch.setattr(settings, "DEVICE_CRITICALITY_FIELD", "criticality")
        assert inventory.cache_version_mismatch(state)
        assert not inventory.cache_version_mismatch({"cache_version": inventory.cache_version()})

    def test_the_fingerprint_is_the_edits(self, tiers):
        """A plain device added or put in maintenance doesn't look like an edit; a tier or field change does."""
        site = {"tier": "gold"}
        devices = [device("sw0", "Offline"), device("sw1")]
        key = alerts.site_level_keys(devices, devices[:1], site, {})
        assert alerts.site_level_keys([*devices, device("sw2")], devices[:1], site, {}) == key
        assert alerts.site_level_keys(devices, devices[:1], {"tier": "silver"}, {})[0] != key[0]
        assert alerts.site_level_keys(devices, devices[:1], {"tier": "bronze"}, {})[0] != key[0]
        marked = [devices[0], device("sw1", criticality="low")]
        assert alerts.site_level_keys(marked, devices[:1], site, {})[0] != key[0]
        assert alerts.site_level_keys(devices, devices[:1], site, {"sw1": True})[0] != key[0]
        assert alerts.site_level_keys(devices, [], site, {})[1] != key[1]


class TestSiteLevel:
    def test_nothing_set_is_as_before(self):
        devices = [device(f"sw{n}", "Offline" if n == 0 else "Active") for n in range(40)]
        assert level(devices) == {"level": "low", "reason": "1/40 devices offline (2%)"}
        devices[1] = device("core01", "Offline", role="Core Router")
        assert level(devices) == {"level": "critical", "reason": "Core device(s) offline: core01"}

    def test_a_gold_site_with_lab_devices_set_low(self, tiers):
        """300 devices at a gold site: a lab switch down is Low, the firewall Critical."""
        devices = [device(f"ap{n}") for n in range(290)]
        devices += [device(f"lab{n}", criticality="low") for n in range(9)]
        devices.append(device("fw01", role="Firewall"))
        devices[290] = device("lab0", "Offline", criticality="low")
        assert level(devices, "gold") == {"level": "low", "reason": "1/300 devices offline (0%)"}
        devices[-1] = device("fw01", "Offline", role="Firewall")
        assert level(devices, "gold") == {
            "level": "critical",
            "reason": "Critical device(s) offline: fw01 (gold site)",
        }

    def test_a_critical_device_on_a_plain_site(self):
        devices = [device(f"sw{n}") for n in range(20)] + [device("cust-fw", "Offline", criticality="critical")]
        assert level(devices) == {
            "level": "critical",
            "reason": "Critical device(s) offline: cust-fw (set in Nautobot)",
        }

    def test_a_silver_site_is_medium_for_one_device(self, tiers):
        devices = [device("sw0", "Offline")] + [device(f"sw{n}") for n in range(1, 40)]
        assert level(devices, "silver") == {"level": "medium", "reason": "Medium device(s) offline: sw0 (silver site)"}

    def test_the_share_down_still_raises_it(self):
        """Many devices set low, down: their share decides (more than 25%: Medium)."""
        devices = [device(f"lab{n}", "Offline", criticality="low") for n in range(5)] + [
            device(f"sw{n}") for n in range(5)
        ]
        assert level(devices)["level"] == "medium"
        assert level([device(f"lab{n}", "Offline", criticality="low") for n in range(3)])["level"] == "critical"

    def test_a_core_set_low_in_nautobot_is_not_critical(self):
        devices = [device("core01", "Offline", role="Core Router", criticality="low")] + [
            device(f"sw{n}") for n in range(9)
        ]
        assert level(devices) == {"level": "low", "reason": "1/10 devices offline (10%)"}

    def test_many_critical_devices_are_summarised(self, tiers):
        devices = [device(f"sw{n:02}", "Offline") for n in range(34)] + [device(f"ok{n}") for n in range(100)]
        result = level(devices, "gold")
        assert result["reason"] == "Critical device(s) offline: sw00, sw01, sw02 (+31 more) (gold site)"


# ---------------------------------------------------------------------------
# Through board builds: a level change from an edit is logged, not notified.
# ---------------------------------------------------------------------------


def write(sites: dict[str, str], devices: list[dict]) -> None:
    """*sites*: {site id: tier}; *devices*: snapshot devices."""
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            inventory.write_locations(
                conn, [{**_snapshot_location(sid, f"Site {sid}"), "tier": tier} for sid, tier in sites.items()]
            )
            inventory.write_devices(conn, devices)
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_completed_at=NOW,
                last_successful_sync=NOW,
                cache_version=inventory.CACHE_VERSION,
            )
    finally:
        conn.close()


def site_devices(site_id: str, down: int = 1, total: int = 5, criticality: str = "") -> list[dict]:
    return [
        {
            **_snapshot_device(f"{site_id}-sw{n}", site_id, "Access Switch", "Offline" if n < down else "Active"),
            "criticality": criticality,
        }
        for n in range(total)
    ]


def build(clock, at: str) -> dict:
    clock["now"] = at
    caching.cache.clear()
    return {row["id"]: row for row in alerts.build_alert_board_payload(snapshot_only=True)["alerts"]}


def query(sql: str) -> list[dict]:
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            return [db.row_to_dict(row) for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def outbox_sites() -> list[str]:
    return [row["site_id"] for row in query("SELECT site_id FROM notification_outbox ORDER BY id")]


class TestEditsAreNotNotified:
    def test_a_bulk_tier_change_on_200_sites_pages_nobody(self, board, tiers):
        """200 sites with a switch down go gold (Low → Critical): 200 feed entries, no message.
        Then a device going down at a quiet gold site is notified as usual."""
        sites = {f"s{n:03}": "" for n in range(200)}
        devices = [d for sid in sites for d in site_devices(sid)]
        write({**sites, "quiet": ""}, devices + site_devices("quiet", down=0))
        rows = build(board, "2026-10-18T12:00:00Z")
        assert rows["s000"]["alert_level"] == "low" and rows["quiet"]["alert_level"] == "ok"

        write({**dict.fromkeys(sites, "gold"), "quiet": "gold"}, [])
        rows = build(board, "2026-10-18T12:01:00Z")
        assert {rows[sid]["alert_level"] for sid in sites} == {"critical"}
        assert len(query("SELECT * FROM site_level_changes WHERE to_level = 'critical'")) == 200
        assert outbox_sites() == []

        write({}, site_devices("quiet", down=1))
        build(board, "2026-10-18T12:02:00Z")
        assert outbox_sites() == ["quiet"]

    def test_an_edit_and_a_device_down_in_the_same_build_is_notified(self, board, tiers):
        write({"a": ""}, site_devices("a", down=0))
        build(board, "2026-10-18T12:00:00Z")
        write({"a": "gold"}, site_devices("a", down=1))
        assert build(board, "2026-10-18T12:01:00Z")["a"]["alert_level"] == "critical"
        assert outbox_sites() == ["a"]

    def test_a_device_set_critical_while_down(self, board):
        write({"a": ""}, site_devices("a", down=1))
        build(board, "2026-10-18T12:00:00Z")
        write({}, [{**site_devices("a", down=1)[0], "criticality": "critical"}])
        row = build(board, "2026-10-18T12:01:00Z")["a"]
        assert row["alert_level"] == "critical" and "(set in Nautobot)" in row["alert_reason"]
        assert outbox_sites() == [], "logged in the feed only"
        assert [r["to_level"] for r in query("SELECT to_level FROM site_level_changes")] == ["critical"]

    def test_changes_without_an_edit_are_notified_as_before(self, board):
        write({"a": ""}, site_devices("a", down=1))
        build(board, "2026-10-18T12:00:00Z")
        write({}, site_devices("a", down=5))
        assert build(board, "2026-10-18T12:01:00Z")["a"]["alert_level"] == "critical"
        assert outbox_sites() == ["a"]

    def test_the_first_build_after_the_upgrade_notifies_as_before(self, board, tiers):
        """Rows from before #377 have no fingerprints: nothing is known, so a change is notified
        (a real outage during the upgrade must still page), and the fingerprints are stored."""
        write({"a": ""}, site_devices("a", down=1))
        build(board, "2026-10-18T12:00:00Z")
        query("UPDATE site_alert_levels SET criticality_key = NULL, down_key = NULL RETURNING site_id")
        write({"a": "gold"}, [])
        assert build(board, "2026-10-18T12:01:00Z")["a"]["alert_level"] == "critical"
        assert outbox_sites() == ["a"]
        stored = query("SELECT criticality_key, down_key FROM site_alert_levels")
        assert stored[0]["criticality_key"] and stored[0]["down_key"]

    def test_the_field_is_stored_and_read_back(self, board):
        write({"a": ""}, [{**site_devices("a")[0], "criticality": "medium"}])
        (stored,) = inventory.read_devices("a")
        assert stored["criticality"] == "medium"


def test_the_board_marks_a_level_set_in_nautobot(board):
    """Only a device's own field gets a mark; a tier default on every device would be clutter."""
    devices = site_devices("a", down=2)
    devices[0] = {**devices[0], "criticality": "low"}
    write({"a": "gold"}, devices)
    marks = {d["device_id"]: d.get("set_level") for d in build(board, NOW)["a"]["down_devices"]}
    assert marks == {"a-sw0": "low", "a-sw1": None}


def test_the_set_level_badge():
    from tests.js_modules import run_js

    run_js(
        """
import { setLevelBadge } from "@js/alerts/rows.js";
check(setLevelBadge({}) === "", "nothing set");
check(setLevelBadge({ set_level: "<b>" }) === "", "only known levels");
const html = setLevelBadge({ set_level: "low" });
check(html.includes("SET LOW") && html.includes('title="Set low in Nautobot'), html);
"""
    )
