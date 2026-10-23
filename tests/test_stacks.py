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
        member(1, "Active", "10.0.0.1/32"),
        # An IP on a member is the stack's: not answering, the whole stack is down (#405).
        member(2, "Offline", "10.0.0.2/32"),
        member(3, "Offline", ""),  # no IP: its own status means nothing
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
    down = {d["device_id"]: d for d in row["down_devices"]}
    # One line for the stack, its master's, never one for a member.
    assert set(down) == {"a-stk1", "a-rt"}, set(down)
    assert down["a-stk1"]["stack"] == {
        "name": "sw-stack01",
        "size": 5,
        "position": 1,
        "master": True,
        "whole": True,
        "members": names,
    }
    assert down["a-stk1"]["status"] == "Offline", "the member's status is the stack's"
    assert "stack" not in down["a-rt"]
    # The stack counts once: it and the router.
    assert (row["device_count"], row["down_device_count"]) == (2, 2), row
    # The assistant (MCP) gets it too.
    from nautobot_maps import mcp

    by_id = {d["device_id"]: d for d in mcp._site(row)["down_devices"]}
    assert by_id["a-stk1"]["stack"]["whole"] is True and by_id["a-stk1"]["stack"]["members"] == names

    # Every IP answering: the stack is up, whatever a member without one says.
    write({"a": "", "b": ""}, [member(2, "Active", "10.0.0.2/32")])
    assert set(d["device_id"] for d in build(board, NOW)["a"]["down_devices"]) == {"a-rt"}


def _dev(position, status="Active", master=None, **extra):
    return {
        "id": f"m{position}",
        "name": f"s:{position}",
        "status": status,
        "primary_ip": f"10.0.0.{position}",
        "vc_id": "vc-1",
        "vc_position": position,
        "vc_master": position == 1 if master is None else master,
        **extra,
    }


def test_a_stack_counts_as_one_device():
    """Any of a stack's IPs down makes its lead down; the others aren't counted (#405)."""
    from nautobot_maps import alerts

    solo = {"id": "r", "status": "Active"}
    merged = alerts.merge_stacks([_dev(2, "offline", down_source="librenms"), solo, _dev(1), _dev(3)])
    assert [d["id"] for d in merged] == ["r", "m1"], "the master stands for the stack"
    assert merged[1]["status"] == "offline" and merged[1]["down_source"] == "librenms"
    # All up: the lead as it is.
    lead = _dev(1)
    assert alerts.merge_stacks([lead, _dev(2)]) == [lead]
    # No master among them: the first by place leads.
    assert [d["id"] for d in alerts.merge_stacks([_dev(3, master=False), _dev(2, master=False)])] == ["m2"]
    # A chassis of one is just a device.
    assert alerts.merge_stacks([_dev(1)]) == [_dev(1)]


@pytest.mark.parametrize(
    ("device", "counted", "whole"),
    [
        ({"status": "Offline"}, True, True),
        # In maintenance it isn't counted: not said.
        ({"status": "Offline"}, False, False),
        # Flapping, up for now: not said.
        ({"status": "Flapping", "down_source": "flapping"}, True, False),
    ],
)
def test_when_a_stack_is_down_whole(device, counted, whole):
    from nautobot_maps import alerts

    stacks = alerts.board_stacks({"vc-1": [{"name": "s", "status": "Active"}, {"name": "s:2", "status": "Active"}]})
    stack = alerts.stack_of({**device, "id": "m1", "vc_id": "vc-1", "vc_name": "s"}, stacks, counted)
    assert stack["whole"] is whole, stack
    assert ("members" in stack) is whole, "the names only when they are all down"


def test_a_stack_of_one_left_after_exclusions_is_not_a_stack(monkeypatch):
    from nautobot_maps import alerts

    monkeypatch.setattr(settings, "ALERT_BOARD_EXCLUDED_DEVICE_STATUSES", {"decommissioning"})
    stacks = alerts.board_stacks(
        {"vc-1": [{"name": "s", "status": "Active"}, {"name": "s:2", "status": "Decommissioning"}]}
    )
    assert alerts.stack_of({"id": "m1", "vc_id": "vc-1"}, stacks) == {}


def test_stack_text_on_the_board_and_in_copy():
    run_js(
        """
import { stackText } from "@js/alerts/rows.js";
import { siteReport } from "@js/alerts/panels.js";
const stack = { name: "sw-stack01", position: 1, size: 4, master: true, whole: true, members: ["sw-stack01", "sw-stack01:2", "sw-stack01:3", "sw-stack01:4"] };
// A member monitored on its own: just itself.
check(stackText({ ...stack, position: 2, master: false, whole: false }, { names: true }) === "Stack sw-stack01, member 2 of 4", "member");
check(stackText({ ...stack, whole: false }, { names: true }) === "Stack sw-stack01, master of 4", "a master, not down whole");
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
