"""A tenant's status page (#291): its sites only, with a status token or as the board."""

import json
import shutil
import subprocess

import pytest

from nautobot_maps import alerts, auth, settings, status
from tests import test_tokens
from tests.test_app import auth_config
from tests.test_integration import REPO_ROOT, _extract_js_function
from tests.test_tokens import bearer, make

clock = test_tokens.clock
client = test_tokens.client

BOARD = {
    "checked_at": "2026-10-09T12:00:00Z",
    "alerts": [
        {
            "id": "loc-lon",
            "name": "London HQ",
            "tenant": "Acme Corp",
            "tenants": ["Acme Corp", "Nordnet"],
            "alert_level": "critical",
            "current_downtime_seconds": 3600,
            "physical_address": "1 Canada Square",
            "down_devices": [{"device_name": "lon-core-01", "primary_ip": "10.0.0.1"}],
            "active_cases": [{"case_number": "INC-1"}],
        },
        {"id": "loc-par", "name": "Paris PoP", "tenant": "acme corp", "alert_level": "ok"},
        {
            "id": "loc-ams",
            "name": "Amsterdam",
            "tenant": "Acme Corp",
            "alert_level": "maintenance",
            "maintenance": {"until": "2026-10-09T14:00:00Z", "reason": "Swap core router", "by": "olga"},
        },
        {"id": "loc-osl", "name": "Oslo", "tenant": "Other Co", "alert_level": "critical"},
        {"id": "loc-cph", "name": "Copenhagen", "tenant": "Acme Corp", "alert_level": "low"},
        {"id": "loc-sto", "name": "Stockholm", "tenant": "Acme Corp", "alert_level": "no_data", "device_count": 2},
        {"id": "loc-emea", "name": "EMEA", "tenant": "Acme Corp", "alert_level": "no_data", "device_count": 0},
    ],
}


@pytest.fixture
def board(monkeypatch):
    monkeypatch.setattr(alerts, "get_alert_board_data", lambda *a, **k: BOARD)


class TestTenantStatus:
    def test_only_the_tenants_sites_and_nothing_internal(self):
        result = status.tenant_status(BOARD, "ACME CORP")
        assert result["tenant"] == "Acme Corp"
        assert result["summary"] == {"sites": 5, "affected": 2}
        assert result["sites"] == [
            {"name": "London HQ", "state": "outage", "since": "2026-10-09T11:00:00Z", "until": None},
            {"name": "Copenhagen", "state": "degraded", "since": None, "until": None},
            {"name": "Amsterdam", "state": "maintenance", "since": None, "until": "2026-10-09T14:00:00Z"},
            {"name": "Stockholm", "state": "unknown", "since": None, "until": None},
            {"name": "Paris PoP", "state": "operational", "since": None, "until": None},
        ]
        text = json.dumps(result)
        for secret in ("10.0.0.1", "lon-core-01", "INC-1", "Canada Square", "Swap core", "olga", "Nordnet", "Oslo"):
            assert secret not in text

    def test_a_related_tenant_sees_the_shared_site(self):
        assert [s["name"] for s in status.tenant_status(BOARD, "Nordnet")["sites"]] == ["London HQ"]

    def test_unknown_tenant(self):
        assert status.tenant_status(BOARD, "Nobody") is None


class TestAccess:
    def test_a_status_token_opens_only_its_tenant(self, client, board):
        token = make("acme-status", "status", tenant="Acme Corp")["token"]
        assert client.get("/api/status/Acme%20Corp", headers=bearer(token)).status_code == 200
        assert client.get("/api/status/acme%20corp", headers=bearer(token)).status_code == 200
        assert client.get("/api/status/Other%20Co", headers=bearer(token)).status_code == 403
        for path in ("/api/alerts", "/alerts", "/api/maintenance", "/api/status-other", "/static/js/alerts.js"):
            assert client.get(path, headers=bearer(token)).status_code == 403, path
        response = client.post("/api/alert-cases", json={}, headers=bearer(token))
        assert response.status_code == 403

    def test_a_revoked_status_token_gets_401(self, client, board):
        from nautobot_maps import db, tokens

        created = make("acme-status", "status", tenant="Acme Corp")
        conn = db.get_conn()
        try:
            tokens.revoke(conn, created["id"], "admin")
        finally:
            conn.close()
        assert client.get("/api/status/Acme%20Corp", headers=bearer(created["token"])).status_code == 401

    def test_with_require_viewer_the_page_loads_but_the_data_needs_access(self, client, board, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
        token = make("acme-status", "status", tenant="Acme Corp")["token"]
        viewer = make("staff", "viewer")["token"]
        anonymous = {"REMOTE_ADDR": "203.0.113.9"}
        with auth_config(mode="header"):
            page = client.get("/status/Acme%20Corp", environ_base=anonymous)
            assert page.status_code == 200 and b"Acme Corp" in page.data
            assert page.headers["Referrer-Policy"] == "no-referrer"
            for asset in sorted(auth.STATUS_PAGE_ASSETS):
                assert client.get(asset, environ_base=anonymous).status_code == 200, asset
            assert client.get("/static/js/alerts.js", environ_base=anonymous).status_code == 401
            assert client.get("/api/status/Acme%20Corp", environ_base=anonymous).status_code == 401
            assert client.get("/api/status/Acme%20Corp", headers=bearer(token)).status_code == 200
            assert client.get("/api/status/Other%20Co", headers=bearer(viewer)).status_code == 200

    def test_open_like_the_board_without_require_viewer(self, client, board):
        response = client.get("/api/status/Acme%20Corp")
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        assert client.get("/api/status/Nobody").status_code == 404

    def test_the_page_escapes_the_tenant(self, client, board):
        page = client.get("/status/%3Cscript%3Ex")
        assert b"<script>x" not in page.data and b"&lt;script&gt;x" in page.data

    def test_board_errors_are_a_503(self, client, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("db at postgresql://secret")

        monkeypatch.setattr(alerts, "get_alert_board_data", broken)
        response = client.get("/api/status/Acme%20Corp")
        assert response.status_code == 503 and b"secret" not in response.data


class TestStatusTokens:
    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ({"role": "status"}, "tenant is required"),
            ({"role": "status", "tenant": "  "}, "tenant is required"),
            ({"role": "status", "tenant": "x" * 201}, "tenant is required"),
            ({"role": "viewer", "tenant": "Acme Corp"}, "only for role status"),
        ],
    )
    def test_validation(self, pg_database, clock, body, message):
        from nautobot_maps import tokens

        with pytest.raises(tokens.TokenError, match=message):
            make("t", **body)

    def test_verify_and_cli(self, pg_database, clock, capsys):
        from nautobot_maps import __main__ as cli
        from nautobot_maps import tokens

        assert cli.main(["token", "create", "acme", "--role", "status", "--tenant", " Acme Corp "]) == 0
        secret = capsys.readouterr().out.strip()
        assert tokens.verify(secret) == {"name": "acme", "role": "status", "tenant": "Acme Corp"}
        assert cli.main(["token", "list"]) == 0
        assert "(tenant Acme Corp)" in capsys.readouterr().out


def test_the_page_renderer():
    if shutil.which("node") is None:
        pytest.skip("node is required")
    js = (REPO_ROOT / "static" / "js" / "status.js").read_text(encoding="utf-8")
    labels = js[js.index("const STATE_LABELS") : js.index("};", js.index("const STATE_LABELS")) + 2]
    names = ("escHtml", "shortTime", "headline", "siteLine", "statusKey", "errorText")
    script = (
        "function check(c, m) { if (!c) throw new Error(m); }\n"
        + labels
        + "\n"
        + "\n".join(_extract_js_function(js, n) for n in names)
        + """
check(headline({ summary: { sites: 3, affected: 0 } }) === "All 3 sites operational", "all ok");
check(headline({ summary: { sites: 1, affected: 0 } }) === "Operational", "one ok");
check(headline({ summary: { sites: 5, affected: 2 } }) === "2 of 5 sites affected", "affected");
const now = new Date("2026-10-09T12:00:00Z");
const line = siteLine({ name: "<b>HQ</b>", state: "outage", since: "2026-10-09T11:00:00Z" }, now);
check(line.includes("&lt;b&gt;HQ&lt;/b&gt;") && line.includes("Outage") && line.includes("since "), line);
check(siteLine({ name: "A", state: "maintenance", until: "2026-10-09T14:00:00Z" }, now).includes("until "), "until");
check(siteLine({ name: "A", state: "weird" }, now).includes("No data"), "unknown state");
check(statusKey("#key=nmt_abc") === "nmt_abc" && statusKey("") === "", "key");
check(errorText(403).includes("doesn't work"), "refused");
"""
    )
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr or completed.stdout
