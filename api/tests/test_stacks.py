"""Stacks (virtual chassis, #400): nameless members are named, and the site
panel shows a stack as one device."""

import pytest

from app.clients import nautobot
from app.services import inventory_service as inventory
from app.utils.legacy_settings import settings

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
            _device(
                "m4", None, True, display="sw-stack01:? (uuid)"
            ),  # no place: Nautobot's display
            _device("m5", None, None),  # neither
            {"id": "x", "name": None},  # not in a stack
            {"id": "y", "name": "edge", "virtual_chassis": None},
        ]
    )
    by_id = {d["id"]: d for d in devices}
    assert by_id["m1"]["name"] == "sw-stack01" and by_id["m1"]["vc_master"] is True
    assert (by_id["m1"]["vc_id"], by_id["m1"]["vc_name"], by_id["m1"]["vc_position"]) == (
        "vc-1",
        "sw-stack01",
        1,
    )
    assert by_id["m2"]["name"] == "sw-stack01:2" and by_id["m2"]["vc_master"] is False
    assert by_id["m3"]["name"] == "sw-stack01:3"
    assert by_id["m4"]["vc_position"] is None and by_id["m4"]["name"] == "sw-stack01:? (uuid)"
    assert by_id["m5"]["name"] == "sw-stack01 member"
    assert by_id["x"]["name"] == "Unknown" and by_id["x"]["vc_id"] == ""
    assert by_id["y"]["vc_master"] is False and by_id["y"]["vc_position"] is None


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
    from app.services import alerts_service as alerts

    solo = {"id": "r", "status": "Active"}
    merged = alerts.merge_stacks(
        [_dev(2, "offline", down_source="librenms"), solo, _dev(1), _dev(3)]
    )
    assert [d["id"] for d in merged] == ["r", "m1"], "the master stands for the stack"
    assert merged[1]["status"] == "offline" and merged[1]["down_source"] == "librenms"
    # All up: the lead as it is.
    lead = _dev(1)
    assert alerts.merge_stacks([lead, _dev(2)]) == [lead]
    # No master among them: the first by place leads.
    assert [
        d["id"] for d in alerts.merge_stacks([_dev(3, master=False), _dev(2, master=False)])
    ] == ["m2"]
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
    from app.services import alerts_service as alerts

    stacks = alerts.board_stacks(
        {"vc-1": [{"name": "s", "status": "Active"}, {"name": "s:2", "status": "Active"}]}
    )
    stack = alerts.stack_of(
        {**device, "id": "m1", "vc_id": "vc-1", "vc_name": "s"}, stacks, counted
    )
    assert stack["whole"] is whole, stack
    assert ("members" in stack) is whole, "the names only when they are all down"


def test_a_stack_of_one_left_after_exclusions_is_not_a_stack(monkeypatch):
    from app.services import alerts_service as alerts

    monkeypatch.setattr(settings, "ALERT_BOARD_EXCLUDED_DEVICE_STATUSES", {"decommissioning"})
    stacks = alerts.board_stacks(
        {"vc-1": [{"name": "s", "status": "Active"}, {"name": "s:2", "status": "Decommissioning"}]}
    )
    assert alerts.stack_of({"id": "m1", "vc_id": "vc-1"}, stacks) == {}


# Held back until what they need is ported (#587):
#   test_stored_and_read_back: HTTP routes (step 7)
#   test_a_changed_stack_makes_the_next_sync_full: HTTP routes (step 7)
#   test_the_board_says_which_stack_a_down_device_is_in: MCP (step 9)
