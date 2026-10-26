"""Circuits whose description is a site's address, in the site panel (#383)."""

from unittest.mock import MagicMock

import pytest
import requests

from nautobot_maps import alerts, caching, inventory, nautobot, settings


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("Roersvej 33, 5000 Odense C", "roersvej 33|5000"),
        # The town, its spelling, a floor and a country don't count.
        ("Roersvej 33, 5000 Odense", "roersvej 33|5000"),
        ("ROERSVEJ 33,  5000  Odense C, Denmark", "roersvej 33|5000"),
        ("Roersvej 33, st. th., 5000 Odense C", "roersvej 33|5000"),
        ("Roersvej 33 st. tv, 5000 Odense C", "roersvej 33|5000"),
        ("Roersvej 33 2. th, 5000 Odense C", "roersvej 33|5000"),
        ("Roersvej 33A 1 tv, 5000 Odense C", "roersvej 33a|5000"),
        ("Roersvej 33\n5000 Odense C", "roersvej 33|5000"),
        # A letter after the number, with or without a space.
        ("Roersvej 33A, 5000 Odense C", "roersvej 33a|5000"),
        ("Roersvej 33 a, 5000 Odense C", "roersvej 33a|5000"),
        ("Skt. Knuds Gade 2, 5000 Odense", "skt knuds gade 2|5000"),
        ("Stureplan 4, 114 35 Stockholm", "stureplan 4|11435"),
        ("Stureplan 4, 11435 Stockholm", "stureplan 4|11435"),
        # Not in that form: only the same words match.
        ("Fibre 1G – Roersvej 33, 5000 Odense C", "text:fibre 1g – roersvej 33 5000 odense c"),
        ("1 Canada Square, London E14 5AB", "text:1 canada square london e14 5ab"),
        ("Roersvej 33-35, 5000 Odense", "text:roersvej 33-35 5000 odense"),
        # Too short to be sure: never matched.
        ("Roersvej 33", ""),
        ("Hovedgaden 1", ""),
        ("Odense", ""),
        ("Fibre to Odense centre", ""),  # no number
        ("", ""),
        (" , \n", ""),
        (None, ""),
    ],
)
def test_address_key(text, key):
    assert inventory.address_key(text) == key


def test_different_numbers_or_postcodes_do_not_match():
    key = inventory.address_key("Roersvej 33, 5000 Odense C")
    assert inventory.address_key("Roersvej 35, 5000 Odense C") != key
    assert inventory.address_key("Roersvej 33, 5200 Odense V") != key
    assert inventory.address_key("Roervej 33, 5000 Odense C") != key


def _circuit(circuit_id, cid, description, **extra):
    return {
        "id": circuit_id,
        "cid": cid,
        "provider": {"name": "Telia"},
        "circuit_type": {"name": "MPLS"},
        "status": {"label": "Active"},
        "tenant": {"name": "Acme Corp"},
        "commit_rate": 1_000_000,
        "description": description,
        **extra,
    }


class TestFetch:
    def test_reads_every_circuit_fresh_with_its_key(self, monkeypatch):
        calls = []

        def fetch(endpoint, params=None, **kwargs):
            calls.append((endpoint, params, kwargs.get("use_cache", True)))
            return [
                _circuit("c1", "TDC-1", " Roersvej 33, 5000 Odense C "),
                _circuit("c2", "", "", display="Unnamed 2", commit_rate=True),
                {"cid": "no id"},
            ]

        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        first, second = inventory.fetch_circuits()
        assert calls == [("circuits/circuits/", {"depth": 1}, False)]
        assert first == {
            "circuit_id": "c1",
            "cid": "TDC-1",
            "provider": "Telia",
            "circuit_type": "MPLS",
            "status": "Active",
            "tenant": "Acme Corp",
            "commit_rate": 1_000_000,
            "description": "Roersvej 33, 5000 Odense C",
            "address_key": "roersvej 33|5000",
            "terminated": False,
            "location_ids": [],
        }
        assert second["cid"] == "Unnamed 2"
        assert second["commit_rate"] is None  # not a number
        assert second["address_key"] == ""

    def test_terminated_at_a_location_on_either_side(self, monkeypatch):
        at_location = {"id": "t1", "location": {"id": "loc-1"}}
        provider_network = {"id": "t2", "location": None, "provider_network": {"id": "pn"}}
        circuits = [
            _circuit("a", "A", "", circuit_termination_a=at_location),
            _circuit("z", "Z", "", circuit_termination_a=provider_network, circuit_termination_z=at_location),
            _circuit("pn", "PN", "", circuit_termination_a=provider_network),
            _circuit("brief", "B", "", circuit_termination_a="http://nautobot/api/circuits/circuit-terminations/t3/"),
            _circuit("none", "N", "", circuit_termination_a=None),
        ]
        monkeypatch.setattr(nautobot, "fetch_all_pages", lambda *args, **kwargs: circuits)
        fetched = inventory.fetch_circuits()
        assert {c["cid"]: c["terminated"] for c in fetched} == {
            "A": True,
            "Z": True,
            "PN": False,
            "B": False,
            "N": False,
        }
        # Where each ends (#511), for the status page.
        assert {c["cid"]: c["location_ids"] for c in fetched} == {
            "A": ["loc-1"],
            "Z": ["loc-1"],
            "PN": [],
            "B": [],
            "N": [],
        }

    def test_no_circuits_app_is_no_circuits(self, monkeypatch):
        def fetch(endpoint, params=None, **kwargs):
            raise requests.HTTPError(response=MagicMock(status_code=404))

        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        assert inventory.fetch_circuits() == []

    @pytest.mark.parametrize("error", [requests.HTTPError(response=MagicMock(status_code=500)), OSError("down")])
    def test_a_failed_read_is_none(self, monkeypatch, error):
        def fetch(endpoint, params=None, **kwargs):
            raise error

        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        assert inventory.fetch_circuits() is None


def _location(location_id, name, address, parent_id=""):
    return {
        "id": location_id,
        "name": name,
        "latitude": "55.4",
        "longitude": "10.4",
        "location_type": {"id": "lt", "name": "Site"},
        "physical_address": address,
        "parent": {"id": parent_id, "name": parent_id} if parent_id else None,
    }


ODENSE = "Roersvej 33, 5000 Odense C"
LOCATIONS = [
    _location("loc-ode", "Odense", ODENSE),
    # A building of Odense at the same address: not "another site".
    _location("loc-ode-b", "Odense building B", ODENSE, parent_id="loc-ode"),
    _location("loc-ode2", "Odense colo", "Roersvej 33, 5000 Odense"),
    _location("loc-aar", "Aarhus", "Banegårdspladsen 1, 8000 Aarhus C"),
    _location("loc-none", "No address", ""),
    _location("loc-town", "Town only", "Odense"),
]
CIRCUITS = [
    # Terminated at Odense, and its description is the address: listed once.
    _circuit("c-term", "TDC-0001", ODENSE, circuit_termination_a={"location": {"id": "loc-ode"}}),
    # Terminated at Odense's building: never "by address", on Odense or elsewhere.
    _circuit("c-bld", "TDC-0006", ODENSE, circuit_termination_z={"location": {"id": "loc-ode-b"}}),
    _circuit("c-town", "TDC-0007", "Odense"),
    _circuit("c-b", "tdc-0002", "Roersvej 33, 5000 Odense"),
    _circuit("c-a", "GTT-0003", "roersvej 33a, 5000 Odense"),  # 33A: another building
    _circuit("c-aar", "TDC-0004", "Banegårdspladsen 1, 8000 Aarhus"),
    _circuit("c-text", "TDC-0005", f"Fibre 1G – {ODENSE}"),
]


class TestSitePanel:
    @pytest.fixture(autouse=True)
    def _synced(self, client, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        self.circuits = list(CIRCUITS)
        self.circuits_error = None
        self.calls = []

        def fetch(endpoint, params=None, **kwargs):
            self.calls.append(endpoint)
            if endpoint == "dcim/locations/":
                return LOCATIONS
            if endpoint == "circuits/circuits/":
                if self.circuits_error:
                    raise self.circuits_error
                return self.circuits
            if endpoint == "circuits/circuit-terminations/" and params.get("location") == "loc-ode":
                return [{"circuit": {"id": "c-term", "cid": "TDC-0001"}, "term_side": "A"}]
            return []

        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        self.sync()

    def sync(self):
        inventory.sync_nautobot(force=True)
        caching.cache.clear()

    def panel(self, client, location_id):
        response = client.get(f"/api/locations/{location_id}/detail")
        assert response.status_code == 200
        return response.get_json()["circuits"]

    def test_terminated_first_then_by_address(self, client):
        circuits = self.panel(client, "loc-ode")
        assert [(c["cid"], c.get("match", "")) for c in circuits] == [
            ("TDC-0001", ""),  # terminated: listed once, not again by address
            ("tdc-0002", "address"),
        ]
        by_address = circuits[1]
        assert by_address["provider"] == "Telia"
        assert by_address["commit_rate"] == "1 Gbps"
        assert by_address["description"] == "Roersvej 33, 5000 Odense"
        # Odense colo has the address too; Odense's own building doesn't count.
        assert by_address["shared_with"] == 1

    def test_the_other_site_at_the_address_lists_them_too(self, client):
        circuits = self.panel(client, "loc-ode2")
        # TDC-0001 and TDC-0006 have a termination (at Odense): not guessed here.
        assert [(c["cid"], c["match"], c["shared_with"]) for c in circuits] == [("tdc-0002", "address", 1)]

    def test_a_child_at_the_same_address_does_not_count_its_parent(self, client):
        circuits = self.panel(client, "loc-ode-b")
        assert {c["shared_with"] for c in circuits} == {1}  # Odense colo, not Odense

    def test_only_its_own_address(self, client):
        assert [c["cid"] for c in self.panel(client, "loc-aar")] == ["TDC-0004"]
        assert self.panel(client, "loc-none") == []
        assert self.panel(client, "loc-town") == []  # "Odense" alone is no address

    def test_none_by_address_when_the_terminations_cannot_be_read(self, client, monkeypatch):
        read = nautobot.fetch_all_pages

        def terminations_fail(endpoint, params=None, **kwargs):
            if endpoint == "circuits/circuit-terminations/":
                raise requests.HTTPError(response=MagicMock(status_code=500))
            return read(endpoint, params, **kwargs)

        monkeypatch.setattr(nautobot, "fetch_all_pages", terminations_fail)
        detail = client.get("/api/locations/loc-ode/detail").get_json()
        assert detail["circuits"] == []
        assert detail["circuits_error"] == "Circuit information unavailable"

    def test_a_failed_read_keeps_the_circuits_and_a_later_one_replaces_them(self, client):
        self.circuits_error = requests.HTTPError(response=MagicMock(status_code=500))
        self.sync()
        assert [c["cid"] for c in self.panel(client, "loc-aar")] == ["TDC-0004"]
        self.circuits_error = None
        self.circuits = [_circuit("c-new", "NEW-1", "Banegårdspladsen 1, 8000 Aarhus C")]
        self.sync()
        assert [c["cid"] for c in self.panel(client, "loc-aar")] == ["NEW-1"]

    def test_one_read_per_sync_however_many_sites(self, client):
        assert self.calls.count("circuits/circuits/") == 1
        before = len(self.calls)
        self.panel(client, "loc-ode")
        assert "circuits/circuits/" not in self.calls[before:]  # the panel reads the database


def test_without_a_database_none_by_address(monkeypatch):
    monkeypatch.setattr(alerts.db, "dialect", lambda: None)
    assert alerts.address_circuits("loc-ode", set()) == []
