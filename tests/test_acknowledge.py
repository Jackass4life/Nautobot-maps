"""Acknowledge an alarm, separate from a case number (#288)."""

import os
import shutil
import subprocess

import pytest

import app as flask_app
from nautobot_maps import alerts, caching, db
from tests import test_maintenance
from tests.test_app import auth_config
from tests.test_integration import REPO_ROOT, _extract_js_function
from tests.test_maintenance import build, set_devices

# London HQ: core and acc1 down, acc2 up (#283's fixture).
clock = test_maintenance.clock
site = test_maintenance.site


def ack(device_ids=None, note="", clear=False, by="olga"):
    conn = db.get_conn()
    try:
        return alerts.acknowledge(conn, "loc-lon", device_ids, by, note, clear=clear)
    finally:
        conn.close()


def events() -> list[tuple]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT i.device_id, e.event_type FROM alert_events e JOIN alert_instances i ON i.id = e.alert_instance_id "
            "WHERE e.event_type IN ('acknowledged', 'unacknowledged') ORDER BY e.id"
        ).fetchall()
    finally:
        conn.close()
    return [(row["device_id"], row["event_type"]) for row in rows]


def acked(row) -> dict:
    return {d["device_name"]: d.get("acknowledged") for d in row["down_devices"]}


class TestAcknowledge:
    def test_every_down_device_in_one_go_shown_and_in_the_history(self, site):
        build()
        result = ack(note="On my way")
        assert sorted(result["changed"]) == ["acc1", "core"] and result["missing"] == []
        row = build()
        assert {name: (a["by"], a["note"]) for name, a in acked(row).items()} == {
            "core": ("olga", "On my way"),
            "acc1": ("olga", "On my way"),
        }
        assert sorted(events()) == [("acc1", "acknowledged"), ("core", "acknowledged")]

    def test_some_devices_all_or_nothing_and_remove(self, site):
        build()
        assert ack(["acc1", "nope"]) == {"changed": [], "missing": ["nope"]}
        assert events() == [], "nothing changed"
        ack(["acc1"])
        assert acked(build()) == {"core": None, "acc1": acked(build())["acc1"]} and acked(build())["acc1"]
        assert ack(["acc1"], clear=True)["changed"] == ["acc1"]
        assert ack(["acc1"], clear=True)["changed"] == [], "nothing to remove"
        assert all(a is None for a in acked(build()).values())
        assert events() == [("acc1", "acknowledged"), ("acc1", "unacknowledged")]

    def test_acknowledging_again_keeps_the_first_owner(self, site):
        build()
        ack(["acc1"], note="Mine", by="olga")
        assert ack(by="vera", note="Me too")["changed"] == ["core"], "only the one not acknowledged yet"
        by_name = acked(build())
        assert (by_name["acc1"]["by"], by_name["acc1"]["note"]) == ("olga", "Mine")
        assert by_name["core"]["by"] == "vera"
        assert sorted(events()) == [("acc1", "acknowledged"), ("core", "acknowledged")], "no duplicate event"

    def test_it_ends_with_the_alarm(self, site):
        build()
        ack(["acc1"])
        set_devices(core="Offline", acc1="Active", acc2="Active")
        build()
        set_devices(core="Offline", acc1="Offline", acc2="Active")
        assert acked(build())["acc1"] is None, "a new outage is a new incident, not acknowledged"


class TestApi:
    @pytest.fixture
    def client(self, site):
        build()
        flask_app.app.config["TESTING"] = True
        caching.cache.clear()
        with flask_app.app.test_client() as test_client:
            yield test_client

    def test_acknowledge_and_remove(self, client):
        # What the row's one-click Acknowledge sends: device_ids null.
        resp = client.post("/api/alert-acks", json={"site_id": "loc-lon", "device_ids": None, "note": "On it"})
        assert resp.status_code == 200 and sorted(resp.get_json()["changed"]) == ["acc1", "core"]
        board = client.get("/api/alerts").get_json()
        row = next(a for a in board["alerts"] if a["id"] == "loc-lon")
        assert all(d["acknowledged"]["note"] == "On it" for d in row["down_devices"]), "the cached board was dropped"
        removed = client.post("/api/alert-acks/remove", json={"site_id": "loc-lon"}).get_json()["changed"]
        assert sorted(removed) == ["acc1", "core"]

    def test_bad_requests(self, client):
        assert client.post("/api/alert-acks", data="x").status_code == 400
        assert client.post("/api/alert-acks", json={}).status_code == 400
        for bad in ("acc1", "", 0, False, [], ["  "], [1]):
            resp = client.post("/api/alert-acks", json={"site_id": "loc-lon", "device_ids": bad})
            assert resp.status_code == 400, bad
        for bad_note in (0, False, [], {}):
            resp = client.post("/api/alert-acks", json={"site_id": "loc-lon", "note": bad_note})
            assert resp.status_code == 400, bad_note
        assert events() == [], "bad input never acknowledges everything"
        resp = client.post("/api/alert-acks", json={"site_id": "loc-lon", "device_ids": ["acc2"]})
        assert resp.status_code == 404 and resp.get_json()["missing_device_ids"] == ["acc2"], "acc2 is up"

    def test_operator_role_with_sign_in(self, client):
        with auth_config(mode="header", viewer_groups={"noc"}, operator_groups={"ops"}):
            viewer = {"X-Forwarded-User": "vera", "X-Forwarded-Groups": "noc"}
            assert client.post("/api/alert-acks", json={"site_id": "loc-lon"}, headers=viewer).status_code == 403
            operator = {"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"}
            resp = client.post("/api/alert-acks", json={"site_id": "loc-lon"}, headers=operator)
            assert resp.status_code == 200
        conn = db.get_conn()
        try:
            (by,) = {
                r["acknowledged_by"]
                for r in conn.execute("SELECT acknowledged_by FROM alert_instances").fetchall()
                if r["acknowledged_by"]
            }
        finally:
            conn.close()
        assert by == "olga"


class TestInTheBrowser:
    def _run(self, body):
        if shutil.which("node") is None:
            pytest.skip("node is required")
        js = (REPO_ROOT / "static" / "js" / "alerts.js").read_text(encoding="utf-8")
        names = (
            "escHtml",
            "downDeviceList",
            "caseDevices",
            "ackNote",
            "ackButton",
            "siteCaseState",
            "caseStateBadge",
            "ackRequestBody",
        )
        script = (
            "function check(c, m) { if (!c) throw new Error(m); }\n"
            + "\n".join(_extract_js_function(js, name) for name in names)
            + "\n"
            + body
        )
        completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False, env=os.environ)
        assert completed.returncode == 0, completed.stderr or completed.stdout

    def test_ack_counts_as_someone_on_it(self):
        self._run("""
const ack = { by: "olga", note: "On <it>", at: "2026-10-10T20:00:00Z" };
check(ackNote({ acknowledged: ack }) === '<div class="ack-note">ACK olga · On &lt;it&gt;</div>', ackNote({ acknowledged: ack }));
check(ackNote({}) === "", "not acknowledged");
const site = { id: "s1", name: "Oslo", down_devices: [
  { device_id: "d1", device_name: "sw1", acknowledged: ack },
  { device_id: "d2", device_name: "sw2", case_numbers: ["INC-1"] },
] };
check(siteCaseState(site).state === "handled", "a case or an ack: someone is on it");
check(caseStateBadge(site).includes("✓ INC-1 · ACK"), caseStateBadge(site));
check(ackButton(site).includes(">Acknowledge<"), "one device isn't acknowledged: Acknowledge");
// One acknowledged and one untouched: "1 of 2 no case or ack", not "no case".
const partly = { id: "s3", down_devices: [{ device_id: "a", acknowledged: ack }, { device_id: "b" }] };
check(caseStateBadge(partly).includes(">1 of 2 no case or ack<"), caseStateBadge(partly));
site.down_devices[1].acknowledged = ack;
check(ackButton(site).includes("unack-btn") && ackButton(site).includes(">Remove ack<"), ackButton(site));
check(ackButton({ id: "s2", down_devices: [{ device_id: "", device_name: "ghost" }] }) === "", "nothing to acknowledge");
// One click = every device: null, which the API accepts (an empty list it refuses).
check(JSON.stringify(ackRequestBody("s1", [], "")) === '{"site_id":"s1","device_ids":null,"note":""}', "all");
check(ackRequestBody("s1", ["d1"], "x").device_ids[0] === "d1", "some");
""")
