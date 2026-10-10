"""Root cause within a site (#285): devices behind a down core device."""

from datetime import UTC, datetime

from nautobot_maps import db, export, notify
from tests import test_maintenance
from tests.test_maintenance import build, set_devices

# London HQ: a core router and two access switches, all up for long (#283's fixture).
clock = test_maintenance.clock
site = test_maintenance.site


def causes() -> dict:
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT device_id, probable_cause FROM alert_instances WHERE status = 'open'").fetchall()
    finally:
        conn.close()
    return {row["device_id"]: row["probable_cause"] for row in map(db.row_to_dict, rows)}


def test_the_core_first_and_the_others_behind_it(site):
    row = build()  # core and acc1 down
    names = [d["device_name"] for d in row["down_devices"]]
    assert names[0] == "core" and row["down_devices"][0]["core"] is True
    acc1 = next(d for d in row["down_devices"] if d["device_name"] == "acc1")
    assert acc1["probable_cause"] == "core" and "core" not in acc1
    assert causes() == {"core": "", "acc1": "core"}, "kept in the history"


def test_no_core_down_no_cause(site):
    set_devices(core="Active", acc1="Offline", acc2="Active")
    row = build()
    assert all("probable_cause" not in d and "core" not in d for d in row["down_devices"])
    assert causes() == {"acc1": ""}


def test_a_core_back_up_clears_the_cause(site):
    build()
    set_devices(core="Active", acc1="Offline", acc2="Active")
    build()
    assert causes() == {"acc1": ""}


def test_several_core_devices(site):
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            conn.execute(
                "INSERT INTO device_criticality_override (nautobot_device_id, is_critical, updated_by, updated_at) "
                "VALUES ('acc2', 1, 'test', '2026-10-09T00:00:00Z')"
            )
    finally:
        conn.close()
    set_devices(core="Offline", acc1="Offline", acc2="Offline")
    row = build()
    assert {d["device_name"] for d in row["down_devices"] if d.get("core")} == {"core", "acc2"}
    assert causes()["acc1"] in ("core, acc2", "acc2, core")


def test_the_csv_has_the_cause(site):
    build()
    conn = db.get_conn()
    try:
        lines = list(export.incident_lines(export.iter_site_incidents(conn, "loc-lon", None), datetime.now(UTC)))
    finally:
        conn.close()
    assert lines[0].rstrip().endswith(",probable_cause,case_urls")
    acc1 = next(line for line in lines if line.startswith("London HQ,acc1"))
    assert acc1.rstrip().endswith(",core,")


def test_the_notification_names_the_likely_cause(site):
    row = build()
    event = notify.build_event("alarm", "ok", "critical", row, "2026-10-09T12:00:00Z")
    text = "\n".join(notify.detail_lines(event))
    assert "Likely cause: core down; 1 other device probably behind it" in text
    assert "| core |" in text or text.count("core") >= 2
    acc1 = next(line for line in text.splitlines() if line.startswith("- acc1"))
    assert "| likely caused by core" in acc1


def test_a_failed_observation_keeps_the_cause_from_the_history(site, monkeypatch):
    from nautobot_maps import alerts

    build()

    def unreadable(*args, **kwargs):
        raise RuntimeError("Nautobot unreachable")

    monkeypatch.setattr(alerts, "get_location_devices_and_alert", unreadable)
    row = build()
    by_name = {d["device_name"]: d for d in row["down_devices"]}
    assert by_name["acc1"]["probable_cause"] == "core"
    assert by_name["core"].get("core") is True and row["down_devices"][0]["device_name"] == "core"


def test_the_notification_counts_every_device_behind_the_core():
    devices = [{"device_name": "core", "core": True}] + [
        {"device_name": f"acc{n}", "probable_cause": "core"} for n in range(30)
    ]
    site = {"id": "s", "name": "S", "down_devices": devices, "down_device_count": 31, "device_count": 40}
    event = notify.build_event("alarm", "ok", "critical", site, "2026-10-09T12:00:00Z")
    assert len(event["down_devices"]) == notify.MAX_DEVICES_IN_MESSAGE
    assert event["likely_cause"] == {"cores": ["core"], "behind": 30}
    assert "Likely cause: core down; 30 other devices probably behind it" in "\n".join(notify.detail_lines(event))


def test_a_batched_summary_names_the_likely_cause_too():
    """A wide outage goes out as a summary (NOTIFY_SUMMARY_THRESHOLD); it says the cause too."""
    devices = [{"device_name": "core", "core": True}, {"device_name": "acc1", "probable_cause": "core"}]
    site = {"id": "s", "name": "London HQ", "down_devices": devices, "down_device_count": 2, "device_count": 7}
    alarm = notify.build_event("alarm", "ok", "critical", site, "2026-10-09T12:00:00Z")
    plain = notify.build_event(
        "alarm", "ok", "low", {"id": "t", "name": "Oslo", "down_device_count": 1}, "2026-10-09T12:00:00Z"
    )
    lines = notify.summary_lines([alarm, plain])
    assert lines[0] == "🔴 London HQ: Critical (2 down; likely cause: core down; 1 other device probably behind it)"
    assert lines[1] == "🔴 Oslo: Low (1 down)"


def test_a_failed_observation_keeps_core_devices_also_with_nothing_behind_them(site, monkeypatch):
    from nautobot_maps import alerts

    set_devices(core="Offline", acc1="Active", acc2="Active")
    build()

    def unreadable(*args, **kwargs):
        raise RuntimeError("Nautobot unreachable")

    monkeypatch.setattr(alerts, "get_location_devices_and_alert", unreadable)
    (core,) = build()["down_devices"]
    assert core["device_name"] == "core" and core.get("core") is True, "stored with the incident"
