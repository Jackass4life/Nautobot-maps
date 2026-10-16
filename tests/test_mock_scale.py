"""The mock's scale mode (MOCK_SCALE): a NOC's load, the same on every start."""

import ipaddress
import json
import os
import pathlib
import subprocess
import sys

DEMO = pathlib.Path(__file__).resolve().parent.parent / "demo"

SUMMARY = """
import json, mock_nautobot as m
devices = [d for devs in m.DEVICES.values() for d in devs]
print(json.dumps({
    "sites": sum(1 for loc in m.LOCATIONS if loc["latitude"]),
    "devices": len(devices),
    "down": sorted(d["id"] for d in devices if d["status"]["label"] == "Offline"),
    "ips": [d["primary_ip4"]["host"] for d in devices if d["primary_ip4"]],
    "names": [loc["name"] for loc in m.LOCATIONS],
}))
"""


def _summary(scale):
    env = {**os.environ, "MOCK_SCALE": scale, "PYTHONHASHSEED": "random"}
    out = subprocess.run([sys.executable, "-c", SUMMARY], cwd=DEMO, env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_scale_mode_is_a_nocs_load_and_the_same_every_start():
    first, again = _summary("1200"), _summary("1200")
    assert first == again
    assert first["sites"] == 1209 and first["devices"] > 9000
    assert 50 <= len(first["down"]) <= 100  # a normal day's 50–100 devices down
    assert len(set(first["names"])) == len(first["names"])
    for host in first["ips"]:
        ipaddress.IPv4Address(host)  # valid past 255 sites
    assert len(set(first["ips"])) == len(first["ips"])


def test_off_by_default_and_any_size():
    assert _summary("")["sites"] == 9
    assert _summary("10")["sites"] == 19  # fewer sites than alarms: no error
    assert _summary("1,200")["sites"] == 1209


def test_a_wrong_value_says_so():
    env = {**os.environ, "MOCK_SCALE": "yes"}
    out = subprocess.run(
        [sys.executable, "-c", "import mock_nautobot"], cwd=DEMO, env=env, capture_output=True, text=True
    )
    assert out.returncode != 0 and "MOCK_SCALE must be a number of sites" in out.stderr
