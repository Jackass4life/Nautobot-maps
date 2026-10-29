"""Which switch and port a down access point is on (#524)."""

from datetime import UTC, datetime

import pytest

from nautobot_maps import alerts, caching, catalyst, contract, db, inventory, nautobot, neighbours, settings, wireless
from tests.test_wireless import (  # noqa: F401 (fixtures)
    APS,
    DK,
    SE,
    ap,
    board,
    build_board,
    clock,
    controller_says,
    controllers,
    write_sites,
)

TOPOLOGY = {
    "nodes": [
        {"id": "n-ap1", "label": "hel-ap01", "family": "Unified AP"},
        {"id": "n-ap2", "label": "hel-ap02", "family": "Unified AP"},
        {"id": "n-ap3", "label": "hel-ap03.example.com", "family": "Unified AP"},
        {"id": "n-sw", "label": "hel-acc-sw02", "family": "Switches and Hubs"},
        {"id": "n-wlc", "label": "hel-wlc01", "family": "Wireless Controller"},
    ],
    "links": [
        # Either way round; the AP's own port is left out.
        {"source": "n-ap1", "target": "n-sw", "startPortName": "GigabitEthernet0", "endPortName": "Gi1/0/14"},
        {"source": "n-sw", "target": "n-ap2", "startPortName": "Gi1/0/15", "endPortName": "GigabitEthernet0"},
        {"source": "n-ap3", "target": "n-sw", "endPortName": "Gi1/0/16"},
        # A second link of ap1 (to the controller): its first stays.
        {"source": "n-ap1", "target": "n-wlc", "endPortName": "Te1/0/1"},
        # Two APs (a mesh link) or an unknown node: no switch to name.
        {"source": "n-ap1", "target": "n-ap2"},
        {"source": "n-ap1", "target": "n-gone"},
    ],
}


class TestCatalystTopology:
    def test_each_aps_switch_and_port(self):
        assert catalyst.ap_neighbours(TOPOLOGY) == [
            {"ap_id": "n-ap1", "ap_name": "hel-ap01", "switch": "hel-acc-sw02", "port": "Gi1/0/14"},
            {"ap_id": "n-ap2", "ap_name": "hel-ap02", "switch": "hel-acc-sw02", "port": "Gi1/0/15"},
            {"ap_id": "n-ap3", "ap_name": "hel-ap03.example.com", "switch": "hel-acc-sw02", "port": "Gi1/0/16"},
        ]
        assert catalyst.ap_neighbours({}) == []

    def test_read_once_per_instance(self, monkeypatch):
        asked = []
        monkeypatch.setattr(catalyst, "get", lambda c, path, params: asked.append(path) or {"response": TOPOLOGY})
        assert catalyst.fetch_topology(DK) == TOPOLOGY
        assert asked == ["/dna/intent/api/v1/topology/physical-topology"]
        monkeypatch.setattr(catalyst, "get", lambda c, path, params: {})
        assert catalyst.fetch_topology(DK) == {"nodes": [], "links": []}

    def test_synced_with_the_access_points(self, controllers, monkeypatch):  # noqa: F811
        checked = []
        real_check = contract.check_and_record
        monkeypatch.setattr(
            contract,
            "check_and_record",
            lambda source, path, records, *a: checked.append(path) or real_check(source, path, records, *a),
        )
        controllers["dk"] = [
            {**ap("hel-ap01", False, "FGL1"), "id": "n-ap1"},
            {**ap("hel-ap02", True, "FGL2"), "id": "n-ap2"},
        ]
        controllers["topology"]["dk"] = TOPOLOGY
        wireless.sync_controller(DK)
        index = load()
        assert index["serial"] == {
            "FGL1": {"device": "hel-acc-sw02", "port": "Gi1/0/14", "source": "Catalyst Center dk"},
            "FGL2": {"device": "hel-acc-sw02", "port": "Gi1/0/15", "source": "Catalyst Center dk"},
        }
        # ap03 isn't in the inventory just read: matched by its name.
        assert index["name"]["hel-ap03"]["port"] == "Gi1/0/16"
        assert "topology/physical-topology" in checked
        # A topology that can't be read keeps the last answer; the APs are unaffected.
        controllers["topology"]["dk"] = RuntimeError("timeout")
        wireless.sync_controller(DK)
        assert load()["serial"]["FGL1"]["port"] == "Gi1/0/14"
        conn = db.get_conn()
        try:
            assert inventory.get_sync_state(wireless.source(DK), conn=conn)["status"] == "idle"
        finally:
            conn.close()


def load(devices=None) -> dict:
    conn = db.get_conn()
    try:
        return neighbours.read_index(conn, devices)
    finally:
        conn.close()


CABLE = {
    "id": "cab-1",
    "termination_a": {"id": "if-1", "name": "GigabitEthernet0", "device": {"id": "dev-ap1"}},
    "termination_b": {"id": "if-2", "name": "Gi1/0/7", "device": {"id": "dev-sw1"}},
}


class TestNautobotCables:
    def test_a_cable_end(self):
        assert inventory.cable_end(CABLE, "a") == ("dev-ap1", "GigabitEthernet0")
        assert inventory.cable_end({"termination_b": {"name": "x", "device": "dev-9"}}, "b") == ("dev-9", "x")
        # A circuit termination has no device.
        assert inventory.cable_end({"termination_a": {"id": "ct-1", "circuit": {"id": "c"}}}, "a") == ("", "")

    def test_read_with_the_inventory_incrementally(self, pg_database, monkeypatch):
        asked = []
        monkeypatch.setattr(
            nautobot,
            "fetch_all_pages",
            lambda path, params=None, **kw: asked.append(params) or [CABLE, {"id": "cab-2"}],
        )
        assert inventory.fetch_cables({"last_updated__gte": "2026-10-01T00:00:00Z"}) == [
            {
                "cable_id": "cab-1",
                "a_device_id": "dev-ap1",
                "a_port": "GigabitEthernet0",
                "b_device_id": "dev-sw1",
                "b_port": "Gi1/0/7",
            }
        ]
        assert asked == [{"last_updated__gte": "2026-10-01T00:00:00Z", "depth": 1}]

        def broken(*args, **kwargs):
            raise RuntimeError("timeout")

        monkeypatch.setattr(nautobot, "fetch_all_pages", broken)
        assert inventory.fetch_cables(None) is None, "kept: not an empty list"
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                inventory.write_cables(conn, inventory.fetch_cables(None) or [], full=False)
                inventory.write_cables(
                    conn, [dict(cable_id="c1", a_device_id="a", a_port="p", b_device_id="b", b_port="q")], full=False
                )
                inventory.write_cables(
                    conn, [dict(cable_id="c1", a_device_id="a", a_port="p2", b_device_id="b", b_port="q")], full=False
                )
                inventory.write_cables(
                    conn, [dict(cable_id="c2", a_device_id="x", a_port="", b_device_id="y", b_port="")], full=False
                )
            assert [
                r["a_port"]
                for r in conn.execute("SELECT a_port FROM nautobot_cable_cache WHERE cable_id = 'c1'").fetchall()
            ] == ["p2"]
            with db.transaction(conn):
                inventory.write_cables(conn, [], full=True)
            assert conn.execute("SELECT count(*) AS n FROM nautobot_cable_cache").fetchone()["n"] == 0
        finally:
            conn.close()


def cable(conn, ap_id: str, switch_id: str, port: str) -> None:
    inventory.write_cables(
        conn,
        [{"cable_id": f"c-{ap_id}", "a_device_id": switch_id, "a_port": port, "b_device_id": ap_id, "b_port": "Gi0"}],
        full=False,
    )


class TestOnTheBoard:
    def test_a_down_access_point_says_where_it_is_and_the_row_which_switch(self, board, monkeypatch):  # noqa: F811
        write_sites({"cph": {"sw1": "Active", **{name: "Active" for name in APS[:16]}}})
        controller_says(monkeypatch, {"cph": APS[:12]}, every={"cph": APS[:16]})
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                # Catalyst Center says the first 14 are on sw02; Nautobot's cables, ap15 and ap16 on sw1.
                rows = [
                    (
                        "catalyst_center:dk",
                        f"FGL-CPH-{name}".upper(),
                        "",
                        "cph-acc-sw02",
                        f"Gi1/0/{n}",
                        datetime.now(UTC),
                    )
                    for n, name in enumerate(APS[:14], start=1)
                ]
                conn.cursor().executemany(
                    "INSERT INTO wireless_ap_neighbours (controller, serial_key, name_key, switch_name, switch_port, seen_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    rows,
                )
                for n, name in enumerate(APS[14:16], start=15):
                    cable(conn, f"cph-{name}", "cph-sw1", f"Gi1/0/{n}")
        finally:
            conn.close()
        (row,) = build_board()["alerts"]
        first = next(d for d in row["down_devices"] if d["device_name"] == f"cph-{APS[0]}")
        assert first["neighbour"] == {"device": "cph-acc-sw02", "port": "Gi1/0/1", "source": "Catalyst Center dk"}
        assert row["behind"] == {"device": "cph-acc-sw02", "down": 12, "total": 14}

    def test_one_down_names_its_switch_but_no_row_line(self, board, monkeypatch):  # noqa: F811
        write_sites({"cph": {"sw1": "Active", APS[0]: "Active", APS[1]: "Active"}})
        controller_says(monkeypatch, {"cph": APS[:1]}, every={"cph": APS[:2]})
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                for name in APS[:2]:
                    cable(conn, f"cph-{name}", "cph-sw1", "Gi1/0/1")
        finally:
            conn.close()
        (row,) = build_board()["alerts"]
        (down,) = row["down_devices"]
        assert down["neighbour"] == {
            "device": "cph-sw1",
            "device_id": "cph-sw1",
            "port": "Gi1/0/1",
            "source": "Nautobot",
        }
        assert "behind" not in row, "one access point down: no line"

    def test_none_known_shows_nothing(self, board, monkeypatch):  # noqa: F811
        write_sites({"cph": {"sw1": "Active", APS[0]: "Active", APS[1]: "Active"}})
        controller_says(monkeypatch, {"cph": APS[:2]}, every={"cph": APS[:2]})
        (row,) = build_board()["alerts"]
        assert "behind" not in row and not any("neighbour" in d for d in row["down_devices"])

    def test_the_site_panel(self, board, monkeypatch):  # noqa: F811
        write_sites({"cph": {"sw1": "Active", APS[0]: "Active"}})
        controller_says(monkeypatch, {"cph": APS[:1]}, every={"cph": APS[:1]})
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                cable(conn, f"cph-{APS[0]}", "cph-sw1", "Gi1/0/9")
        finally:
            conn.close()
        monkeypatch.setattr(alerts.nautobot, "fetch_all_pages", lambda *a, **k: [])
        monkeypatch.setattr(alerts, "location_circuits", lambda location_id: ([], ""))
        caching.cache.clear()
        devices = {d["name"]: d for d in alerts.get_location_detail("cph")["devices"]}
        assert devices[f"cph-{APS[0]}"]["neighbour"]["port"] == "Gi1/0/9"
        assert "neighbour" not in devices["cph-sw1"], "only access points"


def test_behind_needs_two_down_on_one_switch():
    index = {
        "serial": {},
        "name": {},
        "cables": {f"a{n}": {"device": "sw1" if n < 3 else "sw2", "port": ""} for n in range(5)},
    }
    aps = [{"id": f"a{n}", "role": "Access Point"} for n in range(5)]
    assert neighbours.behind(aps, {"a0", "a1"}, index) == {"device": "sw1", "down": 2, "total": 3}
    assert neighbours.behind(aps, {"a0", "a3"}, index) is None, "one on each"
    assert neighbours.behind(aps, {"a0"}, index) is None
    assert neighbours.text({"device": "sw1", "port": "Gi1/0/1"}) == "Connected to sw1 · Gi1/0/1"
    assert neighbours.text({"device": "sw1", "port": ""}) == "Connected to sw1"


@pytest.fixture(autouse=True)
def _ap_role(monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_POINT_ROLES", {"access point"})
