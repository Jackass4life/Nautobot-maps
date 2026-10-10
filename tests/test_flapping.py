"""Flapping (#286): a device going up and down is one incident with a label."""

import os
import shutil
import subprocess

import pytest

from nautobot_maps import alerts, caching, settings
from tests import test_alert_delay
from tests.test_alert_delay import LONG_AGO, build, rows, set_devices
from tests.test_integration import REPO_ROOT, _extract_js_function

# The alert delay tests' fixtures: London HQ, all up for long, and a clock.
clock = test_alert_delay.clock
site = test_alert_delay.site


def at(clock, minute: int, acc1: str):
    """acc1's status at 12:MM (Nautobot changed it then); the others stay up."""
    now = f"2026-10-10T12:{minute:02d}:00Z"
    clock["now"] = now
    set_devices(core=("Active", LONG_AGO), acc1=(acc1, now), acc2=("Active", LONG_AGO))
    return build()


@pytest.fixture
def flapping(site, monkeypatch):
    monkeypatch.setattr(settings, "ALERT_DELAY_SECONDS", 0)
    monkeypatch.setattr(settings, "FLAP_CHANGES", 4)


def acc1(row):
    return next((d for d in row["down_devices"] if d["device_name"] == "acc1"), None)


class TestFlapping:
    def test_a_bouncing_device_is_one_incident_with_a_label_and_a_steady_level(self, flapping, clock):
        at(clock, 0, "Offline")  # 1 change: alarms as usual
        at(clock, 2, "Active")  # 2: recovers as usual
        assert rows("SELECT status FROM alert_instances") == [{"status": "resolved"}]
        at(clock, 4, "Offline")  # 3: a new incident
        row = at(clock, 6, "Active")  # 4 changes in 30 min: flapping
        assert row["alert_level"] != "ok", "counts as down while momentarily up"
        assert acc1(row) == {**acc1(row), "flapping": True, "flap_changes": 4, "status": "Flapping"}
        level_changes = len(rows("SELECT id FROM site_level_changes"))

        for minute, status in ((8, "Offline"), (10, "Active"), (12, "Offline"), (14, "Active")):
            row = at(clock, minute, status)
            assert acc1(row)["flapping"] and row["alert_level"] != "ok"
        assert rows("SELECT status FROM alert_instances ORDER BY id") == [{"status": "resolved"}, {"status": "open"}]
        assert len(rows("SELECT id FROM site_level_changes")) == level_changes, "no bouncing level, no notifications"

        # Steady up for a whole window after the last change (12:14): over,
        # resolved when it came back up.
        assert acc1(at_same(clock, "2026-10-10T12:43:00Z"))["flapping"]
        row = at_same(clock, "2026-10-10T12:44:01Z")
        assert row["alert_level"] == "ok" and acc1(row) is None
        (instance,) = rows("SELECT resolved_at FROM alert_instances WHERE id = (SELECT max(id) FROM alert_instances)")
        assert instance["resolved_at"].startswith("2026-10-10T12:14:00")
        assert rows("SELECT device_id FROM device_states") == [], "steady: forgotten"

    def test_a_flapping_device_up_now_is_down_since_its_first_change_not_nautobots_last_edit(
        self, flapping, clock, monkeypatch
    ):
        monkeypatch.setattr(settings, "ALERT_DELAY_SECONDS", 3600)  # no incident from the blips themselves
        for minute, status in ((0, "Offline"), (2, "Active"), (4, "Offline")):
            at(clock, minute, status)
        clock["now"] = "2026-10-10T12:06:00Z"
        set_devices(core=("Active", LONG_AGO), acc1=("Active", LONG_AGO), acc2=("Active", LONG_AGO))
        row = build()
        assert acc1(row)["flapping"]
        (instance,) = rows("SELECT down_started_at FROM alert_instances")
        assert instance["down_started_at"].startswith("2026-10-10T12:00:00")

    def test_blips_under_the_alert_delay_add_up_to_flapping(self, flapping, clock, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_DELAY_SECONDS", 300)
        for minute, status in ((0, "Offline"), (1, "Active"), (2, "Offline")):
            assert at(clock, minute, status)["alert_level"] == "ok", "each blip alone is under the delay"
        row = at(clock, 3, "Active")
        assert acc1(row)["flapping"] and row["alert_level"] != "ok"

    def test_off(self, flapping, clock, monkeypatch):
        monkeypatch.setattr(settings, "FLAP_CHANGES", 0)
        for minute, status in ((0, "Offline"), (2, "Active"), (4, "Offline"), (6, "Active"), (8, "Offline")):
            row = at(clock, minute, status)
        assert "flapping" not in acc1(row)
        assert len(rows("SELECT id FROM alert_instances")) == 3, "one incident per outage, as before"

    def test_the_map_counts_a_flapping_device_as_down(self, flapping, clock):
        for minute, status in ((0, "Offline"), (2, "Active"), (4, "Offline"), (6, "Active")):
            at(clock, minute, status)
        caching.cache.clear()
        assert alerts.build_location_alert_levels()["levels"]["loc-lon"]["level"] != "ok"


def at_same(clock, now):
    """A later build with nothing changed."""
    clock["now"] = now
    return build()


class TestNextDeviceState:
    NOW = "2026-10-10T12:00:00Z"

    def test_up_and_steady_has_no_row(self, monkeypatch):
        monkeypatch.setattr(settings, "FLAP_CHANGES", 4)
        assert alerts.next_device_state(None, False, None, self.NOW) is None

    def test_changes_older_than_the_window_are_forgotten(self, monkeypatch):
        monkeypatch.setattr(settings, "FLAP_CHANGES", 2)
        stored = {"state": "up", "since": "2026-10-10T11:00:00Z", "changes": ["2026-10-10T11:00:00Z"]}
        row = alerts.next_device_state(stored, True, self.NOW, self.NOW)
        assert row == {"state": "down", "since": self.NOW, "changes": [self.NOW], "flapping_since": None}

    def test_flapping_lasts_until_a_whole_window_without_a_change(self, monkeypatch):
        monkeypatch.setattr(settings, "FLAP_CHANGES", 2)
        stored = {"state": "down", "since": "2026-10-10T11:50:00Z", "changes": ["2026-10-10T11:50:00Z"]}
        row = alerts.next_device_state(stored, False, None, self.NOW)
        assert row["flapping_since"] == self.NOW and row["state"] == "up"
        # 29 minutes later: one change left in the window, below the threshold, still flapping.
        later = alerts.next_device_state(row, False, None, "2026-10-10T12:29:00Z")
        assert later["flapping_since"] == self.NOW
        assert alerts.next_device_state(later, False, None, "2026-10-10T12:30:01Z") is None


class TestLabel:
    def _run(self, body):
        if shutil.which("node") is None:
            pytest.skip("node is required")
        js = (REPO_ROOT / "static" / "js" / "alerts.js").read_text(encoding="utf-8")
        script = f"function check(c, m) {{ if (!c) throw new Error(m); }}\n{_extract_js_function(js, 'flappingBadge')}\n{body}"
        completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False, env=os.environ)
        assert completed.returncode == 0, completed.stderr or completed.stdout

    def test_one_label_with_the_count_in_the_hover_text(self):
        self._run("""
check(flappingBadge({}) === "", "not flapping");
const html = flappingBadge({ flapping: true, flap_changes: 5 });
check(html.includes(">FLAPPING<") && html.includes('title="Up and down 5 times in the last 30 minutes"'), html);
""")
