"""A tenant's status page (#291): its sites only, with a status token or as the board."""

import json

import pytest

from app import __main__ as cli
from app.clients import nautobot
from app.core.legacy_settings import settings
from app.db import connection as db
from app.db import session as db_session
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from app.services import status_link_service as statuslinks
from app.services import status_service as status
from tests import db as adb
from tests import test_tokens
from tests.test_app import auth_config
from tests.test_tokens import bearer, make, verify

clock = test_tokens.clock

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
            "description": "  Head office, trading floor\n",
            "latitude": 51.5054,
            "longitude": -0.0235,
            "down_devices": [{"device_name": "lon-core-01", "primary_ip": "10.0.0.1"}],
            "network_device_count": 12,
            "access_point_count": 40,
            "down_network_device_count": 3,
            "down_access_point_count": 1,
            "active_cases": [{"case_number": "INC-1"}],
            "tenant_descriptions": {
                "Acme Corp": "Retail bank, 24/7",
                "Nordnet": "Nordnet's own note",
            },
        },
        {
            "id": "loc-par",
            "name": "Paris PoP",
            "tenant": "acme corp",
            "alert_level": "ok",
            "network_device_count": 2,
            "description": "   ",
        },
        {
            "id": "loc-ams",
            "name": "Amsterdam",
            "tenant": "Acme Corp",
            "alert_level": "maintenance",
            "maintenance": {
                "until": "2026-10-09T14:00:00Z",
                "reason": "Swap core router",
                "by": "olga",
            },
        },
        {"id": "loc-osl", "name": "Oslo", "tenant": "Other Co", "alert_level": "critical"},
        {"id": "loc-cph", "name": "Copenhagen", "tenant": "Acme Corp", "alert_level": "low"},
        {
            "id": "loc-sto",
            "name": "Stockholm",
            "tenant": "Acme Corp",
            "alert_level": "no_data",
            "device_count": 2,
        },
        {
            "id": "loc-emea",
            "name": "EMEA",
            "tenant": "Acme Corp",
            "alert_level": "no_data",
            "device_count": 0,
        },
    ],
}


def link(tenant: str) -> str:
    """*tenant*'s status page link id (#500)."""
    return adb.run(statuslinks.ensure, [tenant])[tenant]


@pytest.fixture
def known_tenants(pg_database):
    """The tenants Nautobot has, as of the last sync: a status token needs one (#528)."""
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            adb.call(
                inventory.write_tenants,
                [
                    {"tenant_id": "t-acme", "name": "Acme Corp"},
                    {"tenant_id": "t-other", "name": "Other Co"},
                ],
            )
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
            "devices": [],
            "circuits": [],
        }
        nowhere = {
            "description": None,
            "address": None,
            "latitude": None,
            "longitude": None,
            **none,
        }
        london = {
            "description": "Head office, trading floor",
            "address": "1 Canada Square",
            "latitude": 51.5054,
            "longitude": -0.0235,
            # How many of each kind, and down: counts only (#503).
            "network_devices": 12,
            "access_points": 40,
            "network_devices_down": 3,
            "access_points_down": 1,
            "devices": [],
            "circuits": [],
        }
        paris = {**nowhere, "network_devices": 2}
        assert result["sites"] == [
            {
                "name": "London HQ",
                "state": "outage",
                "since": "2026-10-09T11:00:00Z",
                "until": None,
                **london,
            },
            {"name": "Copenhagen", "state": "degraded", "since": None, "until": None, **nowhere},
            {
                "name": "Amsterdam",
                "state": "maintenance",
                "since": None,
                "until": "2026-10-09T14:00:00Z",
                **nowhere,
            },
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
                {
                    "name": "Colo",
                    "tenant": "Acme Corp",
                    "alert_level": "ok",
                    "facility": "Equinix CPH1",
                },
                # Nautobot's textarea: one line here.
                {
                    "name": "Two lines",
                    "tenant": "Acme Corp",
                    "alert_level": "ok",
                    "physical_address": "Borgergade 10\r\n 1300 Copenhagen\n",
                },
                # An impossible point is no point (coordinates.py flags it).
                {
                    "name": "Typo",
                    "tenant": "Acme Corp",
                    "alert_level": "ok",
                    "latitude": 555.0,
                    "longitude": 12.5,
                },
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
        assert status.tenant_status(board, "Acme")["summary"] == {
            "sites": 3,
            "affected": 1,
            "devices_down": 2,
        }

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
            self.circuit(
                "GTT-3",
                terminated=False,
                address_key=inventory.address_key(LONDON),
                commit_rate=500_000,
            ),
            # Terminated elsewhere: never matched by address.
            self.circuit(
                "TEL-4", address_key=inventory.address_key(LONDON), location_ids=["loc-osl"]
            ),
        ]
        assert inventory.address_key(LONDON)
        board = {"alerts": [{**BOARD["alerts"][0], "physical_address": LONDON}, BOARD["alerts"][1]]}
        sites = {
            site["name"]: site["circuits"]
            for site in status.tenant_status(board, "Acme Corp", circuits)["sites"]
        }
        assert sites["Paris PoP"] == [
            {"cid": "TEL-1", "type": "Internet Transit", "bandwidth": "1 Gbps"},
            {"cid": "TEL-2", "type": "", "bandwidth": ""},
        ]
        assert [circuit["cid"] for circuit in sites["London HQ"]] == ["TEL-1", "GTT-3"]
        assert "Telia" not in json.dumps(sites), "never the provider"

    def test_below_the_site_and_one_address_once(self):
        """A building's circuit is its site's; an address match goes to the
        site, not again to its building at the same address; two sites at one
        address both have it, as on the map (review of #511)."""
        rows = [
            {"id": "loc-lon", "physical_address": LONDON},
            {
                "id": "loc-lon-b",
                "physical_address": LONDON,
            },  # a building of London HQ, also on the board
            {"id": "loc-colo", "physical_address": LONDON},  # another site at the same address
        ]
        parents = {
            "loc-lon-f2": "loc-lon-b",
            "loc-lon-b": "loc-lon",
            "loc-lon": "loc-uk",
            "loc-colo": "loc-uk",
        }
        circuits = [
            self.circuit("FLOOR-2", location_ids=["loc-lon-f2"]),
            self.circuit("BY-ADDRESS", terminated=False, address_key=inventory.address_key(LONDON)),
            self.circuit("NOWHERE", location_ids=["loc-osl"]),
        ]
        placed = {
            row: [c["cid"] for c in found]
            for row, found in status.site_circuits(rows, circuits, parents).items()
        }
        assert placed == {
            "loc-lon-b": ["FLOOR-2"],
            "loc-lon": ["BY-ADDRESS"],
            "loc-colo": ["BY-ADDRESS"],
        }

    def test_at_scale_in_one_pass(self):
        rows = [
            {"id": f"s{n}", "physical_address": f"{n} Harbour Street, 1000 Town"}
            for n in range(1200)
        ]
        circuits = [self.circuit(f"C{n}", location_ids=[f"s{n % 1200}"]) for n in range(1500)]
        placed = status.site_circuits(rows, circuits, {})
        assert sum(len(found) for found in placed.values()) == 1500 and len(placed["s0"]) == 2

    def test_only_the_tenants_active_circuits(self, pg_database):
        rows = [
            self.circuit("MINE", tenant=" acme corp "),
            self.circuit("PLANNED", status="Planned"),
            self.circuit("OFFLINE", status="Offline"),
            self.circuit("THEIRS", tenant="Nordnet"),
            self.circuit("UPLINK", tenant=""),
            # Matched as the board matches tenants (casefold), not as SQL lower() would.
            self.circuit("STRASSE", tenant="Straße AG"),
            # Written in the shape before #511: no location_ids or tenant_key.
            {
                k: v
                for k, v in self.circuit("OLD", tenant="Øresund A/S").items()
                if k != "location_ids"
            },
        ]
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                adb.call(inventory.write_circuits, rows)
            assert [c["cid"] for c in inventory.read_tenant_circuits(conn, "ACME CORP")] == ["MINE"]
            assert [c["cid"] for c in inventory.read_tenant_circuits(conn, "STRASSE AG")] == [
                "STRASSE"
            ]
            assert [c["cid"] for c in inventory.read_tenant_circuits(conn, "øresund a/s")] == [
                "OLD"
            ]
        finally:
            conn.close()

    def test_on_the_page(self, flask_client, board):
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                adb.call(
                    inventory.write_circuits, [self.circuit("TEL-1", location_ids=["loc-par"])]
                )
        finally:
            conn.close()
        sites = flask_client.get(f"/api/v1/status/{link('Acme Corp')}").get_json()["sites"]
        assert next(s for s in sites if s["name"] == "Paris PoP")["circuits"] == [
            {"cid": "TEL-1", "type": "Internet Transit", "bandwidth": "1 Gbps"}
        ]

    def test_a_request_never_holds_two_connections(self, flask_client, board, monkeypatch):
        """The board read takes its own (its sync schedule, with an inventory source): the
        link's is closed first, so busy requests can't use up the pool (#608 review)."""
        acme = link("Acme Corp")
        board_data = alerts.get_alert_board_data

        def board_with_its_own_connection():
            # The link's (the request's session, #643) is given back by now.
            state["link_held"] = db_session.current().in_transaction()
            db.get_conn().close()
            return board_data()

        monkeypatch.setattr(alerts, "get_alert_board_data", board_with_its_own_connection)
        real, state = db.get_conn, {"open": 0, "most": 0, "taken": 0}

        class Counted:
            def __init__(self, conn):
                self._conn = conn

            def __getattr__(self, name):
                return getattr(self._conn, name)

            def close(self):
                state["open"] -= 1
                self._conn.close()

        def counted(*args, **kwargs):
            state["open"] += 1
            state["taken"] += 1
            state["most"] = max(state["most"], state["open"])
            return Counted(real(*args, **kwargs))

        monkeypatch.setattr(db, "get_conn", counted)
        assert flask_client.get(f"/api/v1/status/{acme}").status_code == 200
        assert state["taken"] >= 2 and state["most"] == 1 and state["open"] == 0
        assert state["link_held"] is False

    def test_without_circuits_the_page_still_answers(self, flask_client, board, monkeypatch):
        monkeypatch.setattr(inventory, "read_tenant_circuits", lambda conn, tenant: 1 / 0)
        sites = flask_client.get(f"/api/v1/status/{link('Acme Corp')}").get_json()["sites"]
        assert all(site["circuits"] == [] for site in sites)


class TestTenantScopes:
    def test_scoped_level_and_since_use_only_the_matching_roles(self):
        checked_at = "2026-10-09T12:00:00Z"
        devices = [
            {"role": "Firewall", "status": "Offline", "first_seen_down_at": "2026-10-09T11:00:00Z"},
            {"role": "Firewall", "status": "Active"},
            {
                "role": "Core Router",
                "status": "Offline",
                "first_seen_down_at": "2026-10-09T10:00:00Z",
            },
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
                "description": None,
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
                "devices": [],
                "circuits": [],
            }
        ]
        assert result["summary"]["devices_down"] == 1
        assert "Firewall" not in json.dumps(result)

    def test_no_matching_role_hides_site(self):
        scoped = alerts.tenant_scoped_alerts(
            {"acme corp": ["firewall"]},
            ["Acme Corp"],
            [],
            [],
            [],
            None,
            "2026-10-09T12:00:00Z",
            None,
            {},
            "",
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
                {
                    "id": "lead",
                    "role": "Access Switch",
                    "status": "Active",
                    "vc_id": "vc1",
                    "vc_master": True,
                },
                {
                    "id": "member",
                    "role": "Access Switch",
                    "status": "Offline",
                    "vc_id": "vc1",
                    "vc_position": 2,
                },
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
        board = {
            "alerts": [
                {
                    "name": "Fw work",
                    "tenant": "Acme Corp",
                    "alert_level": "ok",
                    "tenant_alerts": scoped,
                }
            ]
        }
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

        malformed = [
            {**location, "id": f"loc-{index}", "custom_fields": {"tenant_scope": "{bad"}}
            for index in (2, 3)
        ]
        normalized = inventory.normalize_locations(malformed, include_without_coordinates=True)
        assert [item["tenant_scopes"] for item in normalized] == [{}, {}]
        warnings = [
            record
            for record in caplog.records
            if "Invalid tenant_scope custom field" in record.message
        ]
        assert len(warnings) == 1
        assert "2 locations" in warnings[0].message


class TestTenantDevices:
    """STATUS_PAGE_TENANT_DEVICES (#555): the devices whose tenant is the customer, by name."""

    CHECKED_AT = "2026-10-09T12:00:00Z"

    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch):
        monkeypatch.setattr(settings, "STATUS_PAGE_TENANT_DEVICES", True)

    def named(
        self,
        devices=(),
        pending=(),
        maintenance=(),
        window=None,
        scopes=None,
        stacks=None,
        tenants=None,
    ):
        return alerts.tenant_devices(
            scopes or {},
            tenants or ["Acme Corp", "Nordnet"],
            list(devices),
            list(pending),
            list(maintenance),
            window,
            self.CHECKED_AT,
            stacks or {},
        )

    def test_only_each_tenants_own_devices_on_a_shared_site(self):
        devices = [
            {"id": "a", "name": "acme-sw", "tenant": "ACME Corp", "status": "Active"},
            {
                "id": "b",
                "name": "acme-ap",
                "tenant": "Acme Corp",
                "status": "Offline",
                "first_seen_down_at": "x",
            },
            {"id": "n", "name": "nordnet-fw", "tenant": "Nordnet", "status": "Active"},
            {"id": "o", "name": "core-01", "tenant": "", "status": "Offline"},
            {"id": "z", "name": "other-sw", "tenant": "Other Co", "status": "Active"},
        ]
        assert self.named(devices) == {
            "acme corp": [
                {"name": "acme-sw", "state": "up", "since": None},
                {"name": "acme-ap", "state": "down", "since": "x"},
            ],
            "nordnet": [{"name": "nordnet-fw", "state": "up", "since": None}],
        }

    def test_counted_as_the_device_counts(self):
        """Within the delay: up; in a device window: maintenance; a site window: all maintenance."""
        down = {
            "id": "d",
            "name": "down",
            "tenant": "Acme Corp",
            "status": "Offline",
            "first_seen_down_at": "t1",
        }
        pending = {"id": "p", "name": "pending", "tenant": "Acme Corp", "status": "Offline"}
        resting = {"id": "m", "name": "resting", "tenant": "Acme Corp", "status": "Offline"}
        states = {
            d["name"]: d["state"] for d in self.named([down], [pending], [resting])["acme corp"]
        }
        assert states == {"down": "down", "pending": "up", "resting": "maintenance"}
        in_window = self.named([down], window={"until": "later"})["acme corp"]
        assert in_window == [{"name": "down", "state": "maintenance", "since": None}]

    def test_a_stack_once_by_its_name_and_the_tenants_roles(self):
        stack = alerts.merge_stacks(
            [
                {
                    "id": "lead",
                    "name": "sw1-1",
                    "role": "Access Switch",
                    "status": "Active",
                    "tenant": "Acme Corp",
                }
                | {"vc_id": "vc1", "vc_name": "sw1", "vc_master": True},
                {
                    "id": "member",
                    "name": "sw1-2",
                    "role": "Access Switch",
                    "status": "Offline",
                    "tenant": "Acme Corp",
                }
                | {"vc_id": "vc1", "vc_name": "sw1", "vc_position": 2},
            ]
        )
        firewall = {
            "id": "fw",
            "name": "fw1",
            "role": "Firewall",
            "status": "Active",
            "tenant": "Acme Corp",
        }
        named = self.named([*stack, firewall], stacks={"vc1": {"members": [], "names": []}})
        assert named["acme corp"][0] == {"name": "sw1", "state": "down", "since": self.CHECKED_AT}
        assert len(named["acme corp"]) == 2
        # TENANT_SCOPE_FIELD: only the roles the tenant's status page counts.
        scoped = self.named([*stack, firewall], scopes={"acme corp": ["firewall"]})
        assert scoped == {"acme corp": [{"name": "fw1", "state": "up", "since": None}]}

    def test_off_names_nothing(self, monkeypatch):
        device = {"id": "a", "name": "acme-sw", "tenant": "Acme Corp", "status": "Offline"}
        board = {"alerts": [{"name": "Site", "tenant": "Acme Corp", "alert_level": "low"}]}
        board["alerts"][0]["tenant_devices"] = self.named([device])
        assert status.tenant_status(board, "Acme Corp")["sites"][0]["devices"]
        monkeypatch.setattr(settings, "STATUS_PAGE_TENANT_DEVICES", False)
        assert self.named([device]) == {}
        # Also from a board built while it was on.
        assert status.tenant_status(board, "Acme Corp")["sites"][0]["devices"] == []
        assert "acme-sw" not in json.dumps(status.tenant_status(board, "Acme Corp"))

    def test_down_first_then_by_name(self):
        board = {
            "alerts": [
                {
                    "name": "Site",
                    "tenant": "Acme Corp",
                    "alert_level": "low",
                    "tenant_devices": {
                        "acme corp": [
                            {"name": "b-up", "state": "up", "since": None},
                            {"name": "z-down", "state": "down", "since": "t"},
                            {"name": "A-up", "state": "up", "since": None},
                            {"name": "m", "state": "maintenance", "since": None},
                        ],
                        "nordnet": [{"name": "nordnet-fw", "state": "down", "since": "t"}],
                    },
                }
            ]
        }
        devices = status.tenant_status(board, "ACME CORP")["sites"][0]["devices"]
        assert [d["name"] for d in devices] == ["z-down", "m", "A-up", "b-up"]

    def test_from_the_board_build_to_the_page(self, flask_client, pg_database, monkeypatch):
        """Nautobot's device tenant, through the board, to /api/status (and its answer model)."""
        from tests.test_app import _snapshot_device, _snapshot_location

        for name, value in (("NAUTOBOT_URL", ""), ("NAUTOBOT_TOKEN", ""), ("LIBRENMS_URL", "")):
            monkeypatch.setattr(settings, name, value)
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *args, **kwargs: False)
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                adb.call(
                    inventory.write_locations,
                    [{**_snapshot_location("loc-lon", "London HQ"), "tenant": "Acme Corp"}],
                )
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_completed_at=self.CHECKED_AT,
                    last_successful_sync=self.CHECKED_AT,
                    cache_version=inventory.CACHE_VERSION,
                )
                adb.call(
                    inventory.write_devices,
                    [
                        {
                            **_snapshot_device("lon-ap-01", "loc-lon", "Access Point", "Offline"),
                            "tenant": "Acme Corp",
                        },
                        {
                            **_snapshot_device("lon-sw-01", "loc-lon", "Access Switch", "Active"),
                            "tenant": "Acme Corp",
                        },
                        _snapshot_device("lon-core-01", "loc-lon", "Core Router", "Active"),
                    ],
                )
        finally:
            conn.close()
        board = alerts.build_alert_board_payload(snapshot_only=True)
        monkeypatch.setattr(alerts, "get_alert_board_data", lambda *a, **k: board)
        (site,) = flask_client.get(f"/api/v1/status/{link('Acme Corp')}").get_json()["sites"]
        assert [(d["name"], d["state"]) for d in site["devices"]] == [
            ("lon-ap-01", "down"),
            ("lon-sw-01", "up"),
        ]
        assert site["devices"][0]["since"]
        assert "lon-core-01" not in json.dumps(site)


class TestAccess:
    def test_a_status_token_opens_only_its_tenant(self, known_tenants, flask_client, board):
        token = make("acme-status", "status", tenant="Acme Corp")["token"]
        acme, other = link("Acme Corp"), link("Other Co")
        assert flask_client.get(f"/api/v1/status/{acme}", headers=bearer(token)).status_code == 200
        assert flask_client.get(f"/api/v1/status/{other}", headers=bearer(token)).status_code == 403
        # A link that opens nothing looks the same as another tenant's (#500).
        assert (
            flask_client.get(f"/api/v1/status/{'x' * 24}", headers=bearer(token)).status_code == 403
        )
        for path in (
            "/api/v1/alerts",
            "/alerts",
            "/api/v1/maintenance",
            "/api/v1/status-other",
            "/static/css/other.css",
        ):
            assert flask_client.get(path, headers=bearer(token)).status_code == 403, path
        response = flask_client.post("/api/v1/alert-cases", json={}, headers=bearer(token))
        assert response.status_code == 403

    def test_a_revoked_status_token_gets_401(self, known_tenants, flask_client, board):
        from app.services import token_service as tokens

        created = make("acme-status", "status", tenant="Acme Corp")
        adb.run(tokens.revoke, created["id"], "admin")
        assert (
            flask_client.get(
                f"/api/v1/status/{link('Acme Corp')}", headers=bearer(created["token"])
            ).status_code
            == 401
        )

    def test_with_require_viewer_the_page_loads_but_the_data_needs_access(
        self, known_tenants, flask_client, board, monkeypatch, frontend_build
    ):
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
        token = make("acme-status", "status", tenant="Acme Corp")["token"]
        viewer = make("staff", "viewer")["token"]
        anonymous = {"REMOTE_ADDR": "203.0.113.9"}
        acme = link("Acme Corp")
        with auth_config(mode="header"):
            page = flask_client.get(f"/status/{acme}", environ_base=anonymous)
            assert page.status_code == 200 and b'id="__nuxt"' in page.data
            assert page.headers["Referrer-Policy"] == "no-referrer"
            # The Nuxt build's files (#463): code only, no data.
            assert (
                flask_client.get("/_nuxt/entry.abc123.js", environ_base=anonymous).status_code
                == 200
            )
            assert (
                flask_client.get("/static/css/other.css", environ_base=anonymous).status_code == 401
            )
            assert (
                flask_client.get(f"/api/v1/status/{acme}", environ_base=anonymous).status_code
                == 401
            )
            # No sign-in, no clue whether a link exists (#500).
            assert (
                flask_client.get(f"/api/v1/status/{'x' * 24}", environ_base=anonymous).status_code
                == 401
            )
            assert (
                flask_client.get(f"/api/v1/status/{acme}", headers=bearer(token)).status_code == 200
            )
            assert (
                flask_client.get(
                    f"/api/v1/status/{link('Other Co')}", headers=bearer(viewer)
                ).status_code
                == 200
            )

    def test_open_like_the_board_without_require_viewer(self, flask_client, board, monkeypatch):
        response = flask_client.get(f"/api/v1/status/{link('Acme Corp')}")
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        monkeypatch.setattr(inventory, "snapshot_initialized", lambda conn=None: True)
        assert flask_client.get(f"/api/v1/status/{link('Nobody')}").status_code == 404

    def test_before_the_first_sync_it_is_a_503_not_no_sites(self, flask_client, monkeypatch):
        monkeypatch.setattr(alerts, "get_alert_board_data", lambda *a, **k: {"alerts": []})
        monkeypatch.setattr(inventory, "snapshot_initialized", lambda conn=None: False)
        assert flask_client.get(f"/api/v1/status/{link('Acme Corp')}").status_code == 503

    def test_the_page_never_holds_the_link(self, flask_client, board, frontend_build):
        # The page is the Nuxt build's (#463): the link comes from the URL in
        # the browser, never written into the page, so it can't inject markup.
        page = flask_client.get("/status/%3Cscript%3Ex")
        assert (
            page.status_code == 200
            and b"script%3Ex" not in page.data
            and b"<script>x" not in page.data
        )

    def test_board_errors_are_a_503(self, flask_client, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("db at postgresql://secret")

        monkeypatch.setattr(alerts, "get_alert_board_data", broken)
        response = flask_client.get(f"/api/v1/status/{link('Acme Corp')}")
        assert response.status_code == 503 and b"secret" not in response.data


class TestLinks:
    """A random link per tenant, not its name (#500)."""

    def test_a_name_no_longer_opens_a_page(self, flask_client, board, monkeypatch):
        monkeypatch.setattr(inventory, "snapshot_initialized", lambda conn=None: True)
        acme = link("Acme Corp")
        assert statuslinks.ID_PATTERN.match(acme) and "acme" not in acme.lower()
        assert flask_client.get(f"/api/v1/status/{acme}").get_json()["tenant"] == "Acme Corp"
        # The old links, by name: the same answer as a tenant without sites.
        for old in (
            "Acme%20Corp",
            "acme%20corp",
            "Other%20Co",
            acme.upper() if acme.upper() != acme else acme[:-1],
        ):
            response = flask_client.get(f"/api/v1/status/{old}")
            assert response.status_code == 404 and response.get_json() == {
                "error": "No sites for this tenant"
            }, old

    def test_one_link_per_tenant_capitals_aside_and_kept(self, flask_client):
        first = adb.run(statuslinks.ensure, ["Acme Corp", "ACME CORP", "Other Co", "", "  "])
        assert set(first) == {"Acme Corp", "ACME CORP", "Other Co"}
        assert first["Acme Corp"] == first["ACME CORP"] != first["Other Co"]
        assert adb.run(statuslinks.ensure, ["acme corp"]) == {"acme corp": first["Acme Corp"]}
        assert adb.run(statuslinks.tenant_for, first["Other Co"]) == "other co"
        assert adb.run(statuslinks.tenant_for, "Other Co") is None
        # 300 tenants at once: two queries, every one its own link.
        many = adb.run(statuslinks.ensure, [f"Tenant {i}" for i in range(300)])
        assert len(set(many.values())) == 300

    def test_a_leaked_link_is_replaced(self, flask_client, board, capsys):
        old = link("Acme Corp")
        assert cli.main(["status-link", "Acme Corp"]) == 0
        assert capsys.readouterr().out.strip() == f"/status/{old}"
        assert cli.main(["status-link", "acme corp", "--new"]) == 0
        new = capsys.readouterr().out.strip().removeprefix("/status/")
        assert new != old and flask_client.get(f"/api/v1/status/{new}").status_code == 200
        assert flask_client.get(f"/api/v1/status/{old}").status_code == 404
        assert cli.main(["status-link", " "]) == 1

    def test_without_the_database_it_says_so_and_still_asks_who_you_are(
        self, flask_client, board, monkeypatch
    ):
        acme = link("Acme Corp")

        async def unreachable(*args):
            raise OSError("connection refused")

        monkeypatch.setattr(statuslinks, "tenant_for", unreachable)
        response = flask_client.get(f"/api/v1/status/{acme}")
        assert response.status_code == 503 and "unavailable" in response.get_json()["error"]
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
        with auth_config(mode="header"):
            # Database trouble is no clue to one who may not look (#500).
            assert (
                flask_client.get(
                    f"/api/v1/status/{acme}", environ_base={"REMOTE_ADDR": "203.0.113.9"}
                ).status_code
                == 401
            )

    def test_a_typo_makes_no_link(self, flask_client, capsys):
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

    def test_links_are_read_not_written_once_made(self, flask_client):
        first = adb.run(statuslinks.ensure, [f"Tenant {i}" for i in range(300)])
        writes = []

        async def watched(session, tenants):
            real = session.execute

            async def execute(statement, *args, **kwargs):
                writes.extend(["insert"] if "INSERT" in str(statement) else [])
                return await real(statement, *args, **kwargs)

            session.execute = execute
            return await statuslinks.ensure(session, tenants)

        assert adb.run(watched, list(first)) == first and writes == []

    def test_a_status_token_is_shown_even_when_its_link_fails(
        self, known_tenants, flask_client, monkeypatch
    ):
        admin = make("admin", "admin")["token"]

        def broken(*args, **kwargs):
            raise RuntimeError("statement timeout")

        monkeypatch.setattr(statuslinks, "ensure", broken)
        body = {"name": "acme-api", "role": "status", "tenant": "Acme Corp"}
        response = flask_client.post("/api/v1/tokens", json=body, headers=bearer(admin))
        assert response.status_code == 201
        created = response.get_json()["token"]
        assert created["token"].startswith("nmt_") and created["status_link"] is None

    def test_a_status_token_comes_with_its_link(self, known_tenants, flask_client, capsys):
        assert (
            cli.main(["token", "create", "acme-cli", "--role", "status", "--tenant", "Acme Corp"])
            == 0
        )
        out, err = capsys.readouterr()
        assert f"Share: /status/{link('Acme Corp')}#key={out.strip()}" in err
        admin = make("admin", "admin")["token"]
        body = {"name": "acme-api", "role": "status", "tenant": "Acme Corp"}
        created = flask_client.post("/api/v1/tokens", json=body, headers=bearer(admin)).get_json()[
            "token"
        ]
        assert created["status_link"] == f"/status/{link('Acme Corp')}"
        viewer = flask_client.post(
            "/api/v1/tokens", json={"name": "v", "role": "viewer"}, headers=bearer(admin)
        ).get_json()
        # The new stack answers it as null (#592).
        assert viewer["token"]["status_link"] is None


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
        from app.services import token_service as tokens

        with pytest.raises(tokens.TokenError, match=message):
            make("t", **body)

    def test_a_tenant_nautobot_does_not_have_is_refused(self, flask_client, known_tenants, capsys):
        """#528: a typo makes no token and no link, on the command line and the API."""

        assert cli.main(["token", "create", "acme", "--role", "status", "--tenant", "Acme"]) == 1
        assert "no tenant 'Acme' in Nautobot (as of the last sync)" in capsys.readouterr().err
        admin = make("admin", "admin")["token"]
        refused = flask_client.post(
            "/api/v1/tokens",
            json={"name": "acme", "role": "status", "tenant": "Acme"},
            headers=bearer(admin),
        )
        assert refused.status_code == 400
        assert refused.get_json() == {"error": "no tenant 'Acme' in Nautobot (as of the last sync)"}
        conn = db.get_conn()
        try:
            assert conn.execute("SELECT count(*) AS n FROM status_links").fetchone()["n"] == 0
            assert not adb.run(statuslinks.known, "Acme")
        finally:
            conn.close()
        # Capitals don't matter; the token keeps Nautobot's spelling.
        created = make("acme", "status", tenant="ACME corp")
        assert (
            created["tenant"] == "Acme Corp" and verify(created["token"])["tenant"] == "Acme Corp"
        )
        assert cli.main(["token", "list", "--tenant", "acme corp"]) == 0
        out = capsys.readouterr().out
        assert "(tenant Acme Corp)" in out and "admin" not in out
        listed = flask_client.get(
            "/api/v1/tokens?tenant=Other%20Co", headers=bearer(admin)
        ).get_json()
        assert listed == {"tokens": [], "inactive": 0}

    def test_verify_and_cli(self, known_tenants, pg_database, clock, capsys):
        from app import __main__ as cli

        assert (
            cli.main(["token", "create", "acme", "--role", "status", "--tenant", " Acme Corp "])
            == 0
        )
        secret = capsys.readouterr().out.strip()
        assert verify(secret) == {"name": "acme", "role": "status", "tenant": "Acme Corp"}
        assert cli.main(["token", "list"]) == 0
        assert "(tenant Acme Corp)" in capsys.readouterr().out
