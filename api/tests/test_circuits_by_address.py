"""Circuits whose description is a site's address, in the site panel (#383)."""

from unittest.mock import MagicMock

import pytest
import requests

from app.clients import nautobot
from app.services import inventory_service as inventory


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
            "tenant_key": "acme corp",
        }
        assert second["cid"] == "Unnamed 2"
        assert second["commit_rate"] is None  # not a number
        assert second["address_key"] == ""

    def test_terminated_at_a_location_on_either_side(self, monkeypatch):
        at_location = {"id": "t1", "location": {"id": "loc-1"}}
        provider_network = {"id": "t2", "location": None, "provider_network": {"id": "pn"}}
        circuits = [
            _circuit("a", "A", "", circuit_termination_a=at_location),
            _circuit(
                "z",
                "Z",
                "",
                circuit_termination_a=provider_network,
                circuit_termination_z=at_location,
            ),
            _circuit("pn", "PN", "", circuit_termination_a=provider_network),
            _circuit(
                "brief",
                "B",
                "",
                circuit_termination_a="http://nautobot/api/circuits/circuit-terminations/t3/",
            ),
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

    @pytest.mark.parametrize(
        "error", [requests.HTTPError(response=MagicMock(status_code=500)), OSError("down")]
    )
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
