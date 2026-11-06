"""A tenant's status page (#291): its sites only, with a status token or as the board."""

import json

import pytest

from app import db
from app.clients import nautobot
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from app.services import status_service as status
from app.utils.legacy_settings import settings
from tests import test_tokens

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
    from app.services import status_link_service
    from app.session import SessionLocal

    with SessionLocal() as session:
        link_id = status_link_service.ensure(session, [tenant])[tenant]
        session.commit()
    return link_id


@pytest.fixture
def known_tenants(pg_database):
    """The tenants Nautobot has, as of the last sync: a status token needs one (#528)."""
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            inventory.write_tenants(
                conn,
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
                inventory.write_circuits(conn, rows)
            assert [c["cid"] for c in inventory.read_tenant_circuits(conn, "ACME CORP")] == ["MINE"]
            assert [c["cid"] for c in inventory.read_tenant_circuits(conn, "STRASSE AG")] == [
                "STRASSE"
            ]
            assert [c["cid"] for c in inventory.read_tenant_circuits(conn, "øresund a/s")] == [
                "OLD"
            ]
        finally:
            conn.close()


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


class TestAccess:
    pass  # its tests are held back (the end of the file says why)


class TestLinks:
    """A random link per tenant, not its name (#500)."""


class TestStatusTokens:
    pass  # its tests are held back (the end of the file says why)


# Held back until what they need is ported (#587):
#   TestCircuits.test_on_the_page: HTTP routes (step 7)
#   TestCircuits.test_without_circuits_the_page_still_answers: HTTP routes (step 7)
#   TestTenantDevices.test_from_the_board_build_to_the_page: HTTP routes (step 7)
#   TestAccess.test_a_status_token_opens_only_its_tenant: HTTP routes (step 7)
#   TestAccess.test_a_revoked_status_token_gets_401: HTTP routes (step 7)
#   TestAccess.test_with_require_viewer_the_page_loads_but_the_data_needs_access: HTTP routes (step 7)
#   TestAccess.test_open_like_the_board_without_require_viewer: HTTP routes (step 7)
#   TestAccess.test_before_the_first_sync_it_is_a_503_not_no_sites: HTTP routes (step 7)
#   TestAccess.test_the_page_never_holds_the_link: HTTP routes (step 7)
#   TestAccess.test_board_errors_are_a_503: HTTP routes (step 7)
#   TestLinks.test_a_name_no_longer_opens_a_page: HTTP routes (step 7)
#   TestLinks.test_one_link_per_tenant_capitals_aside_and_kept: HTTP routes (step 7)
#   TestLinks.test_a_leaked_link_is_replaced: HTTP routes (step 7)
#   TestLinks.test_without_the_database_it_says_so_and_still_asks_who_you_are: HTTP routes (step 7)
#   TestLinks.test_a_typo_makes_no_link: HTTP routes (step 7)
#   TestLinks.test_links_are_read_not_written_once_made: HTTP routes (step 7)
#   TestLinks.test_a_status_token_is_shown_even_when_its_link_fails: HTTP routes (step 7)
#   TestLinks.test_a_status_token_comes_with_its_link: HTTP routes (step 7)
#   TestStatusTokens.test_validation: the current app's statuslinks/tokens/auth modules (step 7)
#   TestStatusTokens.test_a_tenant_nautobot_does_not_have_is_refused: HTTP routes (step 7)
#   TestStatusTokens.test_verify_and_cli: the CLI (step 9)
