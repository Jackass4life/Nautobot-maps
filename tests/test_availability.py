"""Availability per site, tenant and month (#287)."""

from datetime import UTC, datetime, timedelta

import pytest

import app as flask_app
from nautobot_maps import alerts, availability, caching, db, inventory, settings, timeutil
from tests import http_client

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


def run(month="2026-10", basis="any_down", sites=None, from_date=None, to_date=None, with_incidents=False):
    conn = db.get_conn()
    try:
        return availability.report(conn, month, basis, sites, from_date, to_date, with_incidents=with_incidents)
    finally:
        conn.close()


class TestIntervals:
    def test_union_subtract_clip(self):
        a, b, c = (t(1), t(2)), (t(1, 12), t(3)), (t(5), t(6))
        assert availability.union([c, a, b]) == [(t(1), t(3)), c]
        assert availability.subtract([(t(1), t(5))], [(t(2), t(3)), (t(4), t(10))]) == [(t(1), t(2)), (t(3), t(4))]
        assert availability.clip([(t(1) - timedelta(days=2), t(2))], OCT, t(3)) == [(OCT, t(2))]
        assert availability.seconds([a, b]) == 2 * 86400

    def test_a_month(self):
        at = t(21)

        def bounds(month):
            period = availability.resolve_period(month, None, None, at)
            return period["start"], period["end"]

        assert bounds("2026-10") == (OCT, at), "the current month: up to now"
        assert bounds(None) == (OCT, at), "the default"
        assert bounds("2026-09") == (datetime(2026, 9, 1, tzinfo=UTC), OCT)
        assert bounds("2025-12")[1] == datetime(2026, 1, 1, tzinfo=UTC)
        for bad in ("2026-13", "26-10", "oct", "0000-01", "9999-12"):
            with pytest.raises(availability.ReportError, match="2026-10"):
                bounds(bad)
        with pytest.raises(availability.ReportError, match="hasn't started"):
            bounds("2026-11")
        sept = availability.resolve_period("2026-09", None, None, at)
        assert (sept["from"], sept["to"], sept["month"], sept["label"]) == (
            "2026-09-01",
            "2026-09-30",
            "2026-09",
            "2026-09",
        )

    def test_any_days(self):
        # Any days, both included (#341); the end not after now.
        at = t(21, 9)

        def bounds(from_date, to_date):
            period = availability.resolve_period(None, from_date, to_date, at)
            return period["start"], period["end"]

        assert bounds("2026-10-03", "2026-10-09") == (t(3), t(10))
        assert bounds("2026-10-20", "2026-10-25") == (t(20), at)
        assert bounds("2026-10-21", "2026-10-21") == (t(21), at), "today so far"
        # Months of every length: no "31 September".
        assert bounds("2024-02-01", "2024-02-29")[1] == datetime(2024, 3, 1, tzinfo=UTC)
        for bad, message in (
            (("2026-02-30", "2026-03-01"), "real date"),
            (("2026-9-1", "2026-09-02"), "2026-10-03"),
            (("2026-10-09", "2026-10-03"), "on or after"),
            (("2025-10-01", "2026-10-02"), "at most 366 days"),
            (("2026-10-22", "2026-10-23"), "hasn't started"),
            (("9999-12-31", "9999-12-31"), "real date"),
            (("2026-10-03", None), "both from and to"),
        ):
            with pytest.raises(availability.ReportError, match=message):
                bounds(*bad)
        assert bounds("2025-10-01", "2026-09-30")[0] == datetime(2025, 10, 1, tzinfo=UTC)
        with pytest.raises(availability.ReportError, match="not both"):
            availability.resolve_period("2026-10", "2026-10-03", "2026-10-04", at)

    def test_a_whole_month_from_to_is_that_month(self):
        # The page always sends from and to; a whole month keeps its CSV month (#341).
        at = t(21, 9)
        labels = {
            ("2026-09-01", "2026-09-30"): ("2026-09", "2026-09"),
            ("2026-10-01", "2026-10-31"): ("2026-10", "2026-10"),  # so far
            ("2024-02-01", "2024-02-29"): ("2024-02", "2024-02"),
            ("2026-09-01", "2026-09-29"): (None, "2026-09-01 to 2026-09-29"),
            ("2026-09-02", "2026-09-30"): (None, "2026-09-02 to 2026-09-30"),
            ("2026-09-01", "2026-10-31"): (None, "2026-09-01 to 2026-10-31"),
            ("2026-10-03", "2026-10-03"): (None, "2026-10-03"),
        }
        for (from_date, to_date), expected in labels.items():
            period = availability.resolve_period(None, from_date, to_date, at)
            assert (period["month"], period["label"]) == expected, (from_date, to_date)

    def test_oldest_day(self, monkeypatch):
        # The calendar's first day is the first whole day report() accepts (#341).
        monkeypatch.setattr(settings, "ALERT_HISTORY_RETENTION_DAYS", 0)
        assert availability.oldest_day(t(21, 9)) == ""
        monkeypatch.setattr(settings, "ALERT_HISTORY_RETENTION_DAYS", 10)
        assert availability.oldest_day(t(21, 9)) == "2026-10-12", "the 11th is partly deleted"
        # At midnight too: a moment later the 11th is partly deleted.
        assert availability.oldest_day(t(21)) == "2026-10-12"

    def test_format_downtime(self):
        # As the board shows durations (lib/format.js formatDuration, #311).
        cases = {
            0: "—",
            None: "—",
            59: "0m",
            14 * 60: "14m",
            3600: "1h 0m",
            8040: "2h 14m",
            47 * 3600 + 59 * 60: "47h 59m",
            48 * 3600: "2 days",
            283 * 86400 + 3600: "283 days",
        }
        for seconds, text in cases.items():
            assert availability.format_downtime(seconds) == text, seconds


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
                # Several changes before the month: the last one decides (critical all month).
                ("s4", "ok", "critical", OCT - timedelta(days=30)),
                ("s4", "critical", "low", OCT - timedelta(days=20)),
                ("s4", "low", "critical", OCT - timedelta(days=10)),
            ):
                conn.execute(
                    "INSERT INTO site_level_changes (site_id, site_name, from_level, to_level, changed_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (site, site.upper(), frm, to, at),
                )
            window(conn, "s1", "", t(10, 1), t(10, 3))  # one of those Critical hours was maintenance

            # A site only in the history, never Critical: listed, with its incident.
            incident(conn, "s3", "d1", t(4), t(4, 1))

        write(setup)
        sites = {row["site_id"]: row for row in run(basis="critical")["sites"]}
        assert sites["s1"]["downtime_seconds"] == 6 * 3600 + 3600
        assert sites["s2"]["downtime_seconds"] == 2 * 86400
        assert sites["s4"]["downtime_seconds"] == PERIOD
        assert (sites["s3"]["downtime_seconds"], sites["s3"]["incidents"]) == (0, 1)

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

    def test_first_seen_mid_month_then_changed(self, pg_database, clock):
        """First seen Critical on the 15th, back to OK on the 20th: five days, not from the 1st."""

        def setup(conn):
            conn.execute(
                "INSERT INTO site_alert_levels (site_id, site_name, alert_level, updated_at, first_seen_at) "
                "VALUES ('new', 'New', 'ok', %s, %s)",
                (t(20), t(15)),
            )
            conn.execute(
                "INSERT INTO site_level_changes (site_id, site_name, from_level, to_level, changed_at) "
                "VALUES ('new', 'New', 'critical', 'ok', %s)",
                (t(20),),
            )

        write(setup)
        (site,) = run(basis="critical")["sites"]
        assert site["downtime_seconds"] == 5 * 86400

    def test_months_with_deleted_history_are_refused(self, pg_database, clock, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_HISTORY_RETENTION_DAYS", 10)
        with pytest.raises(availability.ReportError, match="History before 2026-10-11 is deleted"):
            run()
        monkeypatch.setattr(settings, "ALERT_HISTORY_RETENTION_DAYS", 30)
        assert run()["period"]["seconds"] == PERIOD

    def test_every_board_site_is_listed_and_tenants_average(self, pg_database, clock):
        def setup(conn):
            incident(conn, "s1", "d1", t(2), t(4))  # two days of twenty: 90 %
            incident(conn, "s5", "d9", t(5), t(6), name="history name")  # one day: 95 %

        write(setup)
        sites = [
            {"id": "s1", "name": "Oslo", "tenants": ["Acme", "Nordic"], "device_count": 3},
            {"id": "s2", "name": "Bergen", "tenants": ["Acme"], "device_count": 1},
            # No monitored devices and no history: no availability to speak of,
            # also when the site is in maintenance.
            {"id": "s3", "name": "Empty", "tenants": ["Acme"], "device_count": 0},
            {"id": "s4", "name": "Planned", "tenants": ["Acme"], "device_count": 0, "maintenance": {"until": "x"}},
            # Unmonitored now, but down in the month: listed, with its board tenants.
            {"id": "s5", "name": "Gone quiet", "tenants": ["Nordic"], "device_count": 0},
        ]
        data = run(sites=sites)
        assert [(r["name"], r["availability"]) for r in data["sites"]] == [
            ("Oslo", 90.0),
            ("Gone quiet", 95.0),
            ("Bergen", 100.0),
        ]
        assert {r["tenant"]: (r["availability"], r["sites"]) for r in data["tenants"]} == {
            "Acme": (95.0, 2),
            "Nordic": (92.5, 2),
        }
        assert data["period"] == {
            "start": "2026-10-01T00:00:00Z",
            "end": "2026-10-21T00:00:00Z",
            "seconds": PERIOD,
            "complete": False,
            "from": "2026-10-01",
            "to": "2026-10-31",
            "label": "2026-10",
        }

    def test_a_from_to_period(self, pg_database, clock, monkeypatch):
        write(lambda conn: incident(conn, "s1", "d1", t(2, 12), t(4, 12)))
        sites = [{"id": "s1", "name": "Oslo", "tenants": ["Acme"], "device_count": 1}]
        # 3–4 October: the 2nd's half day is outside, 36 of 48 hours down.
        data = run(None, sites=sites, from_date="2026-10-03", to_date="2026-10-04")
        assert data["sites"][0]["downtime_seconds"] == 36 * 3600
        assert data["sites"][0]["availability"] == 25.0
        assert data["month"] is None
        assert data["period"] == {
            "start": "2026-10-03T00:00:00Z",
            "end": "2026-10-05T00:00:00Z",
            "seconds": 2 * 86400,
            "complete": True,
            "from": "2026-10-03",
            "to": "2026-10-04",
            "label": "2026-10-03 to 2026-10-04",
        }
        # A month says its days too.
        month = run(sites=sites)["period"]
        assert (month["from"], month["to"], month["label"]) == ("2026-10-01", "2026-10-31", "2026-10")
        assert run("2026-02", sites=sites)["period"]["to"] == "2026-02-28"
        monkeypatch.setattr(settings, "ALERT_HISTORY_RETENTION_DAYS", 10)
        with pytest.raises(availability.ReportError, match="2026-10-03 to 2026-10-04 can't be reported"):
            run(None, from_date="2026-10-03", to_date="2026-10-04")
        assert run(None, from_date="2026-10-11", to_date="2026-10-20")["period"]["seconds"] == 10 * 86400

    def test_the_summary_and_tenant_descriptions(self, pg_database, clock):
        # The page's cards (#342): every site counts the same.
        write(lambda conn: incident(conn, "s1", "d1", t(2), t(4)))  # 90 %
        sites = [
            {
                "id": "s1",
                "name": "Oslo",
                "tenants": ["Acme"],
                "device_count": 1,
                "tenant_descriptions": {"Acme": "Gold <VIP>"},
            },
            {
                "id": "s2",
                "name": "Bergen",
                "tenants": ["Nordic"],
                "device_count": 1,
                "tenant_descriptions": {"Nordic": "", "Unlisted": "not a tenant here"},
            },
        ]
        data = run(sites=sites)
        assert data["summary"] == {
            "sites": 2,
            "availability": 95.0,
            "downtime_seconds": 2 * 86400,
            "incidents": 1,
            "below_target": 1,
            "no_downtime": 1,
            "target": 99.9,
            "worst_tenant": {"tenant": "Acme", "sites": 1, "availability": 90.0, "downtime_seconds": 2 * 86400},
        }
        assert data["tenant_descriptions"] == {"Acme": "Gold <VIP>"}, "only listed tenants with a description"
        empty = run("2026-09", sites=[])
        assert empty["summary"]["availability"] == 100.0 and empty["summary"]["worst_tenant"] is None
        # No downtime at all: no tenant is named the worst.
        quiet = run("2026-09", sites=sites)["summary"]
        assert quiet["worst_tenant"] is None and quiet["no_downtime"] == 2

    def test_outages_maintenance_and_incidents(self, pg_database, clock):
        # #354: outages are the merged down time after maintenance; each incident
        # says what maintenance covered and carries its cases.
        def setup(conn):
            incident(conn, "s1", "d1", t(2, 10), t(2, 12))  # 2 h
            incident(conn, "s1", "d2", t(2, 11), t(2, 13))  # overlaps: one outage of 3 h
            incident(conn, "s1", "d3", t(5, 8), t(5, 9))  # 1 h, half in a site window
            window(conn, "s1", "", t(5, 8, 30), t(5, 10))
            incident(conn, "s1", "d4", t(6, 8), t(6, 9))  # fully in a device window
            window(conn, "s1", "d4", t(6, 7), t(6, 10))
            row = conn.execute("SELECT id FROM alert_instances WHERE device_id = 'd2'").fetchone()
            conn.execute(
                "INSERT INTO alert_cases (alert_instance_id, case_number) VALUES (%s, 'INC-2'), (%s, 'INC-1')",
                (row["id"], row["id"]),
            )

        write(setup)
        sites = [
            {
                "id": "s1",
                "name": "Oslo",
                "tenants": ["Acme"],
                "device_count": 4,
                "ancestor_path": "EMEA › NOR",
                "physical_address": "Storgata 1",
                "country": "Norway",
                "tier": "gold",
            }
        ]
        data = run(sites=sites)
        (site,) = data["sites"]
        assert (site["path"], site["address"], site["country"], site["tier"], site["monitored_devices"]) == (
            "EMEA › NOR",
            "Storgata 1",
            "Norway",
            "gold",
            4,
        )
        assert site["outages"] == 2 and site["downtime_seconds"] == 3 * 3600 + 1800
        assert site["longest_outage_seconds"] == 3 * 3600 and site["mean_outage_seconds"] == (3 * 3600 + 1800) // 2
        # When they began; the mean time to restore counts restored outages only.
        assert (site["first_outage"], site["last_outage"]) == ("2026-10-02T10:00:00Z", "2026-10-05T08:00:00Z")
        assert site["maintenance_seconds"] == 90 * 60, "the site-wide window only"
        conn = db.get_conn()
        try:
            # Streamed: read while the connection is open, as the export does.
            listed = list(availability.report(conn, "2026-10", "any_down", sites, with_incidents=True)["incidents"])
        finally:
            conn.close()
        by_device = {r["device"]: r for r in listed}
        assert by_device["d2"]["cases"] == "INC-1;INC-2", "as the site history's export"
        assert (by_device["d3"]["maintenance"], by_device["d3"]["minutes_counted"]) == ("partly", 30)
        assert (by_device["d4"]["maintenance"], by_device["d4"]["minutes_counted"]) == ("full", 0)
        assert by_device["d1"]["maintenance"] == "none" and by_device["d1"]["site_path"] == "EMEA › NOR"
        assert [r["device"] for r in listed] == ["d1", "d2", "d3", "d4"], "oldest first"

    def test_still_down_has_no_restore_time(self, pg_database, clock):
        # An outage still on at the report's end isn't restored: no mean time to restore (review on #354).
        write(lambda conn: incident(conn, "s1", "d1", t(19)))
        (site,) = run(sites=[{"id": "s1", "name": "Oslo", "device_count": 1}])["sites"]
        assert (
            site["outages"] == 1 and site["mean_outage_seconds"] is None and site["longest_outage_seconds"] == 2 * 86400
        )
        report = run(sites=[{"id": "s1", "name": "Oslo", "device_count": 1}])
        line = "".join(availability.csv_lines(report, "sites")).splitlines()[1]
        assert ",1,2880,,2026-10-19 00:00,2026-10-19 00:00," in line

    def test_too_many_incidents_are_refused(self, pg_database, clock, monkeypatch):
        write(lambda conn: (incident(conn, "s1", "d1", t(2), t(3)), incident(conn, "s1", "d2", t(4), t(5))))
        monkeypatch.setattr(availability, "INCIDENT_EXPORT_LIMIT", 1)
        with pytest.raises(availability.ReportError, match="More than 1 incidents"):
            run(with_incidents=True)
        assert "incidents" not in run(), "only the export reads them"

    def test_unknown_basis(self, pg_database, clock):
        with pytest.raises(availability.ReportError, match="basis"):
            run(basis="uptime")


REAL_BOARD_SITES = availability.board_sites


class TestPages:
    @pytest.fixture
    def client(self, pg_database, clock, monkeypatch):
        write(lambda conn: incident(conn, "s1", "d1", t(2), t(4), name="=Oslo"))
        monkeypatch.setattr(availability, "board_sites", lambda: [{"id": "s1", "name": "=Oslo", "tenants": ["Acme"]}])
        flask_app.app.config["TESTING"] = True
        caching.cache.clear()
        with http_client.FlaskStyleClient() as test_client:
            yield test_client

    def test_the_page_explains_itself(self, client):
        """A help (i) for someone new to the page: what it shows, each choice and figure."""
        html = client.get("/availability?month=2026-10").get_data(as_text=True)
        assert html.count('class="info-tip help-tip"') == 9
        for text in (
            "How much of the time each site and tenant was up",  # the title
            "What counts as down",  # the Downtime choice
            "1 − downtime ÷ period",  # the Availability card
            "An incident is one device going down",  # the Downtime card
            "up less than 99.9 % of the period",  # Below target, with the target
            "The tenant with the lowest availability",
            "the average of its sites",  # Tenants
            "Every monitored site, the worst first",  # Sites
            "Every incident: each device outage",  # the CSV links
        ):
            assert text in html, text
        # Read aloud as help.
        assert 'aria-label="Help: How much of the time' in html

    def test_the_streamed_csv_is_the_same_through_fastapi(self, client, asgi_client):
        """#436: Flask streams it row by row; through a2wsgi it arrives whole and unchanged."""
        flask_csv = client.get("/api/availability.csv?month=2026-10&view=incidents&header=1").get_data()
        asgi = asgi_client.get("/api/availability.csv?month=2026-10&view=incidents&header=1")
        assert asgi.status_code == 200 and asgi.headers["content-type"].startswith("text/csv")
        assert asgi.content == flask_csv and len(flask_csv.splitlines()) > 3

    def test_page_json_and_csv(self, client):
        page = client.get("/availability?month=2026-10").get_data(as_text=True)
        assert "=Oslo" in page and "90.000 %" in page and "(so far)" in page
        assert (
            'href="/api/availability.csv?from=2026-10-01&amp;to=2026-10-31&amp;basis=any_down&amp;view=tenants&amp;header=1"'
            in page
        )
        data = client.get("/api/availability?month=2026-10").get_json()
        assert data["sites"][0]["availability"] == 90.0
        csv_text = client.get("/api/availability.csv?month=2026-10").get_data(as_text=True)
        lines = csv_text.lstrip("﻿").splitlines()
        # The first columns as before; the site's details and outages after them (#354).
        assert lines[0] == (
            "month,basis,site,tenants,availability_pct,downtime_min,incidents,period_from,period_to,"
            "site_path,address,country,tier,monitored_devices,outages,longest_outage_min,"
            "mean_time_to_restore_min,first_outage_utc,last_outage_utc,maintenance_min,site_id"
        )
        assert lines[1] == (
            "2026-10,any_down,'=Oslo,Acme,90.0,2880,1,2026-10-01,2026-10-31,"
            ",,,,0,1,2880,2880,2026-10-02 00:00,2026-10-02 00:00,0,s1"
        ), "formula-safe"
        tenants = client.get("/api/availability.csv?view=tenants&month=2026-10").get_data(as_text=True)
        assert "2026-10,any_down,Acme,1,90.0,2880,2026-10-01,2026-10-31" in tenants

    def test_a_from_to_period_on_the_page_json_and_csv(self, client):
        # #341: down the 2nd and 3rd; the 3rd and 4th asked for: half.
        query = "from=2026-10-03&to=2026-10-04"
        page = client.get(f"/availability?{query}").get_data(as_text=True)
        assert "50.000 %" in page and "(so far)" not in page
        assert 'name="from" type="date" value="2026-10-03"' in page and 'value="2026-10-04"' in page
        assert (
            'href="/api/availability.csv?from=2026-10-03&amp;to=2026-10-04&amp;basis=any_down&amp;view=sites&amp;header=1"'
            in page
        )
        assert 'data-today="2026-10-21"' in page
        data = client.get(f"/api/availability?{query}").get_json()
        assert data["period"]["label"] == "2026-10-03 to 2026-10-04" and data["month"] is None
        resp = client.get(f"/api/availability.csv?{query}")
        assert 'filename="availability-sites-2026-10-03_2026-10-04.csv"' in resp.headers["Content-Disposition"]
        assert (
            resp.get_data(as_text=True).lstrip("﻿").splitlines()[1]
            == ",any_down,'=Oslo,Acme,50.0,1440,1,2026-10-03,2026-10-04,,,,,0,1,1440,1440,2026-10-03 00:00,2026-10-03 00:00,0,s1"
        )
        one_day = client.get("/api/availability.csv?from=2026-10-03&to=2026-10-03")
        one_day.get_data()  # streamed (#354): read it, as a browser does
        assert 'filename="availability-sites-2026-10-03.csv"' in one_day.headers["Content-Disposition"]
        # A month, asked for either way, is that month: its CSV keeps the month column.
        month = client.get("/availability?month=2026-10").get_data(as_text=True)
        assert 'name="from" type="date" value="2026-10-01"' in month and 'value="2026-10-31"' in month
        assert "from=2026-10-01&amp;to=2026-10-31&amp;basis=any_down" in month
        resp = client.get("/api/availability.csv?from=2026-10-01&to=2026-10-31")
        assert 'filename="availability-sites-2026-10.csv"' in resp.headers["Content-Disposition"]
        assert resp.get_data(as_text=True).lstrip("\ufeff").splitlines()[1].startswith("2026-10,any_down,")

    def test_the_header_and_every_incident(self, client):
        # #354: a header above the table on request, and every outage on its own.
        sites = client.get("/api/availability.csv?month=2026-10&header=1").get_data(as_text=True)
        lines = sites.lstrip("\ufeff").splitlines()
        blank = lines.index("")
        header = dict(line.split(",", 1) for line in lines[:blank])
        assert header["report"] == "Nautobot Maps availability"
        assert header["period"] == '"2026-10-01 to 2026-10-31 (UTC), so far"'
        assert header["basis"] == "any_down" and header["target_pct"] == "99.9"
        assert header["sites"] == "1" and header["availability_pct"] == "90.0" and header["sites_below_target"] == "1"
        assert header["generated_at_utc"] == "2026-10-21 00:00"
        assert lines[blank + 1].startswith("month,basis,site,")
        resp = client.get("/api/availability.csv?month=2026-10&view=incidents")
        assert 'filename="availability-incidents-2026-10.csv"' in resp.headers["Content-Disposition"]
        rows = resp.get_data(as_text=True).lstrip("\ufeff").splitlines()
        assert rows[0] == ",".join(availability.INCIDENT_COLUMNS)
        assert rows[1] == "'=Oslo,,Acme,d1,2026-10-02 00:00,2026-10-04 00:00,2880,2880,none,,,s1,d1"
        assert client.get("/api/availability.csv?view=devices").status_code == 400

    def test_the_page_answers_first(self, client, monkeypatch):
        # #342: four cards, bars, the tenant (i), and data for sorting; escaped.
        monkeypatch.setattr(
            availability,
            "board_sites",
            lambda: [
                {
                    "id": "s1",
                    "name": "=Oslo",
                    "tenants": ["Acme", "O'Brien <Ltd>"],
                    "tenant_descriptions": {"Acme": 'Bank <"VIP">'},
                }
            ],
        )
        page = client.get("/availability?month=2026-10").get_data(as_text=True)
        assert page.count('class="avail-card"') == 4
        assert "90.000 %" in page and "2 days" in page and "1 incident," in page
        assert '<meter class="pct-bar" min="98" max="100" low="99" high="99.9" optimum="100" value="98.1"' in page
        assert 'data-info-tip="Bank &lt;&#34;VIP&#34;&gt;"' in page and 'Bank <"VIP">' not in page
        # Site rows name the tenant; the description is in the section once (#342).
        assert 'data-tenant-tip="Acme">i</span>' in page
        assert """data-tenant-descriptions='{"Acme": "Bank \\u003c\\"VIP\\"\\u003e"}'""" in page
        assert 'data-availability="90.0" data-downtime="172800" data-incidents="1"' in page
        # JSON in a single-quoted attribute: the quote and the brackets are escaped.
        assert """data-tenants='["Acme", "O\\u0027Brien \\u003cLtd\\u003e"]'""" in page
        assert '<span class="tenant-rest" hidden>, O&#39;Brien &lt;Ltd&gt;</span>' in page
        assert 'title="O&#39;Brien &lt;Ltd&gt;">+1</button>' in page
        assert 'href="/static/css/tooltip.css"' in page, "the (i) needs its styles"
        quiet = client.get("/availability?month=2026-09").get_data(as_text=True)
        assert "none had downtime" in quiet

    def test_an_unreadable_board_is_a_503_not_a_partial_report(self, client, monkeypatch):
        def broken():
            raise RuntimeError("board build failed")

        monkeypatch.setattr(availability, "board_sites", broken)
        resp = client.get("/api/availability")
        assert resp.status_code == 503 and "would miss sites" in resp.get_json()["error"]
        assert client.get("/availability").status_code == 503

    def test_an_empty_board_before_the_first_sync_is_a_503(self, client, monkeypatch):
        monkeypatch.setattr(availability, "board_sites", REAL_BOARD_SITES)
        monkeypatch.setattr(alerts, "cached_alert_board", lambda: {"alerts": []})
        monkeypatch.setattr(inventory, "snapshot_initialized", lambda conn=None: False)
        assert client.get("/api/availability").status_code == 503
        monkeypatch.setattr(inventory, "snapshot_initialized", lambda conn=None: True)
        assert client.get("/api/availability").status_code == 200, "synced, and really no sites"

    def test_the_period_query_can_use_the_index(self, pg_database):
        conn = db.get_conn()
        try:
            conn.execute("SET enable_seqscan = off")
            plan = "\n".join(
                str(next(iter(db.row_to_dict(row).values())))
                for row in conn.execute(
                    "EXPLAIN SELECT 1 FROM alert_instances "
                    "WHERE COALESCE(resolved_at, 'infinity'::timestamptz) > now() AND down_started_at < now()"
                ).fetchall()
            )
        finally:
            conn.close()
        assert "idx_alert_instances_period" in plan, plan

    def test_bad_input(self, client):
        assert client.get("/api/availability?month=2026-13").status_code == 400
        assert client.get("/availability?month=2027-01").status_code == 400
        assert client.get("/api/availability.csv?view=x").status_code == 400
        resp = client.get("/availability?from=2026-02-30&to=2026-03-02")
        assert resp.status_code == 400 and "real date" in resp.get_data(as_text=True)
        # The form keeps what was asked for.
        assert 'value="2026-02-30"' in resp.get_data(as_text=True)
        assert client.get("/api/availability?from=2026-10-03").status_code == 400

    def test_no_database(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        flask_app.app.config["TESTING"] = True
        with http_client.FlaskStyleClient() as client:
            resp = client.get("/availability")
        assert resp.status_code == 503 and "needs the PostgreSQL database" in resp.get_data(as_text=True)


def test_migration_15_backfills_first_seen_where_it_is_known(pg_database):
    def setup(conn):
        conn.execute(
            "INSERT INTO site_alert_levels (site_id, site_name, alert_level, updated_at) VALUES "
            "('steady', 'S', 'ok', %s), ('changed', 'C', 'ok', %s)",
            (t(3), t(9)),
        )
        conn.execute(
            "INSERT INTO site_level_changes (site_id, site_name, from_level, to_level, changed_at) "
            "VALUES ('changed', 'C', 'low', 'ok', %s)",
            (t(9),),
        )
        conn.execute("UPDATE site_alert_levels SET first_seen_at = NULL")
        db.add_site_first_seen(conn)

    write(setup)
    conn = db.get_conn()
    try:
        rows = {
            r["site_id"]: r["first_seen_at"]
            for r in conn.execute("SELECT site_id, first_seen_at FROM site_alert_levels")
        }
    finally:
        conn.close()
    assert rows["steady"] == t(3), "never changed: updated_at is when it was first seen"
    assert rows["changed"] is None, "changed since: unknown"
