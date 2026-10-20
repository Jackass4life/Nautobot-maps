"""Stacks (virtual chassis, #400): nameless members are named, and the site
panel shows a stack as one device."""

import pytest

from nautobot_maps import inventory, nautobot, settings
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
// Member 2's own Offline means nothing: the stack is its lead, which is up (#405).
check(closed.includes(">Stack of 3<") && closed.includes("dot-ok") && !closed.includes("stack-member"), closed);
const open = deviceRowHtml(stack.device, true, stack.members);
check(open.includes("Access Switch") && open.includes(">Stack of 3, monitored as one device:<"), "its role, then the members");
check(open.includes(">2. sw-stack01:2 · C9300<") && open.includes(">3. sw-stack01:3 · C9300 · Serial S3<"), "members without their own status: " + open);
check(closed.includes('class="device-row-role">Stack of 3<'), "a stack isn't styled as a note");
// The lead down: the whole stack is.
const down = deviceRowHtml({ ...stack.device, status: "Offline" }, true, stack.members);
check(down.includes("dot-critical") && down.includes(">The whole stack is down: all 3 members.<"), down);
// A lead in maintenance (or the alert delay) isn't an outage: not said.
const held = deviceRowHtml({ ...stack.device, status: "Offline", maintenance_until: "2026-10-20T21:00:00Z" }, true, stack.members);
check(!held.includes("whole stack is down") && held.includes("Stack of 3 · Maintenance"), held);
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


def test_the_board_says_which_stack_a_down_device_is_in(board, monkeypatch):
    """The board and its Copy say a down stack is down with all its members (#402, #405)."""
    from tests.test_app import _snapshot_device
    from tests.test_criticality import NOW, build, write

    def member(position, status, ip):
        return {
            **_snapshot_device(f"a-stk{position}", "a", "Access Switch", status),
            "name": "sw-stack01" if position == 1 else f"sw-stack01:{position}",
            "primary_ip": ip,
            "vc_id": "vc-1",
            "vc_name": "sw-stack01",
            "vc_position": position,
            "vc_master": position == 1,
        }

    devices = [
        member(1, "Offline", "10.0.0.1/32"),
        # An IP of its own: monitored on its own, so just itself when down.
        member(2, "Offline", "10.0.0.2/32"),
        member(3, "Offline", ""),  # no IP: not on the board, but in the stack's size
        member(4, "Active", ""),
        _snapshot_device("a-rt", "a", "Edge Router", "Offline"),
        # A fifth member at another location: the stack's size is the whole stack.
        {**member(5, "Active", ""), "location_id": "b"},
        # An excluded status: not one of the members down.
        {**member(6, "Decommissioning", "")},
    ]
    monkeypatch.setattr(settings, "ALERT_BOARD_EXCLUDED_DEVICE_STATUSES", {"decommissioning"})
    write({"a": "", "b": ""}, devices)
    row = build(board, NOW)["a"]
    # The whole stack wherever its members are (5 at b), without the Decommissioning one.
    names = ["sw-stack01", "sw-stack01:2", "sw-stack01:3", "sw-stack01:4", "sw-stack01:5"]
    stack = {"name": "sw-stack01", "size": 5, "members": names}
    assert {d["device_id"]: d.get("stack") for d in row["down_devices"]} == {
        # The master is how the stack is monitored: down means every member is (#405).
        "a-stk1": {**stack, "position": 1, "master": True, "whole": True},
        # A member with an IP of its own is just itself.
        "a-stk2": {**stack, "position": 2, "master": False, "whole": False},
        "a-rt": None,
    }
    # Counting is as before: the devices with a primary IP.
    assert row["device_count"] == 3, row["device_count"]
    # The assistant (MCP) gets it too.
    from nautobot_maps import mcp

    by_id = {d["device_id"]: d for d in mcp._site(row)["down_devices"]}
    assert by_id["a-stk1"]["stack"]["whole"] is True and by_id["a-stk1"]["stack"]["members"] == names
    assert "stack" not in by_id["a-rt"]


def test_stack_text_on_the_board_and_in_copy():
    run_js(
        """
import { stackText } from "@js/alerts/rows.js";
import { siteReport } from "@js/alerts/panels.js";
const stack = { name: "sw-stack01", position: 1, size: 4, master: true, whole: true, members: ["sw-stack01", "sw-stack01:2", "sw-stack01:3", "sw-stack01:4"] };
// A member monitored on its own: just itself.
check(stackText({ ...stack, position: 2, master: false, whole: false }, { names: true }) === "Stack sw-stack01, member 2 of 4", "member");
check(stackText(stack) === "Stack sw-stack01: all 4 members down", stackText(stack));
check(stackText(stack, { names: true }) === "Stack sw-stack01: all 4 members down (sw-stack01, sw-stack01:2, sw-stack01:3, sw-stack01:4)", "with names");
check(stackText({ name: "", size: 3, whole: true }, { names: true }) === "Stack: all 3 members down", "no name, no member names");
check(stackText(undefined) === "" && stackText({ size: 1 }) === "", "not a stack");
check(stackText({ name: "s", size: "4" }) === "", "a size must be a number");

const text = siteReport({
  name: "Site A", alert_level: "medium",
  down_devices: [
    { device_name: "sw-stack01", device_ip: "10.0.0.1", role: "Access Switch", status: "Offline", stack },
    { device_name: "edge", role: "Edge Router", status: "Offline" },
  ],
});
check(text.includes("- sw-stack01 | IP 10.0.0.1 | Role Access Switch | Stack sw-stack01: all 4 members down (sw-stack01, sw-stack01:2, sw-stack01:3, sw-stack01:4) | Status Offline"), text);
check(!text.split("\\n").find((line) => line.startsWith("- edge")).includes("Stack"), "not in a stack: nothing said");
"""
    )
