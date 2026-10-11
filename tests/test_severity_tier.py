"""Configurable severity thresholds and a site importance tier (#293)."""

import shutil
import subprocess

import pytest

from nautobot_maps import alerts, inventory, notify, settings
from tests import test_maintenance
from tests.test_integration import REPO_ROOT, _extract_js_function
from tests.test_maintenance import build

clock = test_maintenance.clock
site = test_maintenance.site


def devices(down: int, total: int) -> list[dict]:
    return [
        {"id": f"d{n}", "name": f"sw{n}", "role": "Access", "status": "Offline" if n < down else "Active"}
        for n in range(total)
    ]


class TestThresholds:
    def test_defaults_as_before(self):
        assert alerts.compute_alert_level(devices(1, 4), override_map={})["level"] == "low"  # 25 %: not more than 25
        assert alerts.compute_alert_level(devices(2, 4), override_map={})["level"] == "medium"
        assert alerts.compute_alert_level(devices(3, 4), override_map={})["level"] == "medium"
        assert alerts.compute_alert_level(devices(4, 4), override_map={})["level"] == "critical"

    def test_configured(self, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_MEDIUM_PERCENT", 50)
        monkeypatch.setattr(settings, "ALERT_CRITICAL_PERCENT", 75)
        assert alerts.compute_alert_level(devices(2, 4), override_map={})["level"] == "low"  # 50 %: not more than 50
        result = alerts.compute_alert_level(devices(3, 4), override_map={})
        assert result == {"level": "critical", "reason": "3/4 devices offline (75%)"}
        definitions = alerts.alert_level_definitions()
        assert "More than 50%" in definitions["medium"] and "75% or more of them are down" in definitions["critical"]

    def test_the_settings_are_kept_in_range(self, monkeypatch):
        import os
        import sys

        code = "from nautobot_maps import settings as s; print(s.ALERT_MEDIUM_PERCENT, s.ALERT_CRITICAL_PERCENT)"
        env = {**os.environ, "ALERT_MEDIUM_PERCENT": "0", "ALERT_CRITICAL_PERCENT": "500"}
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        assert out.stdout.split() == ["1", "100"]
        env = {**os.environ, "ALERT_MEDIUM_PERCENT": "60", "ALERT_CRITICAL_PERCENT": "40"}
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        assert out.stdout.split() == ["60", "61"], "critical stays above medium"


class TestTier:
    def test_read_from_the_custom_field(self, monkeypatch):
        monkeypatch.setattr(settings, "SITE_TIER_FIELD", "tier")
        assert inventory.location_tier({"custom_fields": {"tier": "Gold"}}) == "gold"
        assert inventory.location_tier({"custom_fields": {"tier": {"value": "silver", "label": "Silver"}}}) == "silver"
        assert inventory.location_tier({"custom_fields": {"tier": None}}) == ""
        assert inventory.location_tier({"custom_fields": None}) == ""
        monkeypatch.setattr(settings, "SITE_TIER_FIELD", "")
        assert inventory.location_tier({"custom_fields": {"tier": "gold"}}) == ""

    def test_rank(self, monkeypatch):
        monkeypatch.setattr(settings, "SITE_TIERS", ["gold", "silver"])
        assert [alerts.tier_rank(t) for t in ("gold", "Silver", "bronze", "")] == [0, 1, 99, 99]

    def test_on_the_board_and_sorting(self, site, monkeypatch):
        from nautobot_maps import db
        from tests.test_app import _snapshot_location

        conn = db.get_conn()
        try:
            with db.transaction(conn):
                inventory.write_locations(conn, [{**_snapshot_location("loc-lon", "London HQ"), "tier": "gold"}])
        finally:
            conn.close()
        row = build()
        assert (row["tier"], row["tier_rank"]) == ("gold", 0)
        monkeypatch.setattr(settings, "SITE_TIERS", ["platinum"])
        row = build()
        assert (row["tier"], row["tier_rank"]) == ("", 99), "not a configured tier: none"


class TestNotifications:
    def test_a_gold_site_notifies_sooner(self, monkeypatch):
        monkeypatch.setattr(settings, "NOTIFY_MIN_LEVEL", "critical")
        monkeypatch.setattr(settings, "NOTIFY_MIN_LEVEL_BY_TIER", {"gold": "low"})
        assert notify.event_kind("ok", "low", "gold") == "alarm"
        assert notify.event_kind("ok", "low", "silver") is None
        assert notify.event_kind("low", "ok", "gold") == "recovery"
        event = notify.build_event(
            "alarm", "ok", "low", {"id": "s", "name": "S", "tier": "gold"}, "2026-10-10T12:00:00Z"
        )
        assert "Tier: Gold" in notify.detail_lines(event)
        assert notify.summary_lines([event])[0].startswith("🔴 S [Gold]: "), "also in a burst's summary"

    def test_an_override_without_a_tier_is_ignored(self):
        import os
        import sys

        code = "from nautobot_maps import settings as s; print(s.NOTIFY_MIN_LEVEL_BY_TIER)"
        env = {**os.environ, "NOTIFY_MIN_LEVEL_BY_TIER": ":low, gold:low,silver:bogus"}
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        assert out.stdout.strip() == "{'gold': 'low'}"


def test_the_tier_label_and_sort():
    if shutil.which("node") is None:
        pytest.skip("node is required")
    js = (REPO_ROOT / "static" / "js" / "alerts.js").read_text(encoding="utf-8")
    import re

    order = re.search(r"const SEVERITY_ORDER = \[[^\]]*\];", js).group(0)
    names = ("escHtml", "tierPill", "severityWeight", "compareText", "latestDownTime", "compareSites")
    script = (
        "function check(c, m) { if (!c) throw new Error(m); }\n"
        + order
        + "\n"
        + "\n".join(_extract_js_function(js, n) for n in names)
        + """
check(tierPill({ tier: "gold", tier_rank: 0 }).includes('class="tier-pill tier-top"') && tierPill({ tier: "gold", tier_rank: 0 }).includes(">GOLD<"), "top tier");
check(tierPill({ tier: "silver", tier_rank: 1 }).includes('class="tier-pill"'), "other tier");
check(tierPill({}) === "", "none");
const sites = [
  { name: "Office", alert_level: "critical", tier_rank: 99 },
  { name: "Hospital", alert_level: "critical", tier_rank: 0 },
  { name: "Depot", alert_level: "medium", tier_rank: 0 },
];
sites.sort((a, b) => compareSites(a, b, "severity"));
check(sites.map((s) => s.name).join() === "Hospital,Office,Depot", sites.map((s) => s.name).join());
"""
    )
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr or completed.stdout
