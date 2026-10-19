"""A location's country: Nautobot's field, a Country location above it, or the
address's last part only when it can be a country, never the town (#386)."""

import pytest

from nautobot_maps import inventory, nautobot, settings


@pytest.mark.parametrize(
    ("address", "country"),
    [
        ("Vermlandsgade 51, 2300 Copenhagen, Denmark", "Denmark"),
        ("1 Canada Square, Canary Wharf, London E14 5AB, UK", "UK"),
        ("Stureplan 4, 114 35 Stockholm, Sweden", "Sweden"),
        ("Roersvej 33\n5000 Odense C\nDenmark", "Denmark"),
        ("Copenhagen, Denmark", "Denmark"),
        ("2300 Copenhagen, Denmark", "Denmark"),
        # The town, not a country: none.
        ("Roersvej 33, 5000 Odense C", ""),
        ("Roersvej 33, st. th., 5000 Odense C", ""),
        ("Roersvej 33, London E14 5AB", ""),
        ("Roersvej 33, Odense", ""),  # a street and number, then the town
        ("Roersvej 33, 5000, Odense C", ""),  # a postcode on its own, then the town
        ("Stureplan 4, 114 35, Stockholm", ""),
        ("Denmark", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_address_country(address, country):
    assert inventory.address_country(address) == country


TREE = {
    "emea": {"parent_id": "", "location_type": "Region", "name": "EMEA"},
    "dk": {"parent_id": "emea", "location_type": "country", "name": "Denmark"},
    "odense": {"parent_id": "dk", "location_type": "Site", "name": "Odense"},
    "floor": {"parent_id": "odense", "location_type": "Floor", "name": "Floor 2"},
    "orphan": {"parent_id": "gone", "location_type": "Site", "name": "Orphan"},
    "loop-a": {"parent_id": "loop-b", "location_type": "Site", "name": "A"},
    "loop-b": {"parent_id": "loop-a", "location_type": "Site", "name": "B"},
}


@pytest.mark.parametrize(
    ("location_id", "country"),
    [("floor", "Denmark"), ("odense", "Denmark"), ("dk", "Denmark"), ("emea", ""), ("orphan", ""), ("loop-a", "")],
)
def test_hierarchy_country(location_id, country):
    assert inventory.hierarchy_country(location_id, TREE) == country


def test_the_country_type_is_a_setting(monkeypatch):
    tree = {**TREE, "dk": {**TREE["dk"], "location_type": "Land"}}
    assert inventory.hierarchy_country("odense", tree) == ""
    monkeypatch.setattr(settings, "COUNTRY_LOCATION_TYPE", "land")
    assert inventory.hierarchy_country("odense", tree) == "Denmark"
    assert inventory.cache_version().endswith(",land"), "changing it makes the next sync a full one"


def test_tree_change_only_for_a_location_others_are_under():
    stored = {key: dict(node) for key, node in TREE.items()}
    same = [{"id": "dk", "parent_id": "emea", "location_type": "country", "name": "Denmark"}]
    assert not inventory.tree_change(stored, same)
    assert inventory.tree_change(stored, [{**same[0], "name": "DNK"}])  # renamed
    assert inventory.tree_change(stored, [{**same[0], "location_type": "Region"}])  # retyped
    assert inventory.tree_change(stored, [{**same[0], "parent_id": ""}])  # moved
    # A site with nothing under it, or a new location: no.
    assert not inventory.tree_change(
        stored, [{"id": "floor", "parent_id": "odense", "location_type": "Floor", "name": "F3"}]
    )
    assert not inventory.tree_change(stored, [{"id": "new", "parent_id": "dk", "location_type": "Site", "name": "New"}])


def _location(location_id, name, location_type, parent_id="", address="", **extra):
    return {
        "id": location_id,
        "name": name,
        "location_type": {"id": f"lt-{location_type}", "name": location_type},
        "parent": {"id": parent_id} if parent_id else None,
        "physical_address": address,
        "latitude": "55.4",
        "longitude": "10.4",
        **extra,
    }


DENMARK = _location("dk", "Denmark", "Country")
ODENSE = _location("odense", "Odense", "Site", "dk", "Roersvej 33, 5000 Odense C")


@pytest.fixture
def no_lookups(monkeypatch):
    monkeypatch.setattr(nautobot, "fetch_all_pages", lambda *args, **kwargs: [])


def _countries(raw, **kwargs):
    return {loc["id"]: loc["country"] for loc in inventory.normalize_locations(raw, True, **kwargs)}


def test_a_country_location_above_wins_over_the_town(no_lookups):
    assert _countries([DENMARK, ODENSE]) == {"dk": "Denmark", "odense": "Denmark"}


def test_without_one_the_town_is_not_a_country(no_lookups):
    assert _countries([{**ODENSE, "parent": None}]) == {"odense": ""}


def test_nautobots_own_country_field_wins(no_lookups):
    assert _countries([DENMARK, {**ODENSE, "country": {"name": "Danmark"}}])["odense"] == "Danmark"


def test_an_incremental_sync_finds_it_in_the_stored_tree(no_lookups):
    stored = {"dk": {"parent_id": "", "location_type": "Country", "name": "Denmark"}}
    assert _countries([ODENSE], existing_location_tree=stored) == {"odense": "Denmark"}


def test_through_full_and_incremental_syncs(pg_database, client, monkeypatch):
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    calls = []

    def fetch(endpoint, params=None, **kwargs):
        if endpoint != "dcim/locations/":
            return []
        calls.append(dict(params or {}))
        # An incremental sync brings only what changed: Odense, not Denmark.
        return [ODENSE] if (params or {}).get("last_updated__gte") else [DENMARK, ODENSE]

    monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
    inventory.sync_nautobot(force=True)
    inventory.sync_nautobot()
    assert "last_updated__gte" in calls[-1], "the second sync was incremental"
    by_id = {loc["id"]: loc for loc in inventory.read_locations(include_without_coordinates=True)}
    assert by_id["odense"]["country"] == "Denmark"


def test_a_renamed_country_reaches_its_sites_at_the_next_sync(pg_database, client, monkeypatch):
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    country = dict(DENMARK)
    calls = []

    def fetch(endpoint, params=None, **kwargs):
        if endpoint != "dcim/locations/":
            return []
        calls.append(dict(params or {}))
        return [country] if (params or {}).get("last_updated__gte") else [country, ODENSE]

    def countries():
        return {loc["id"]: loc["country"] for loc in inventory.read_locations(include_without_coordinates=True)}

    monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
    inventory.sync_nautobot(force=True)
    country["name"] = "Danmark"
    inventory.sync_nautobot()  # incremental: brings only the country
    assert "last_updated__gte" in calls[-1]
    assert countries()["dk"] == "Danmark"
    inventory.sync_nautobot()  # so this one is full, and brings Odense too
    assert "last_updated__gte" not in calls[-1]
    assert countries() == {"dk": "Danmark", "odense": "Danmark"}
    inventory.sync_nautobot()  # and then incremental again
    assert "last_updated__gte" in calls[-1]
