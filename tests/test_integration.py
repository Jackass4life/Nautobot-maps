"""
Integration tests for the Nautobot Maps application.

These tests start a *real* mock-Nautobot HTTP server (werkzeug) in a background
thread, configure the Flask app to point to it, and exercise every API endpoint
end-to-end – no mocking of app internals.

Run with:
    python -m pytest tests/test_integration.py -v
"""
import os
import threading
import pytest
from werkzeug.serving import make_server

import mock_nautobot  # provided via tests/conftest.py path injection
import app as flask_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mock_nautobot_server():
    """
    Start the mock Nautobot HTTP server on a random port.
    Yields the base URL (e.g. 'http://127.0.0.1:54321').
    """
    server = make_server("127.0.0.1", 0, mock_nautobot.app)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture(scope="module")
def integration_client(mock_nautobot_server):
    """
    Return a Flask test client for nautobot-maps, configured to talk to the
    real mock-Nautobot server started in the fixture above.
    """
    # Patch the module-level config variables so all calls go to the mock server
    flask_app.NAUTOBOT_URL = mock_nautobot_server
    flask_app.NAUTOBOT_TOKEN = "demo-token"
    flask_app.cache.clear()

    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "integration-test-secret"

    with flask_app.app.test_client() as client:
        yield client

    # Restore to blank so other test modules don't accidentally hit the server
    flask_app.NAUTOBOT_URL = ""
    flask_app.NAUTOBOT_TOKEN = ""


# ---------------------------------------------------------------------------
# Helper – clear the cache between tests so each test gets a fresh request
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def clear_cache():
    flask_app.cache.clear()
    yield
    flask_app.cache.clear()


# ---------------------------------------------------------------------------
# 1. Map UI
# ---------------------------------------------------------------------------
class TestMapUI:
    def test_index_page_loads(self, integration_client):
        resp = integration_client.get("/")
        assert resp.status_code == 200

    def test_index_contains_leaflet_reference(self, integration_client):
        resp = integration_client.get("/")
        assert b"leaflet" in resp.data.lower()

    def test_index_contains_search_input(self, integration_client):
        resp = integration_client.get("/")
        assert b'id="search-input"' in resp.data

    def test_static_css_served(self, integration_client):
        resp = integration_client.get("/static/css/map.css")
        assert resp.status_code == 200

    def test_static_js_served(self, integration_client):
        resp = integration_client.get("/static/js/map.js")
        assert resp.status_code == 200

    def test_js_contains_hover_to_preview(self, integration_client):
        """The JS bundle includes the hover-to-preview / click-to-lock logic."""
        resp = integration_client.get("/static/js/map.js")
        js = resp.data.decode()
        assert "bindHoverAndLock" in js
        assert "mouseover" in js
        assert "mouseout" in js
        assert "popup-locked" in js

    def test_css_contains_locked_indicator(self, integration_client):
        """The CSS defines a visual indicator for locked popups."""
        resp = integration_client.get("/static/css/map.css")
        css = resp.data.decode()
        assert ".popup-locked" in css


# ---------------------------------------------------------------------------
# 2. /api/locations – all locations with GPS coordinates
# ---------------------------------------------------------------------------
class TestLocationsEndpoint:
    def test_returns_200(self, integration_client):
        resp = integration_client.get("/api/locations")
        assert resp.status_code == 200

    def test_all_seeded_locations_returned(self, integration_client):
        """All 9 mock-Nautobot locations have GPS coords and must be returned."""
        data = integration_client.get("/api/locations").get_json()
        assert data["locations"]
        # The mock server has 9 locations, all with GPS
        assert len(data["locations"]) == 9

    def test_location_has_required_fields(self, integration_client):
        data = integration_client.get("/api/locations").get_json()
        for loc in data["locations"]:
            assert "id" in loc
            assert "name" in loc
            assert "latitude" in loc
            assert "longitude" in loc
            assert "status" in loc
            assert isinstance(loc["latitude"], float)
            assert isinstance(loc["longitude"], float)

    def test_active_location_present(self, integration_client):
        data = integration_client.get("/api/locations").get_json()
        names = [l["name"] for l in data["locations"]]
        assert "Copenhagen DC" in names

    def test_planned_location_present(self, integration_client):
        data = integration_client.get("/api/locations").get_json()
        statuses = {l["name"]: l["status"] for l in data["locations"]}
        assert statuses.get("Oslo Office") == "Planned"

    def test_tenant_field_populated(self, integration_client):
        data = integration_client.get("/api/locations").get_json()
        cph = next(l for l in data["locations"] if l["name"] == "Copenhagen DC")
        assert cph["tenant"] == "Acme Corp"

    def test_tenant_group_field_populated(self, integration_client):
        data = integration_client.get("/api/locations").get_json()
        cph = next(l for l in data["locations"] if l["name"] == "Copenhagen DC")
        assert cph["tenant_group"] == "Corporate"

    def test_tenant_group_empty_when_no_group(self, integration_client):
        """Frankfurt DC has tenant DataCenter GmbH which has no tenant group."""
        data = integration_client.get("/api/locations").get_json()
        fra = next(l for l in data["locations"] if l["name"] == "Frankfurt DC")
        assert fra["tenant_group"] == ""

    def test_asn_field_populated(self, integration_client):
        data = integration_client.get("/api/locations").get_json()
        cph = next(l for l in data["locations"] if l["name"] == "Copenhagen DC")
        assert cph["asn"] == 65001

    def test_coordinates_are_floats(self, integration_client):
        """Nautobot returns lat/lon as strings; the app must coerce them to float."""
        data = integration_client.get("/api/locations").get_json()
        for loc in data["locations"]:
            assert isinstance(loc["latitude"], float)
            assert isinstance(loc["longitude"], float)


# ---------------------------------------------------------------------------
# 3. /api/locations/<id>/detail – devices + ASNs
# ---------------------------------------------------------------------------
class TestLocationDetailEndpoint:
    def test_returns_200_for_valid_id(self, integration_client):
        resp = integration_client.get("/api/locations/loc-cph/detail")
        assert resp.status_code == 200

    def test_devices_returned_for_copenhagen_dc(self, integration_client):
        data = integration_client.get("/api/locations/loc-cph/detail").get_json()
        assert len(data["devices"]) == 3
        device_names = [d["name"] for d in data["devices"]]
        assert "cph-core-rt01" in device_names
        assert "cph-dist-sw01" in device_names
        assert "cph-fw01" in device_names

    def test_device_fields_populated(self, integration_client):
        data = integration_client.get("/api/locations/loc-cph/detail").get_json()
        router = next(d for d in data["devices"] if d["name"] == "cph-core-rt01")
        assert router["manufacturer"] == "Cisco"
        assert router["device_type"] == "ASR1001-X"
        assert router["role"] == "Core Router"
        assert router["status"] == "Active"
        assert router["tenant"] == "Acme Corp"

    def test_asns_returned_for_copenhagen_dc(self, integration_client):
        data = integration_client.get("/api/locations/loc-cph/detail").get_json()
        assert len(data["asns"]) == 1
        assert data["asns"][0]["asn"] == 65001

    def test_multiple_asns_returned_for_london(self, integration_client):
        """London HQ has two ASNs."""
        data = integration_client.get("/api/locations/loc-lon/detail").get_json()
        asn_numbers = [a["asn"] for a in data["asns"]]
        assert 65050 in asn_numbers
        assert 65051 in asn_numbers

    def test_location_with_no_devices_returns_empty_list(self, integration_client):
        """Oslo Office has no devices."""
        data = integration_client.get("/api/locations/loc-osl/detail").get_json()
        assert data["devices"] == []

    def test_location_with_no_asns_returns_empty_list(self, integration_client):
        """Oslo Office has no ASNs."""
        data = integration_client.get("/api/locations/loc-osl/detail").get_json()
        assert data["asns"] == []

    def test_amsterdam_has_three_devices(self, integration_client):
        data = integration_client.get("/api/locations/loc-ams/detail").get_json()
        assert len(data["devices"]) == 3

    def test_london_has_eight_devices(self, integration_client):
        data = integration_client.get("/api/locations/loc-lon/detail").get_json()
        assert len(data["devices"]) == 8

    def test_london_has_offline_devices(self, integration_client):
        data = integration_client.get("/api/locations/loc-lon/detail").get_json()
        statuses = [d["status"] for d in data["devices"]]
        assert "Offline" in statuses

    def test_london_has_active_devices(self, integration_client):
        data = integration_client.get("/api/locations/loc-lon/detail").get_json()
        statuses = [d["status"] for d in data["devices"]]
        assert "Active" in statuses

    def test_london_colo_has_devices(self, integration_client):
        """London Colo shares the same coordinates as London HQ and has its own devices."""
        data = integration_client.get("/api/locations/loc-lon2/detail").get_json()
        assert len(data["devices"]) == 1
        assert data["devices"][0]["name"] == "lon2-edge-rt01"

    def test_london_colo_has_asns(self, integration_client):
        """London Colo has its own ASN."""
        data = integration_client.get("/api/locations/loc-lon2/detail").get_json()
        assert len(data["asns"]) == 1
        assert data["asns"][0]["asn"] == 65052

    def test_colocated_locations_both_in_locations_list(self, integration_client):
        """Both London HQ and London Colo appear in the locations list."""
        data = integration_client.get("/api/locations").get_json()
        names = [l["name"] for l in data["locations"]]
        assert "London HQ" in names
        assert "London Colo" in names

    def test_colocated_locations_have_same_coordinates(self, integration_client):
        """London HQ and London Colo share identical lat/lon."""
        data = integration_client.get("/api/locations").get_json()
        london_hq = next(l for l in data["locations"] if l["name"] == "London HQ")
        london_colo = next(l for l in data["locations"] if l["name"] == "London Colo")
        assert london_hq["latitude"] == london_colo["latitude"]
        assert london_hq["longitude"] == london_colo["longitude"]


# ---------------------------------------------------------------------------
# 4. /api/search – proximity search
# ---------------------------------------------------------------------------
class TestSearchEndpoint:
    def test_gps_search_finds_two_copenhagen_locations(self, integration_client):
        """
        Both Copenhagen DC (0 km) and Copenhagen Colocation (~1.2 km) are within
        5 km of coordinates 55.6761, 12.5683.
        """
        resp = integration_client.get("/api/search?q=55.6761,12.5683")
        assert resp.status_code == 200
        data = resp.get_json()
        names = [l["name"] for l in data["locations"]]
        assert "Copenhagen DC" in names
        assert "Copenhagen Colocation" in names
        assert data["count"] == 2

    def test_results_sorted_by_distance(self, integration_client):
        resp = integration_client.get("/api/search?q=55.6761,12.5683")
        distances = [l["distance_km"] for l in resp.get_json()["locations"]]
        assert distances == sorted(distances)

    def test_distance_km_is_zero_for_exact_match(self, integration_client):
        resp = integration_client.get("/api/search?q=55.6761,12.5683")
        data = resp.get_json()
        cph_dc = next(l for l in data["locations"] if l["name"] == "Copenhagen DC")
        assert cph_dc["distance_km"] == pytest.approx(0.0, abs=0.01)

    def test_distant_search_returns_no_results(self, integration_client):
        """52.52, 13.40 is Berlin – no Nautobot locations within 5 km."""
        resp = integration_client.get("/api/search?q=52.52,13.40")
        data = resp.get_json()
        assert data["count"] == 0
        assert data["locations"] == []

    def test_radius_field_is_5(self, integration_client):
        resp = integration_client.get("/api/search?q=55.6761,12.5683")
        assert resp.get_json()["radius_km"] == 5

    def test_search_lat_lon_echoed_back(self, integration_client):
        resp = integration_client.get("/api/search?q=51.5074,-0.1278")
        data = resp.get_json()
        assert data["search_lat"] == pytest.approx(51.5074)
        assert data["search_lon"] == pytest.approx(-0.1278)

    def test_london_search_finds_london_hq(self, integration_client):
        resp = integration_client.get("/api/search?q=51.5074,-0.1278")
        data = resp.get_json()
        names = [l["name"] for l in data["locations"]]
        assert "London HQ" in names

    def test_london_search_finds_colocated_sites(self, integration_client):
        """Both London HQ and London Colo share the same coordinates."""
        resp = integration_client.get("/api/search?q=51.5074,-0.1278")
        data = resp.get_json()
        names = [l["name"] for l in data["locations"]]
        assert "London HQ" in names
        assert "London Colo" in names
        assert data["count"] == 2

    def test_missing_query_returns_400(self, integration_client):
        resp = integration_client.get("/api/search")
        assert resp.status_code == 400

    def test_distance_km_field_present_in_all_results(self, integration_client):
        resp = integration_client.get("/api/search?q=55.6761,12.5683")
        for loc in resp.get_json()["locations"]:
            assert "distance_km" in loc
            assert isinstance(loc["distance_km"], float)

    def test_stockholm_search_returns_only_stockholm(self, integration_client):
        """Stockholm PoP is the only location near 59.3293, 18.0686."""
        resp = integration_client.get("/api/search?q=59.3293,18.0686")
        data = resp.get_json()
        assert data["count"] == 1
        assert data["locations"][0]["name"] == "Stockholm PoP"


# ---------------------------------------------------------------------------
# 5. End-to-end scenario: open map → click location → see devices
# ---------------------------------------------------------------------------
class TestEndToEndScenario:
    def test_scenario_full_flow(self, integration_client):
        """
        Simulates a user:
        1. Opening the map
        2. Loading all locations
        3. Clicking Copenhagen DC to load its details
        4. Searching for nearby locations
        """
        # Step 1: Load the map page
        page_resp = integration_client.get("/")
        assert page_resp.status_code == 200

        # Step 2: Load all locations (the JS would do this via fetch)
        locs_resp = integration_client.get("/api/locations")
        assert locs_resp.status_code == 200
        locations = locs_resp.get_json()["locations"]
        cph = next(l for l in locations if l["name"] == "Copenhagen DC")

        # Step 3: Click the Copenhagen DC marker → fetch details
        detail_resp = integration_client.get(f"/api/locations/{cph['id']}/detail")
        assert detail_resp.status_code == 200
        detail = detail_resp.get_json()
        assert detail["devices"]
        assert detail["asns"]

        # Step 4: Search near Copenhagen
        search_resp = integration_client.get(
            f"/api/search?q={cph['latitude']},{cph['longitude']}"
        )
        assert search_resp.status_code == 200
        nearby = search_resp.get_json()
        assert nearby["count"] >= 1
        assert any(l["name"] == "Copenhagen DC" for l in nearby["locations"])
