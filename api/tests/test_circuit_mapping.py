"""Circuit mapping page (#385): circuits without a termination and their likely site."""

import pytest

from app.clients import caching, nautobot
from app.services import inventory_service as inventory
from app.utils.legacy_settings import settings
from tests.test_circuits_by_address import CIRCUITS, LOCATIONS, _circuit


@pytest.fixture
def synced(client, pg_database, monkeypatch):
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
    circuits = [*CIRCUITS, _circuit("c-formula", "=HYPERLINK(1)", "@Roersvej 33, 5000 Odense")]

    def fetch(endpoint, params=None, **kwargs):
        if endpoint == "dcim/locations/":
            return LOCATIONS
        if endpoint == "circuits/circuits/":
            return circuits
        return []

    monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
    inventory.sync_nautobot(force=True)
    caching.cache.clear()


# Held back until what they need is ported (#587):
#   test_groups_worst_first_with_their_sites: HTTP routes (step 7)
#   test_csv_one_row_per_circuit_formula_safe: HTTP routes (step 7)
#   test_left_out_and_sorted: HTTP routes (step 7)
#   test_a_site_off_the_map_links_through_its_building: HTTP routes (step 7)
#   test_page_and_nav: HTTP routes (step 7)
#   test_without_a_database: HTTP routes (step 7)
#   test_viewers_only_when_required: HTTP routes (step 7)
