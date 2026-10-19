"""Stacks (virtual chassis, #400): nameless members are named, and the site
panel shows a stack as one device."""

import pytest

from nautobot_maps import inventory, nautobot
from tests.js_modules import run_js

CHASSIS = {"id": "vc-1", "name": "sw-stack01", "display": "sw-stack01", "master": {"id": "m1"}}


def _device(device_id, name=None, position=None, chassis=CHASSIS, **extra):
    return {
        "id": device_id,
        "name": name,
        "virtual_chassis": chassis,
        "vc_position": position,
        "device_type": {"model": "C9300"},
        **extra,
    }


@pytest.fixture
def no_lookups(monkeypatch):
    monkeypatch.setattr(nautobot, "fetch_all_pages", lambda *args, **kwargs: [])


def test_members_are_named_and_know_their_stack(no_lookups):
    devices = inventory.normalize_devices(
        [
            _device("m1", "sw-stack01", 1, display="sw-stack01"),
            # Nautobot's display adds the UUID: the name is the chassis and place.
            _device("m2", None, 2, display="sw-stack01:2 (5f0e-uuid)"),
            _device("m3", None, 3),
            _device("m4", None, True, display="sw-stack01:? (uuid)"),  # no place: Nautobot's display
            _device("m5", None, None),  # neither
            {"id": "x", "name": None},  # not in a stack
            {"id": "y", "name": "edge", "virtual_chassis": None},
        ]
    )
    by_id = {d["id"]: d for d in devices}
    assert by_id["m1"]["name"] == "sw-stack01" and by_id["m1"]["vc_master"] is True
    assert (by_id["m1"]["vc_id"], by_id["m1"]["vc_name"], by_id["m1"]["vc_position"]) == ("vc-1", "sw-stack01", 1)
    assert by_id["m2"]["name"] == "sw-stack01:2" and by_id["m2"]["vc_master"] is False
    assert by_id["m3"]["name"] == "sw-stack01:3"
    assert by_id["m4"]["vc_position"] is None and by_id["m4"]["name"] == "sw-stack01:? (uuid)"
    assert by_id["m5"]["name"] == "sw-stack01 member"
    assert by_id["x"]["name"] == "Unknown" and by_id["x"]["vc_id"] == ""
    assert by_id["y"]["vc_master"] is False and by_id["y"]["vc_position"] is None


def test_stored_and_read_back(client, pg_database, no_lookups):
    devices = inventory.normalize_devices(
        [
            _device("m1", "sw-stack01", 1, location={"id": "loc-1"}),
            _device("m2", None, 2, display="sw-stack01:2 (uuid)", location={"id": "loc-1"}),
        ]
    )
    conn = inventory.db.get_conn()
    try:
        with inventory.db.transaction(conn):
            inventory.write_devices(conn, devices)
    finally:
        conn.close()
    stored = {d["id"]: d for d in inventory.read_devices("loc-1")}
    assert stored["m1"]["vc_master"] is True and stored["m1"]["vc_position"] == 1
    assert stored["m2"]["name"] == "sw-stack01:2" and stored["m2"]["vc_id"] == "vc-1"
    assert stored["m2"]["vc_master"] is False


def test_the_panel_shows_a_stack_as_one_device():
    run_js(
        """
import { deviceRowHtml, groupStacks } from "@js/map/panel.js";
const member = (id, name, position, extra = {}) => ({ id, name, vc_id: "vc-1", vc_name: "sw-stack01", vc_position: position, device_type: "C9300", status: "Active", ...extra });
const devices = [
  { id: "r1", name: "edge-rt01", role: "Edge Router", status: "Active" },
  member("m3", "sw-stack01:3", 3, { serial: "S3", not_monitored: "no primary IP" }),
  member("m1", "sw-stack01", 1, { vc_master: true, primary_ip: "10.0.0.5/32", role: "Access Switch" }),
  member("m2", "sw-stack01:2", 2, { status: "Offline" }),
  // A chassis of one isn't a stack.
  { id: "solo", name: "solo", vc_id: "vc-2", vc_position: 1 },
];
const entries = groupStacks(devices);
check(entries.map((e) => e.device.id).join() === "r1,m1,solo", entries.map((e) => e.device.id).join());
const stack = entries[1];
check(stack.members.map((m) => m.id).join() === "m2,m3", "members by position");

// Without a master: the member with an IP leads, else the first by position.
const noMaster = groupStacks([member("a", "a", 2), member("b", "b", 1), member("c", "c", 3, { primary_ip: "10.0.0.9" })]);
check(noMaster.length === 1 && noMaster[0].device.id === "c", "the one with an IP leads");
check(groupStacks([member("a", "a", 2), member("b", "b", 1)])[0].device.id === "b", "else the first");

const closed = deviceRowHtml(stack.device, false, stack.members);
// Member 2 is down: the closed row says so, and is red though the master is up.
check(closed.includes(">Stack of 3 · 1 down<") && closed.includes("dot-critical") && !closed.includes("stack-member"), closed);
const open = deviceRowHtml(stack.device, true, stack.members);
check(open.includes("Access Switch") && open.includes(">Stack of 3:<"), "its role in the details, then the members");
check(open.includes(">2. sw-stack01:2 · C9300 · Offline<") && open.includes(">3. sw-stack01:3 · C9300 · Serial S3<"), open);
check(closed.includes('class="device-row-role">Stack of 3 · 1 down<'), "a stack isn't styled as a note");
// A lead in maintenance or unmonitored still says it is a stack.
const quiet = deviceRowHtml({ ...stack.device, not_monitored: "no primary IP" }, false, [stack.members[1]]);
check(quiet.includes(">Stack of 2 · Not monitored<") && quiet.includes("device-row-note") && quiet.includes("dot-ok"), quiet);
check(deviceRowHtml({ id: "m", name: "<x>", vc_position: 1 }, true, [{ name: "<y>", vc_position: 2 }]).includes("2. &lt;y&gt;"), "escaped");
"""
    )


def test_a_changed_stack_makes_the_next_sync_full(client, pg_database, monkeypatch):
    """Renaming a stack or moving its master saves the chassis, not its members (#400)."""
    from nautobot_maps import settings

    monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    changed = []
    calls = []

    def fetch(endpoint, params=None, **kwargs):
        if endpoint == "dcim/locations/":
            calls.append(dict(params or {}))
        if endpoint == "dcim/virtual-chassis/":
            return changed
        return []

    monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
    inventory.sync_nautobot(force=True)
    inventory.sync_nautobot()
    assert "last_updated__gte" in calls[-1]
    inventory.sync_nautobot()
    assert "last_updated__gte" in calls[-1], "no stack changed: still incremental"
    changed.append({"id": "vc-1", "name": "sw-stack01-new"})
    inventory.sync_nautobot()  # incremental, and sees the change
    changed.clear()
    inventory.sync_nautobot()
    assert "last_updated__gte" not in calls[-1], "so this one is full"
