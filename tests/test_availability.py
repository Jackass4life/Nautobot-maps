"""Availability per site, tenant and month (#287)."""

from datetime import UTC, datetime, timedelta

import pytest

import app as flask_app
from nautobot_maps import availability, caching, db, settings, timeutil

NOW = "2026-10-21T00:00:00Z"  # 20 days into October
OCT = datetime(2026, 10, 1, tzinfo=UTC)
PERIOD = 20 * 86400


def t(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(timeutil, "iso_utc_now", lambda: NOW)


def incident(conn, site, device, down, up=None, name=None):
    conn.execute(
        "INSERT INTO alert_instances (alert_key, site_id, site_name, device_id, device_name, alert_level, "
        "status, down_started_at, last_seen_down_at, resolved_at, total_downtime_seconds) "
        "VALUES (%s, %s, %s, %s, %s, 'low', %s, %s, %s, %s, 0)",
        (
            f"{site}|{device}|{down.isoformat()}",
            site,
            name or site,
            device,
            device,
            "open" if up is None else "resolved",
            down,
            down,
            up,
        ),
    )


def window(conn, site, device, starts, ends, ended=None):
    conn.execute(
        "INSERT INTO maintenance_windows (site_id, device_id, starts_at, ends_at, ended_at, reason) "
        "VALUES (%s, %s, %s, %s, %s, 'x')",
        (site, device, starts, ends, ended),
    )


def write(fn):
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            fn(conn)
    finally:
        conn.close()


def run(month="2026-10", basis="any_down", sites=None):
    conn = db.get_conn()
    try:
        return availability.report(conn, month, basis, sites)
    finally:
        conn.close()


class TestIntervals:
    def test_union_subtract_clip(self):
        a, b, c = (t(1), t(2)), (t(1, 12), t(3)), (t(5), t(6))
        assert availability.union([c, a, b]) == [(t(1), t(3)), c]
        assert availability.subtract([(t(1), t(5))], [(t(2), t(3)), (t(4), t(10))]) == [(t(1), t(2)), (t(3), t(4))]
        assert availability.clip([(t(1) - timedelta(days=2), t(2))], OCT, t(3)) == [(OCT, t(2))]
        assert availability.seconds([a, b]) == 2 * 86400

    def test_month_bounds(self):
        at = t(21)
        assert availability.month_bounds("2026-10", at) == (OCT, at), "the current month: up to now"
        assert availability.month_bounds("2026-09", at) == (datetime(2026, 9, 1, tzinfo=UTC), OCT)
        assert availability.month_bounds("2025-12", at)[1] == datetime(2026, 1, 1, tzinfo=UTC)
        for bad in ("2026-13", "26-10", "oct", "", "0000-01", "9999-12"):
            with pytest.raises(availability.ReportError, match="2026-10"):
                availability.month_bounds(bad, at)
        with pytest.raises(availability.ReportError, match="hasn't started"):
            availability.month_bounds("2026-11", at)


class TestReport:
    def test_overlapping_devices_count_once_and_maintenance_is_not_downtime(self, pg_database, clock):
        def setup(conn):
            # Two devices down 10:00-12:00 and 11:00-13:00: three hours.
            incident(conn, "s1", "d1", t(2, 10), t(2, 12))
            incident(conn, "s1", "d2", t(2, 11), t(2, 13))
            # Started in September: only the October part counts (one hour).
            incident(conn, "s1", "d1", OCT - timedelta(hours=5), OCT + timedelta(hours=1))
            # Still down: until now; but a site window 20:00-21:00 on the 20th, ended early at 20:30.
            incident(conn, "s1", "d3", t(20, 20))
            window(conn, "s1", "", t(20, 20), t(20, 21), ended=t(20, 20, 30))
            # A device window covers d4 entirely; a cancelled one covers nothing.
            incident(conn, "s1", "d4", t(5, 8), t(5, 9))
            window(conn, "s1", "d4", t(5, 7), t(5, 10))
            incident(conn, "s1", "d5", t(6, 8), t(6, 9))
            window(conn, "s1", "d5", t(6, 7), t(6, 10), ended=t(6, 6))

        write(setup)
        (site,) = run()["sites"]
        expected = 3 * 3600 + 3600 + (4 * 3600 - 1800) + 0 + 3600
        assert site["downtime_seconds"] == expected
        assert site["incidents"] == 6
        assert site["availability"] == round(100 * (1 - expected / PERIOD), 3)

    def test_consistent_with_the_incident_history(self, pg_database, clock):
        def setup(conn):
            incident(conn, "s1", "d1", t(3, 1), t(3, 2, 30))
            incident(conn, "s1", "d2", t(3, 2), t(3, 4))
            incident(conn, "s1", "d1", t(9), t(9, 0, 10))

        write(setup)
        conn = db.get_conn()
        try:
            rows = conn.execute("SELECT down_started_at, resolved_at FROM alert_instances").fetchall()
        finally:
            conn.close()
        history = availability.union([(r["down_started_at"], r["resolved_at"]) for r in rows])
        assert run()["sites"][0]["downtime_seconds"] == availability.seconds(history) == 3 * 3600 + 600

    def test_at_critical(self, pg_database, clock):
        def setup(conn):
            for site, frm, to, at in (
                ("s1", "ok", "critical", OCT - timedelta(days=1)),  # critical since September
                ("s1", "critical", "low", t(1, 6)),
                ("s1", "low", "critical", t(10)),
                ("s1", "critical", "ok", t(10, 2)),
                ("s2", "critical", "ok", t(3)),  # no earlier change: it was critical from the start
            ):
                conn.execute(
                    "INSERT INTO site_level_changes (site_id, site_name, from_level, to_level, changed_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (site, site.upper(), frm, to, at),
                )
            window(conn, "s1", "", t(10, 1), t(10, 3))  # one of those Critical hours was maintenance

        write(setup)
        sites = {row["site_id"]: row for row in run(basis="critical")["sites"]}
        assert sites["s1"]["downtime_seconds"] == 6 * 3600 + 3600
        assert sites["s2"]["downtime_seconds"] == 2 * 86400

    def test_at_critical_without_a_recorded_change(self, pg_database, clock):
        """A site's first level is not a change: Critical since first seen, or until a later change."""

        def setup(conn):
            conn.execute(
                "INSERT INTO site_alert_levels (site_id, site_name, alert_level, updated_at) VALUES "
                "('always', 'Always', 'critical', %s), ('since', 'Since', 'critical', %s), ('fine', 'Fine', 'ok', %s)",
                (OCT - timedelta(days=3), t(15), OCT - timedelta(days=3)),
            )
            conn.execute(
                "INSERT INTO site_level_changes (site_id, site_name, from_level, to_level, changed_at) "
                "VALUES ('later', 'Later', 'critical', 'ok', %s)",
                (t(25),),  # after the period: it was Critical all along
            )

        write(setup)
        sites = {row["site_id"]: row["downtime_seconds"] for row in run(basis="critical")["sites"]}
        assert sites == {"always": PERIOD, "since": 6 * 86400, "later": PERIOD}

    def test_months_with_deleted_history_are_refused(self, pg_database, clock, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_HISTORY_RETENTION_DAYS", 10)
        with pytest.raises(availability.ReportError, match="History before 2026-10-11 is deleted"):
            run()
        monkeypatch.setattr(settings, "ALERT_HISTORY_RETENTION_DAYS", 30)
        assert run()["period"]["seconds"] == PERIOD

    def test_every_board_site_is_listed_and_tenants_average(self, pg_database, clock):
        write(lambda conn: incident(conn, "s1", "d1", t(2), t(4)))  # two days of twenty: 90 %
        sites = [
            {"id": "s1", "name": "Oslo", "tenants": ["Acme", "Nordic"], "device_count": 3},
            {"id": "s2", "name": "Bergen", "tenants": ["Acme"], "device_count": 1},
            # No monitored devices and no history: no availability to speak of,
            # also when the site is in maintenance.
            {"id": "s3", "name": "Empty", "tenants": ["Acme"], "device_count": 0},
            {"id": "s4", "name": "Planned", "tenants": ["Acme"], "device_count": 0, "maintenance": {"until": "x"}},
        ]
        data = run(sites=sites)
        assert [(r["name"], r["availability"]) for r in data["sites"]] == [("Oslo", 90.0), ("Bergen", 100.0)]
        assert {r["tenant"]: (r["availability"], r["sites"]) for r in data["tenants"]} == {
            "Acme": (95.0, 2),
            "Nordic": (90.0, 1),
        }
        assert data["period"] == {
            "start": "2026-10-01T00:00:00Z",
            "end": "2026-10-21T00:00:00Z",
            "seconds": PERIOD,
            "complete": False,
        }

    def test_unknown_basis(self, pg_database, clock):
        with pytest.raises(availability.ReportError, match="basis"):
            run(basis="uptime")


class TestPages:
    @pytest.fixture
    def client(self, pg_database, clock, monkeypatch):
        write(lambda conn: incident(conn, "s1", "d1", t(2), t(4), name="=Oslo"))
        monkeypatch.setattr(availability, "board_sites", lambda: [{"id": "s1", "name": "=Oslo", "tenants": ["Acme"]}])
        flask_app.app.config["TESTING"] = True
        caching.cache.clear()
        with flask_app.app.test_client() as test_client:
            yield test_client

    def test_page_json_and_csv(self, client):
        page = client.get("/availability?month=2026-10").get_data(as_text=True)
        assert "=Oslo" in page and "90.000 %" in page and "(so far)" in page
        assert 'href="/api/availability.csv?month=2026-10&amp;basis=any_down&amp;view=tenants"' in page
        data = client.get("/api/availability?month=2026-10").get_json()
        assert data["sites"][0]["availability"] == 90.0
        csv_text = client.get("/api/availability.csv?month=2026-10").get_data(as_text=True)
        lines = csv_text.lstrip("﻿").splitlines()
        assert lines[0] == "month,basis,site,tenants,availability_pct,downtime_min,incidents"
        assert lines[1] == "2026-10,any_down,'=Oslo,Acme,90.0,2880,1", "formula-safe"
        tenants = client.get("/api/availability.csv?view=tenants&month=2026-10").get_data(as_text=True)
        assert "2026-10,any_down,Acme,1,90.0,2880" in tenants

    def test_bad_input(self, client):
        assert client.get("/api/availability?month=2026-13").status_code == 400
        assert client.get("/availability?month=2027-01").status_code == 400
        assert client.get("/api/availability.csv?view=x").status_code == 400

    def test_no_database(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as client:
            resp = client.get("/availability")
        assert resp.status_code == 503 and "needs the PostgreSQL database" in resp.get_data(as_text=True)
