"""Case numbers link to the ITSM ticket (#289)."""

import os
import shutil
import subprocess
import sys

import pytest

import app as flask_app
from nautobot_maps import itsm, notify, settings
from tests.test_integration import REPO_ROOT, _extract_js_function

TEMPLATE = "https://itsm.example.com/ticket/{case}"


def test_case_url(monkeypatch):
    monkeypatch.setattr(settings, "CASE_URL_TEMPLATE", TEMPLATE)
    assert itsm.case_url("INC 1/2&x") == "https://itsm.example.com/ticket/INC%201%2F2%26x"
    assert itsm.case_url("") == ""
    monkeypatch.setattr(settings, "CASE_URL_TEMPLATE", "")
    assert itsm.case_url("INC-1") == ""


@pytest.mark.parametrize(
    "value, expected",
    [
        (TEMPLATE, TEMPLATE),
        ("javascript:alert({case})", ""),
        ("https://itsm.example.com/ticket/", ""),  # no {case}
        ("", ""),
    ],
)
def test_only_http_templates_with_a_placeholder(value, expected):
    code = "from nautobot_maps import settings; print(settings.CASE_URL_TEMPLATE)"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env={**os.environ, "CASE_URL_TEMPLATE": value},
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == expected
    if value and not expected:
        assert "CASE_URL_TEMPLATE ignored" in completed.stderr


def test_the_board_page_gets_the_template(monkeypatch):
    monkeypatch.setattr(settings, "CASE_URL_TEMPLATE", TEMPLATE)
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        html = client.get("/alerts").get_data(as_text=True)
    assert f'data-case-url-template="{TEMPLATE}"' in html


def test_notifications_add_the_link(monkeypatch):
    monkeypatch.setattr(settings, "CASE_URL_TEMPLATE", TEMPLATE)
    site = {"id": "s", "name": "S", "down_devices": [{"device_name": "sw1", "case_numbers": ["INC-7"]}]}
    event = notify.build_event("alarm", "ok", "low", site, "2026-10-10T12:00:00Z")
    assert "Cases: INC-7 (https://itsm.example.com/ticket/INC-7)" in notify.detail_lines(event)


def test_the_csv_has_the_links(monkeypatch):
    from datetime import UTC, datetime

    from nautobot_maps import export

    monkeypatch.setattr(settings, "CASE_URL_TEMPLATE", TEMPLATE)
    rows = [
        {
            "site_name": "S",
            "device_name": "sw1",
            "status": "open",
            "cases": "INC-1;INC;7",
            "case_list": ["INC-1", "INC;7"],
        }
    ]
    lines = list(export.incident_lines(rows, datetime.now(UTC)))
    # A number with ";" is one ticket, not two.
    assert lines[1].rstrip().endswith(",https://itsm.example.com/ticket/INC-1;https://itsm.example.com/ticket/INC%3B7")


def test_the_csv_reads_the_numbers_as_a_list(pg_database, monkeypatch):
    from datetime import UTC, datetime

    from nautobot_maps import db, export

    monkeypatch.setattr(settings, "CASE_URL_TEMPLATE", TEMPLATE)
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            row = conn.execute(
                "INSERT INTO alert_instances (alert_key, site_id, site_name, device_id, device_name, alert_level, "
                "status, down_started_at, last_seen_down_at) "
                "VALUES ('k', 's', 'S', 'd', 'sw1', 'low', 'open', now(), now()) RETURNING id"
            ).fetchone()
            conn.execute(
                "INSERT INTO alert_cases (alert_instance_id, case_number, created_by) VALUES (%s, 'INC;7', 'x')",
                (row["id"],),
            )
        lines = list(export.incident_lines(export.iter_site_incidents(conn, "s", None), datetime.now(UTC)))
    finally:
        conn.close()
    assert lines[1].rstrip().endswith(",https://itsm.example.com/ticket/INC%3B7")


class TestInTheBrowser:
    def _run(self, body, template=TEMPLATE):
        if shutil.which("node") is None:
            pytest.skip("node is required")
        js = (REPO_ROOT / "static" / "js" / "alerts.js").read_text(encoding="utf-8")
        functions = "\n".join(_extract_js_function(js, n) for n in ("escHtml", "caseUrl", "casePill", "caseWithUrl"))
        script = f'function check(c, m) {{ if (!c) throw new Error(m); }}\nconst CASE_URL_TEMPLATE = "{template}";\n{functions}\n{body}'
        completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        assert completed.returncode == 0, completed.stderr or completed.stdout

    def test_a_link_to_the_ticket(self):
        self._run("""
const html = casePill('INC "7"');
check(html === '<a class="case-pill" href="https://itsm.example.com/ticket/INC%20%227%22" target="_blank" rel="noopener noreferrer" title="Open the ticket">INC &quot;7&quot;</a>', html);
check(caseWithUrl("INC-7") === "INC-7 (https://itsm.example.com/ticket/INC-7)", "Copy text");
""")

    def test_plain_without_a_template(self):
        self._run(
            """
check(casePill("INC-7") === '<span class="case-pill">INC-7</span>', casePill("INC-7"));
check(caseWithUrl("INC-7") === "INC-7", "Copy text");
""",
            template="",
        )
