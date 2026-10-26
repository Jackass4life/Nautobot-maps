"""A tenant's status page (#291): its sites only, with a status token or as the board."""

import json

import pytest

from nautobot_maps import __main__ as cli
from nautobot_maps import alerts, db, inventory, nautobot, settings, status, statuslinks
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
            "latitude": 51.5054,
            "longitude": -0.0235,
            "down_devices": [{"device_name": "lon-core-01", "primary_ip": "10.0.0.1"}],
            "network_device_count": 12,
            "access_point_count": 40,
            "down_network_device_count": 3,
            "down_access_point_count": 1,
            "active_cases": [{"case_number": "INC-1"}],
            "tenant_descriptions": {"Acme Corp": "Retail bank, 24/7", "Nordnet": "Nordnet's own note"},
        },
        {"id": "loc-par", "name": "Paris PoP", "tenant": "acme corp", "alert_level": "ok", "network_device_count": 2},
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


def link(tenant: str) -> str:
    """*tenant*'s status page link id (#500)."""
    conn = db.get_conn()
    try:
        return statuslinks.ensure(conn, [tenant])[tenant]
    finally:
        conn.close()


@pytest.fixture
def board(monkeypatch):
    monkeypatch.setattr(alerts, "get_alert_board_data", lambda *a, **k: BOARD)


class TestTenantStatus:
    def test_only_the_tenants_sites_and_nothing_internal(self):
        result = status.tenant_status(BOARD, "ACME CORP")
        assert result["tenant"] == "Acme Corp"
        assert result["description"] == "Retail bank, 24/7"
        assert result["summary"] == {"sites": 5, "affected": 2, "devices_down": 4}
        # Whole records: a field added later must be added here on purpose.
        none = {
            "network_devices": 0,
            "access_points": 0,
            "network_devices_down": 0,
            "access_points_down": 0,
            "circuits": [],
        }
        nowhere = {"address": None, "latitude": None, "longitude": None, **none}
        london = {
            "address": "1 Canada Square",
            "latitude": 51.5054,
            "longitude": -0.0235,
            # How many of each kind, and down: counts only (#503).
            "network_devices": 12,
            "access_points": 40,
            "network_devices_down": 3,
            "access_points_down": 1,
            "circuits": [],
        }
        paris = {**nowhere, "network_devices": 2}
        assert result["sites"] == [
            {"name": "London HQ", "state": "outage", "since": "2026-10-09T11:00:00Z", "until": None, **london},
            {"name": "Copenhagen", "state": "degraded", "since": None, "until": None, **nowhere},
            {"name": "Amsterdam", "state": "maintenance", "since": None, "until": "2026-10-09T14:00:00Z", **nowhere},
            {"name": "Stockholm", "state": "unknown", "since": None, "until": None, **nowhere},
            {"name": "Paris PoP", "state": "operational", "since": None, "until": None, **paris},
        ]
        text = json.dumps(result)
        for secret in (
            "10.0.0.1",
            "lon-core-01",
            "INC-1",
            "Swap core",
            "olga",
            "Nordnet",
            "Oslo",
            "own note",
        ):
            assert secret not in text

    def test_where_each_site_is(self):
        """#493: the address, else the coordinates; the page links one of them."""
        board = {
            "checked_at": BOARD["checked_at"],
            "alerts": [
                BOARD["alerts"][0],
                {
                    "name": "Field cabinet",
                    "tenant": "Acme Corp",
                    "alert_level": "ok",
                    "latitude": 55.6761,
                    "longitude": 12.5683,
                },
                {
                    "name": "Nowhere",
                    "tenant": "Acme Corp",
                    "alert_level": "ok",
                    "physical_address": "  ",
                    "latitude": 55.1,
                },
                # The board's address rule: the facility when there's no street address.
                {"name": "Colo", "tenant": "Acme Corp", "alert_level": "ok", "facility": "Equinix CPH1"},
                # Nautobot's textarea: one line here.
                {
                    "name": "Two lines",
                    "tenant": "Acme Corp",
                    "alert_level": "ok",
                    "physical_address": "Borgergade 10\r\n 1300 Copenhagen\n",
                },
                # An impossible point is no point (coordinates.py flags it).
                {"name": "Typo", "tenant": "Acme Corp", "alert_level": "ok", "latitude": 555.0, "longitude": 12.5},
            ],
        }
        places = {
            site["name"]: (site["address"], site["latitude"], site["longitude"])
            for site in status.tenant_status(board, "Acme Corp")["sites"]
        }
        assert places == {
            "London HQ": ("1 Canada Square", 51.5054, -0.0235),
            "Field cabinet": (None, 55.6761, 12.5683),
            "Nowhere": (None, None, None),
            "Colo": ("Equinix CPH1", None, None),
            "Two lines": ("Borgergade 10, 1300 Copenhagen", None, None),
            "Typo": (None, None, None),
        }

    def test_the_headline_counts_only_the_affected_sites_down(self):
        """What the rows add up to: a site in maintenance or with no data says no
        "down", so its count isn't in the headline either (#503 review)."""
        down = {"device_count": 5, "network_device_count": 5, "down_network_device_count": 2}
        board = {
            "alerts": [
                {"name": "Hit", "tenant": "Acme", "alert_level": "critical", **down},
                {"name": "Work", "tenant": "Acme", "alert_level": "maintenance", **down},
                {"name": "Dark", "tenant": "Acme", "alert_level": "no_data", **down},
            ]
        }
        assert status.tenant_status(board, "Acme")["summary"] == {"sites": 3, "affected": 1, "devices_down": 2}

    def test_a_related_tenant_sees_the_shared_site(self):
        result = status.tenant_status(BOARD, "Nordnet")
        assert [s["name"] for s in result["sites"]] == ["London HQ"]
        # Its own description, not the site's owner's (#482).
        assert result["description"] == "Nordnet's own note"

    def test_the_description_whatever_the_spelling(self):
        row = {**BOARD["alerts"][1], "tenant_descriptions": {"ACME Corp": "Retail bank"}}
        board = {"checked_at": BOARD["checked_at"], "alerts": [row]}
        assert status.tenant_status(board, "Acme Corp")["description"] == "Retail bank"

    def test_no_description(self):
        board = {"checked_at": BOARD["checked_at"], "alerts": [BOARD["alerts"][1]]}
        assert status.tenant_status(board, "Acme Corp")["description"] is None

    def test_unknown_tenant(self):
        assert status.tenant_status(BOARD, "Nobody") is None


LONDON = "1 Canada Square, Canary Wharf, London E14 5AB, UK"


class TestCircuits:
    """Each site's circuits, for the customer (#511): theirs, active, no provider."""

    def circuit(self, cid, **fields):
        return {
            "circuit_id": cid.lower(),
            "cid": cid,
            "provider": "Telia Carrier",
            "circuit_type": "Internet Transit",
            "status": "Active",
            "tenant": "Acme Corp",
            "commit_rate": 1_000_000,
            "description": "",
            "address_key": "",
            "terminated": True,
            "location_ids": [],
            **fields,
        }

    def test_at_the_site_or_its_address(self):
        circuits = [
            self.circuit("TEL-1", location_ids=["loc-lon", "loc-par"]),
            self.circuit("TEL-2", circuit_type="", commit_rate=None, location_ids=["loc-par"]),
            # No termination; its description is London's address.
            self.circuit("GTT-3", terminated=False, address_key=inventory.address_key(LONDON), commit_rate=500_000),
            # Terminated elsewhere: never matched by address.
            self.circuit("TEL-4", address_key=inventory.address_key(LONDON), location_ids=["loc-osl"]),
        ]
        assert inventory.address_key(LONDON)
        board = {"alerts": [{**BOARD["alerts"][0], "physical_address": LONDON}, BOARD["alerts"][1]]}
        sites = {site["name"]: site["circuits"] for site in status.tenant_status(board, "Acme Corp", circuits)["sites"]}
        assert sites["Paris PoP"] == [
            {"cid": "TEL-1", "type": "Internet Transit", "bandwidth": "1 Gbps"},
            {"cid": "TEL-2", "type": "", "bandwidth": ""},
        ]
        assert [circuit["cid"] for circuit in sites["London HQ"]] == ["TEL-1", "GTT-3"]
        assert "Telia" not in json.dumps(sites), "never the provider"

    def test_only_the_tenants_active_circuits(self, pg_database):
        rows = [
            self.circuit("MINE", tenant=" acme corp "),
            self.circuit("PLANNED", status="Planned"),
            self.circuit("OFFLINE", status="Offline"),
            self.circuit("THEIRS", tenant="Nordnet"),
            self.circuit("UPLINK", tenant=""),
        ]
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                inventory.write_circuits(conn, rows)
            assert [c["cid"] for c in inventory.read_tenant_circuits(conn, "ACME CORP")] == ["MINE"]
        finally:
            conn.close()

    def test_on_the_page(self, client, board, monkeypatch):
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                inventory.write_circuits(conn, [self.circuit("TEL-1", location_ids=["loc-par"])])
        finally:
            conn.close()
        sites = client.get(f"/api/status/{link('Acme Corp')}").get_json()["sites"]
        assert next(s for s in sites if s["name"] == "Paris PoP")["circuits"] == [
            {"cid": "TEL-1", "type": "Internet Transit", "bandwidth": "1 Gbps"}
        ]

    def test_without_circuits_the_page_still_answers(self, client, board, monkeypatch):
        monkeypatch.setattr(inventory, "read_tenant_circuits", lambda conn, tenant: 1 / 0)
        sites = client.get(f"/api/status/{link('Acme Corp')}").get_json()["sites"]
        assert all(site["circuits"] == [] for site in sites)


class TestTenantScopes:
    def test_scoped_level_and_since_use_only_the_matching_roles(self):
        checked_at = "2026-10-09T12:00:00Z"
        devices = [
            {"role": "Firewall", "status": "Offline", "first_seen_down_at": "2026-10-09T11:00:00Z"},
            {"role": "Firewall", "status": "Active"},
            {"role": "Core Router", "status": "Offline", "first_seen_down_at": "2026-10-09T10:00:00Z"},
            {"role": "Access Point", "status": "Active"},
        ]
        assert alerts.compute_alert_level(devices)["level"] == "critical"
        scoped = alerts.tenant_scoped_alerts(
            {"acme corp": ["firewall", "access point"]},
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
                "device_count": 3,
                "current_downtime_seconds": 3600,
                # Its own devices, not the core router (#503).
                "access_point_count": 1,
                "network_device_count": 2,
                "down_access_point_count": 0,
                "down_network_device_count": 1,
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
                    # The whole site's: not what this tenant sees.
                    "network_device_count": 30,
                    "down_network_device_count": 2,
                    "tenant_alerts": scoped,
                }
            ],
        }
        result = status.tenant_status(board, "ACME CORP")
        assert result["sites"] == [
            {
                "name": "Scoped site",
                "state": "degraded",
                "since": "2026-10-09T11:00:00Z",
                "until": None,
                "address": None,
                "latitude": None,
                "longitude": None,
                "network_devices": 2,
                "access_points": 1,
                "network_devices_down": 1,
                "access_points_down": 0,
                "circuits": [],
            }
        ]
        assert result["summary"]["devices_down"] == 1
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
        # Its offline firewall: counted, but in the site window not down (#503 review).
        assert maintenance["acme corp"]["network_device_count"] == 1
        assert maintenance["acme corp"]["down_network_device_count"] == 0

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
        assert scoped["acme corp"] == {
            "alert_level": "maintenance",
            "device_count": 1,
            "current_downtime_seconds": 0,
            # Counted, but in maintenance it isn't down (#503).
            "access_point_count": 0,
            "network_device_count": 1,
            "down_access_point_count": 0,
            "down_network_device_count": 0,
        }
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
        acme, other = link("Acme Corp"), link("Other Co")
        assert client.get(f"/api/status/{acme}", headers=bearer(token)).status_code == 200
        assert client.get(f"/api/status/{other}", headers=bearer(token)).status_code == 403
        # A link that opens nothing looks the same as another tenant's (#500).
        assert client.get(f"/api/status/{'x' * 24}", headers=bearer(token)).status_code == 403
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
        assert client.get(f"/api/status/{link('Acme Corp')}", headers=bearer(created["token"])).status_code == 401

    def test_with_require_viewer_the_page_loads_but_the_data_needs_access(
        self, client, board, monkeypatch, frontend_build
    ):
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
        token = make("acme-status", "status", tenant="Acme Corp")["token"]
        viewer = make("staff", "viewer")["token"]
        anonymous = {"REMOTE_ADDR": "203.0.113.9"}
        acme = link("Acme Corp")
        with auth_config(mode="header"):
            page = client.get(f"/status/{acme}", environ_base=anonymous)
            assert page.status_code == 200 and b'id="__nuxt"' in page.data
            assert page.headers["Referrer-Policy"] == "no-referrer"
            # The Nuxt build's files (#463): code only, no data.
            assert client.get("/_nuxt/entry.abc123.js", environ_base=anonymous).status_code == 200
            assert client.get("/static/css/other.css", environ_base=anonymous).status_code == 401
            assert client.get(f"/api/status/{acme}", environ_base=anonymous).status_code == 401
            # No sign-in, no clue whether a link exists (#500).
            assert client.get(f"/api/status/{'x' * 24}", environ_base=anonymous).status_code == 401
            assert client.get(f"/api/status/{acme}", headers=bearer(token)).status_code == 200
            assert client.get(f"/api/status/{link('Other Co')}", headers=bearer(viewer)).status_code == 200

    def test_open_like_the_board_without_require_viewer(self, client, board, monkeypatch):
        response = client.get(f"/api/status/{link('Acme Corp')}")
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        monkeypatch.setattr(inventory, "snapshot_initialized", lambda conn=None: True)
        assert client.get(f"/api/status/{link('Nobody')}").status_code == 404

    def test_before_the_first_sync_it_is_a_503_not_no_sites(self, client, monkeypatch):
        monkeypatch.setattr(alerts, "get_alert_board_data", lambda *a, **k: {"alerts": []})
        monkeypatch.setattr(inventory, "snapshot_initialized", lambda conn=None: False)
        assert client.get(f"/api/status/{link('Acme Corp')}").status_code == 503

    def test_the_page_never_holds_the_link(self, client, board, frontend_build):
        # The page is the Nuxt build's (#463): the link comes from the URL in
        # the browser, never written into the page, so it can't inject markup.
        page = client.get("/status/%3Cscript%3Ex")
        assert page.status_code == 200 and b"script%3Ex" not in page.data and b"<script>x" not in page.data

    def test_board_errors_are_a_503(self, client, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("db at postgresql://secret")

        monkeypatch.setattr(alerts, "get_alert_board_data", broken)
        response = client.get(f"/api/status/{link('Acme Corp')}")
        assert response.status_code == 503 and b"secret" not in response.data


class TestLinks:
    """A random link per tenant, not its name (#500)."""

    def test_a_name_no_longer_opens_a_page(self, client, board, monkeypatch):
        monkeypatch.setattr(inventory, "snapshot_initialized", lambda conn=None: True)
        acme = link("Acme Corp")
        assert statuslinks.ID_PATTERN.match(acme) and "acme" not in acme.lower()
        assert client.get(f"/api/status/{acme}").get_json()["tenant"] == "Acme Corp"
        # The old links, by name: the same answer as a tenant without sites.
        for old in ("Acme%20Corp", "acme%20corp", "Other%20Co", acme.upper() if acme.upper() != acme else acme[:-1]):
            response = client.get(f"/api/status/{old}")
            assert response.status_code == 404 and response.get_json() == {"error": "No sites for this tenant"}, old

    def test_one_link_per_tenant_capitals_aside_and_kept(self, client):
        conn = db.get_conn()
        try:
            first = statuslinks.ensure(conn, ["Acme Corp", "ACME CORP", "Other Co", "", "  "])
            assert set(first) == {"Acme Corp", "ACME CORP", "Other Co"}
            assert first["Acme Corp"] == first["ACME CORP"] != first["Other Co"]
            assert statuslinks.ensure(conn, ["acme corp"]) == {"acme corp": first["Acme Corp"]}
            assert statuslinks.tenant_for(conn, first["Other Co"]) == "other co"
            assert statuslinks.tenant_for(conn, "Other Co") is None
            # 300 tenants at once: two queries, every one its own link.
            many = statuslinks.ensure(conn, [f"Tenant {i}" for i in range(300)])
            assert len(set(many.values())) == 300
        finally:
            conn.close()

    def test_a_leaked_link_is_replaced(self, client, board, capsys):
        old = link("Acme Corp")
        assert cli.main(["status-link", "Acme Corp"]) == 0
        assert capsys.readouterr().out.strip() == f"/status/{old}"
        assert cli.main(["status-link", "acme corp", "--new"]) == 0
        new = capsys.readouterr().out.strip().removeprefix("/status/")
        assert new != old and client.get(f"/api/status/{new}").status_code == 200
        assert client.get(f"/api/status/{old}").status_code == 404
        assert cli.main(["status-link", " "]) == 1

    def test_without_the_database_it_says_so_and_still_asks_who_you_are(self, client, board, monkeypatch):
        acme = link("Acme Corp")
        monkeypatch.setattr(db, "get_conn", lambda *a, **k: None)
        response = client.get(f"/api/status/{acme}")
        assert response.status_code == 503 and "PostgreSQL" in response.get_json()["error"]
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
        with auth_config(mode="header"):
            # Database trouble is no clue to one who may not look (#500).
            assert client.get(f"/api/status/{acme}", environ_base={"REMOTE_ADDR": "203.0.113.9"}).status_code == 401

    def test_a_typo_makes_no_link(self, client, capsys):
        conn = db.get_conn()
        try:
            conn.execute(
                "INSERT INTO nautobot_tenant_cache (tenant_id, name, description) VALUES ('t1', 'Acme Corp', '')"
            )
        finally:
            conn.close()
        assert cli.main(["status-link", "Acme Crop", "--new"]) == 1
        assert "no tenant 'Acme Crop'" in capsys.readouterr().err
        assert cli.main(["status-link", "ACME CORP"]) == 0
        assert capsys.readouterr().out.startswith("/status/")
        # Known by its link too: a tenant gone from Nautobot can still be rotated.
        link("Gone Co")
        assert cli.main(["status-link", "Gone Co", "--new"]) == 0

    def test_links_are_read_not_written_once_made(self, client):
        conn = db.get_conn()
        try:
            first = statuslinks.ensure(conn, [f"Tenant {i}" for i in range(300)])
            writes = []
            real = conn.execute

            class Watched:
                def execute(self, query, *args):
                    writes.extend(["insert"] if "INSERT" in query else [])
                    return real(query, *args)

            assert statuslinks.ensure(Watched(), list(first)) == first and writes == []
        finally:
            conn.close()

    def test_a_status_token_is_shown_even_when_its_link_fails(self, client, monkeypatch):
        admin = make("admin", "admin")["token"]

        def broken(*args, **kwargs):
            raise RuntimeError("statement timeout")

        monkeypatch.setattr(statuslinks, "ensure", broken)
        body = {"name": "acme-api", "role": "status", "tenant": "Acme Corp"}
        response = client.post("/api/tokens", json=body, headers=bearer(admin))
        assert response.status_code == 201
        created = response.get_json()["token"]
        assert created["token"].startswith("nmt_") and created["status_link"] is None

    def test_a_status_token_comes_with_its_link(self, client, capsys):
        assert cli.main(["token", "create", "acme-cli", "--role", "status", "--tenant", "Acme Corp"]) == 0
        out, err = capsys.readouterr()
        assert f"Share: /status/{link('Acme Corp')}#key={out.strip()}" in err
        admin = make("admin", "admin")["token"]
        body = {"name": "acme-api", "role": "status", "tenant": "Acme Corp"}
        created = client.post("/api/tokens", json=body, headers=bearer(admin)).get_json()["token"]
        assert created["status_link"] == f"/status/{link('Acme Corp')}"
        viewer = client.post("/api/tokens", json={"name": "v", "role": "viewer"}, headers=bearer(admin)).get_json()
        assert "status_link" not in viewer["token"]


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
