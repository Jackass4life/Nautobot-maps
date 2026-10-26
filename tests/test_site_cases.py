"""The map's site panel says who is on it (#512): the cases of the site's down
devices, each device's own, and when nobody is."""

from nautobot_maps import alerts, db, nautobot
from tests import test_maintenance
from tests.test_maintenance import build

clock = test_maintenance.clock
site = test_maintenance.site


def instance_ids() -> dict:
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT id, device_id FROM alert_instances WHERE status = 'open'").fetchall()
        return {row["device_id"]: row["id"] for row in rows}
    finally:
        conn.close()


def add_case(instance_id: int, number: str, at: str) -> None:
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO alert_cases (alert_instance_id, case_number, created_at) VALUES (%s, %s, %s)",
            (instance_id, number, at),
        )
    finally:
        conn.close()


def detail(monkeypatch) -> dict:
    # No Nautobot here: no ASNs or circuits, the devices from the stored inventory.
    monkeypatch.setattr(nautobot, "fetch_all_pages", lambda *args, **kwargs: [])
    return alerts.get_location_detail("loc-lon")


class TestSitePanelCases:
    def test_nobody_on_it_then_cases_and_an_acknowledgement(self, site, monkeypatch):
        build()  # core and acc1 down: open alerts
        before = detail(monkeypatch)
        assert before["cases"] == []
        down = {d["id"]: d for d in before["devices"]}
        # Down with an open alert, nobody on it yet; up: nothing.
        assert (down["core"]["case_numbers"], down["core"]["acknowledged_by"]) == ([], "")
        assert "case_numbers" not in down["acc2"]

        ids = instance_ids()
        add_case(ids["acc1"], "INC0048213", "2026-10-09T12:00:00Z")
        add_case(ids["acc1"], "INC0048290", "2026-10-09T12:05:00Z")
        add_case(ids["core"], "INC0048213", "2026-10-09T12:00:00Z")
        conn = db.get_conn()
        try:
            conn.execute("UPDATE alert_instances SET acknowledged_by = 'olga' WHERE id = %s", (ids["core"],))
        finally:
            conn.close()

        after = detail(monkeypatch)
        devices = {d["id"]: d for d in after["devices"]}
        # Newest first, each once.
        assert after["cases"] == ["INC0048290", "INC0048213"]
        assert devices["acc1"]["case_numbers"] == ["INC0048290", "INC0048213"]
        assert (devices["core"]["case_numbers"], devices["core"]["acknowledged_by"]) == (["INC0048213"], "olga")
        assert "case_numbers" not in devices["acc2"], "up: no open alert"

    def test_without_a_database_no_cases(self, monkeypatch):
        monkeypatch.setattr(db, "dialect", lambda: "")
        monkeypatch.setattr(alerts, "get_location_devices_and_alert", lambda *args, **kwargs: ([], {}))
        monkeypatch.setattr(nautobot, "fetch_all_pages", lambda *args, **kwargs: [])
        assert alerts.get_location_detail("loc-x")["cases"] == []
