"""A tenant's status page (#291): its sites only, with a status token or as the board."""

import json

import pytest

from nautobot_maps import alerts, db, inventory, nautobot, settings, status
from tests import test_tokens
from tests.test_app import auth_config
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


class TestTenantScopes:
    def test_scoped_level_and_since_use_only_the_matching_roles(self):
        checked_at = "2026-10-09T12:00:00Z"
        devices = [
            {"role": "Firewall", "status": "Offline", "first_seen_down_at": "2026-10-09T11:00:00Z"},
            {"role": "Firewall", "status": "Active"},
            {"role": "Core Router", "status": "Offline", "first_seen_down_at": "2026-10-09T10:00:00Z"},
        ]
        assert alerts.compute_alert_level(devices)["level"] == "critical"
        scoped = alerts.tenant_scoped_alerts(
            {"acme corp": ["firewall"]},
            ["Acme Corp"],
            devices,
            [],
            [],
            None,
            checked_at,
            None,
            {},
            "",
        )
        assert scoped == {
            "acme corp": {
                "alert_level": "medium",
                "device_count": 2,
                "current_downtime_seconds": 3600,
            }
        }
        board = {
            "checked_at": checked_at,
            "alerts": [
                {
                    "name": "Scoped site",
                    "tenant": "Acme Corp",
                    "alert_level": "critical",
                    "current_downtime_seconds": 7200,
                    "tenant_alerts": scoped,
                }
            ],
        }
        result = status.tenant_status(board, "ACME CORP")
        assert result["sites"] == [
            {"name": "Scoped site", "state": "degraded", "since": "2026-10-09T11:00:00Z", "until": None}
        ]
        assert "Firewall" not in json.dumps(result)

    def test_no_matching_role_hides_site(self):
        scoped = alerts.tenant_scoped_alerts(
            {"acme corp": ["firewall"]}, ["Acme Corp"], [], [], [], None, "2026-10-09T12:00:00Z", None, {}, ""
        )
        board = {
            "alerts": [
                {
                    "name": "No firewall here",
                    "tenant": "Acme Corp",
                    "alert_level": "critical",
                    "device_count": 30,
                    "tenant_alerts": scoped,
                }
            ]
        }
        assert status.tenant_status(board, "Acme Corp") is None

    def test_maintenance_and_stacks_follow_board_counting(self):
        window = {"until": "2026-10-09T14:00:00Z"}
        active = [{"id": "fw", "role": "Firewall", "status": "Offline"}]
        maintenance = alerts.tenant_scoped_alerts(
            {"acme corp": ["firewall"]},
            ["Acme Corp"],
            active,
            [],
            [],
            window,
            "2026-10-09T12:00:00Z",
            None,
            {},
            "",
        )
        assert maintenance["acme corp"]["alert_level"] == "maintenance"

        stack = alerts.merge_stacks(
            [
                {"id": "lead", "role": "Access Switch", "status": "Active", "vc_id": "vc1", "vc_master": True},
                {"id": "member", "role": "Access Switch", "status": "Offline", "vc_id": "vc1", "vc_position": 2},
            ]
        )
        stacked = alerts.tenant_scoped_alerts(
            {"acme corp": ["access switch"]},
            ["Acme Corp"],
            stack,
            [],
            [],
            None,
            "2026-10-09T12:00:00Z",
            None,
            {},
            "",
        )
        assert stacked["acme corp"]["alert_level"] == "critical"
        assert stacked["acme corp"]["device_count"] == 1

    def test_scoped_devices_all_in_maintenance_show_maintenance(self):
        """Only the tenant's devices in a window: maintenance, not a hidden site (#410 review)."""
        firewall = {"id": "fw", "role": "Firewall", "status": "Offline"}
        core = {"id": "core", "role": "Core Router", "status": "Active"}
        scoped = alerts.tenant_scoped_alerts(
            {"acme corp": ["firewall"]},
            ["Acme Corp"],
            [core],
            [],
            [firewall],
            None,
            "2026-10-09T12:00:00Z",
            None,
            {},
            "",
        )
        assert scoped["acme corp"] == {"alert_level": "maintenance", "device_count": 1, "current_downtime_seconds": 0}
        board = {"alerts": [{"name": "Fw work", "tenant": "Acme Corp", "alert_level": "ok", "tenant_alerts": scoped}]}
        assert status.tenant_status(board, "Acme Corp")["sites"][0]["state"] == "maintenance"

    def test_location_scope_parsing_and_one_warning_for_bad_fields(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "TENANT_SCOPE_FIELD", "tenant_scope")
        monkeypatch.setattr(nautobot, "id_name_map", lambda endpoint: {})
        monkeypatch.setattr(nautobot, "tenant_group_map", lambda: {})
        location = {
            "id": "loc-1",
            "name": "Scoped",
            "latitude": 55.0,
            "longitude": 12.0,
            "tenant": {"name": "Acme Corp"},
            "location_type": {"name": "Site"},
            "custom_fields": {"tenant_scope": '{"Acme Corp":["Access Switch","Firewall"]}'},
        }
        normalized = inventory.normalize_locations([location], include_without_coordinates=True)
        assert normalized[0]["tenant_scopes"] == {"acme corp": ["access switch", "firewall"]}

        malformed = [{**location, "id": f"loc-{index}", "custom_fields": {"tenant_scope": "{bad"}} for index in (2, 3)]
        normalized = inventory.normalize_locations(malformed, include_without_coordinates=True)
        assert [item["tenant_scopes"] for item in normalized] == [{}, {}]
        warnings = [record for record in caplog.records if "Invalid tenant_scope custom field" in record.message]
        assert len(warnings) == 1
        assert "2 locations" in warnings[0].message


class TestAccess:
    def test_a_status_token_opens_only_its_tenant(self, client, board):
        token = make("acme-status", "status", tenant="Acme Corp")["token"]
        assert client.get("/api/status/Acme%20Corp", headers=bearer(token)).status_code == 200
        assert client.get("/api/status/acme%20corp", headers=bearer(token)).status_code == 200
        assert client.get("/api/status/Other%20Co", headers=bearer(token)).status_code == 403
        for path in ("/api/alerts", "/alerts", "/api/maintenance", "/api/status-other", "/static/css/other.css"):
            assert client.get(path, headers=bearer(token)).status_code == 403, path
        response = client.post("/api/alert-cases", json={}, headers=bearer(token))
        assert response.status_code == 403

    def test_a_revoked_status_token_gets_401(self, client, board):
        from nautobot_maps import tokens

        created = make("acme-status", "status", tenant="Acme Corp")
        conn = db.get_conn()
        try:
            tokens.revoke(conn, created["id"], "admin")
        finally:
            conn.close()
        assert client.get("/api/status/Acme%20Corp", headers=bearer(created["token"])).status_code == 401

    def test_with_require_viewer_the_page_loads_but_the_data_needs_access(
        self, client, board, monkeypatch, frontend_build
    ):
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
        token = make("acme-status", "status", tenant="Acme Corp")["token"]
        viewer = make("staff", "viewer")["token"]
        anonymous = {"REMOTE_ADDR": "203.0.113.9"}
        with auth_config(mode="header"):
            page = client.get("/status/Acme%20Corp", environ_base=anonymous)
            assert page.status_code == 200 and b'id="__nuxt"' in page.data
            assert page.headers["Referrer-Policy"] == "no-referrer"
            # The Nuxt build's files (#463): code only, no data.
            assert client.get("/_nuxt/entry.abc123.js", environ_base=anonymous).status_code == 200
            assert client.get("/static/css/other.css", environ_base=anonymous).status_code == 401
            assert client.get("/api/status/Acme%20Corp", environ_base=anonymous).status_code == 401
            assert client.get("/api/status/Acme%20Corp", headers=bearer(token)).status_code == 200
            assert client.get("/api/status/Other%20Co", headers=bearer(viewer)).status_code == 200

    def test_open_like_the_board_without_require_viewer(self, client, board, monkeypatch):
        response = client.get("/api/status/Acme%20Corp")
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        monkeypatch.setattr(inventory, "snapshot_initialized", lambda conn=None: True)
        assert client.get("/api/status/Nobody").status_code == 404

    def test_before_the_first_sync_it_is_a_503_not_no_sites(self, client, monkeypatch):
        monkeypatch.setattr(alerts, "get_alert_board_data", lambda *a, **k: {"alerts": []})
        monkeypatch.setattr(inventory, "snapshot_initialized", lambda conn=None: False)
        assert client.get("/api/status/Acme%20Corp").status_code == 503

    def test_the_page_never_holds_the_tenant(self, client, board, frontend_build):
        # The page is the Nuxt build's (#463): the tenant comes from the URL in
        # the browser, never written into the page, so it can't inject markup.
        page = client.get("/status/%3Cscript%3Ex")
        assert page.status_code == 200 and b"script%3Ex" not in page.data and b"<script>x" not in page.data

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
