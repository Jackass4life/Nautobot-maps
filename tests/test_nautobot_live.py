"""
Live integration tests for Nautobot Maps against a real Nautobot instance.

These tests are **skipped** unless the ``NAUTOBOT_LIVE_URL`` environment
variable points to a running Nautobot (e.g. ``http://localhost:8080``).

They exercise the Flask application end-to-end against a genuine Nautobot
API – no mocking involved.

Usage:
    # Requires a running Nautobot with seeded data
    NAUTOBOT_LIVE_URL=http://localhost:8080 \
    NAUTOBOT_LIVE_TOKEN=aaaa-bbbb-cccc-dddd-eeee \
    python -m pytest tests/test_nautobot_live.py -v
"""

import os

import pytest

import app as flask_app

# ---------------------------------------------------------------------------
# Skip entire module when no live Nautobot is available
# ---------------------------------------------------------------------------

NAUTOBOT_LIVE_URL = os.environ.get("NAUTOBOT_LIVE_URL", "")
NAUTOBOT_LIVE_TOKEN = os.environ.get("NAUTOBOT_LIVE_TOKEN", "")

pytestmark = pytest.mark.skipif(
    not NAUTOBOT_LIVE_URL,
    reason="NAUTOBOT_LIVE_URL not set – skipping live Nautobot tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_client():
    """
    Return a Flask test client configured to talk to the real Nautobot
    instance described by NAUTOBOT_LIVE_URL / NAUTOBOT_LIVE_TOKEN.
    """
    original_url = flask_app.NAUTOBOT_URL
    original_token = flask_app.NAUTOBOT_TOKEN
    original_verify = flask_app.NAUTOBOT_VERIFY_SSL

    flask_app.NAUTOBOT_URL = NAUTOBOT_LIVE_URL
    flask_app.NAUTOBOT_TOKEN = NAUTOBOT_LIVE_TOKEN
    flask_app.NAUTOBOT_VERIFY_SSL = False
    flask_app.cache.clear()

    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "live-test-secret"

    with flask_app.app.test_client() as client:
        yield client

    flask_app.NAUTOBOT_URL = original_url
    flask_app.NAUTOBOT_TOKEN = original_token
    flask_app.NAUTOBOT_VERIFY_SSL = original_verify


@pytest.fixture(autouse=True)
def clear_cache():
    flask_app.cache.clear()
    yield
    flask_app.cache.clear()


# ---------------------------------------------------------------------------
# 1. Map UI
# ---------------------------------------------------------------------------


class TestLiveMapUI:
    def test_index_page_loads(self, live_client):
        resp = live_client.get("/")
        assert resp.status_code == 200
        assert b"Nautobot" in resp.data

    def test_index_has_leaflet(self, live_client):
        resp = live_client.get("/")
        assert b"leaflet" in resp.data.lower()

    def test_index_has_search_input(self, live_client):
        resp = live_client.get("/")
        assert b'id="search-input"' in resp.data


# ---------------------------------------------------------------------------
# 2. /api/locations – seeded locations from the real Nautobot
# ---------------------------------------------------------------------------


class TestLiveLocations:
    def test_returns_200(self, live_client):
        resp = live_client.get("/api/locations")
        assert resp.status_code == 200

    def test_returns_all_seeded_locations(self, live_client):
        """All 8 seeded locations have GPS coordinates and must appear."""
        data = live_client.get("/api/locations").get_json()
        assert "locations" in data
        assert len(data["locations"]) >= 8

    def test_location_has_required_fields(self, live_client):
        data = live_client.get("/api/locations").get_json()
        for loc in data["locations"]:
            assert "id" in loc
            assert "name" in loc
            assert "latitude" in loc
            assert "longitude" in loc
            assert "status" in loc
            assert isinstance(loc["latitude"], float)
            assert isinstance(loc["longitude"], float)

    def test_copenhagen_dc_present(self, live_client):
        data = live_client.get("/api/locations").get_json()
        names = [loc["name"] for loc in data["locations"]]
        assert "Copenhagen DC" in names

    def test_tenant_populated(self, live_client):
        data = live_client.get("/api/locations").get_json()
        cph = next(
            (loc for loc in data["locations"] if loc["name"] == "Copenhagen DC"),
            None,
        )
        assert cph is not None
        assert cph["tenant"] == "Acme Corp"

    def test_planned_status_present(self, live_client):
        data = live_client.get("/api/locations").get_json()
        oslo = next(
            (loc for loc in data["locations"] if loc["name"] == "Oslo Office"),
            None,
        )
        assert oslo is not None
        assert oslo["status"] == "Planned"

    def test_facility_populated(self, live_client):
        data = live_client.get("/api/locations").get_json()
        cph = next(
            (loc for loc in data["locations"] if loc["name"] == "Copenhagen DC"),
            None,
        )
        assert cph is not None
        assert cph["facility"] == "CPH-1"

    def test_tags_populated(self, live_client):
        data = live_client.get("/api/locations").get_json()
        cph = next(
            (loc for loc in data["locations"] if loc["name"] == "Copenhagen DC"),
            None,
        )
        assert cph is not None
        assert "critical" in cph["tags"]
        assert "production" in cph["tags"]

    def test_tags_empty_when_not_set(self, live_client):
        data = live_client.get("/api/locations").get_json()
        oslo = next(
            (loc for loc in data["locations"] if loc["name"] == "Oslo Office"),
            None,
        )
        assert oslo is not None
        assert oslo["tags"] == []



# ---------------------------------------------------------------------------
# 3. /api/locations/<id>/detail – devices + ASNs
# ---------------------------------------------------------------------------


class TestLiveLocationDetail:
    def _location_id(self, client, name: str) -> str:
        """Look up a location ID by name."""
        data = client.get("/api/locations").get_json()
        loc = next(
            (l for l in data["locations"] if l["name"] == name),
            None,
        )
        assert loc is not None, f"Location '{name}' not found"
        return loc["id"]

    def test_detail_returns_200(self, live_client):
        loc_id = self._location_id(live_client, "Copenhagen DC")
        resp = live_client.get(f"/api/locations/{loc_id}/detail")
        assert resp.status_code == 200

    def test_devices_returned_for_copenhagen_dc(self, live_client):
        loc_id = self._location_id(live_client, "Copenhagen DC")
        data = live_client.get(f"/api/locations/{loc_id}/detail").get_json()
        assert "devices" in data
        assert len(data["devices"]) == 3
        device_names = [d["name"] for d in data["devices"]]
        assert "cph-core-rt01" in device_names

    def test_device_fields_populated(self, live_client):
        loc_id = self._location_id(live_client, "Copenhagen DC")
        data = live_client.get(f"/api/locations/{loc_id}/detail").get_json()
        router = next(
            (d for d in data["devices"] if d["name"] == "cph-core-rt01"),
            None,
        )
        assert router is not None
        assert router["manufacturer"] == "Cisco"
        assert router["device_type"] == "ASR1001-X"
        assert router["role"] == "Core Router"
        assert router["status"] == "Active"

    def test_oslo_has_no_devices(self, live_client):
        loc_id = self._location_id(live_client, "Oslo Office")
        data = live_client.get(f"/api/locations/{loc_id}/detail").get_json()
        assert data["devices"] == []

    def test_london_has_three_devices(self, live_client):
        loc_id = self._location_id(live_client, "London HQ")
        data = live_client.get(f"/api/locations/{loc_id}/detail").get_json()
        assert len(data["devices"]) == 3


# ---------------------------------------------------------------------------
# 4. /api/search – proximity search with real coordinates
# ---------------------------------------------------------------------------


class TestLiveSearch:
    def test_gps_search_finds_copenhagen(self, live_client):
        resp = live_client.get("/api/search?q=55.6761,12.5683")
        assert resp.status_code == 200
        data = resp.get_json()
        names = [loc["name"] for loc in data["locations"]]
        assert "Copenhagen DC" in names

    def test_copenhagen_search_finds_colocation(self, live_client):
        """Both Copenhagen locations are within 5 km of each other."""
        resp = live_client.get("/api/search?q=55.6761,12.5683")
        data = resp.get_json()
        names = [loc["name"] for loc in data["locations"]]
        assert "Copenhagen Colocation" in names

    def test_results_sorted_by_distance(self, live_client):
        resp = live_client.get("/api/search?q=55.6761,12.5683")
        data = resp.get_json()
        distances = [loc["distance_km"] for loc in data["locations"]]
        assert distances == sorted(distances)

    def test_distant_search_returns_no_results(self, live_client):
        """52.52, 13.40 is Berlin – no seeded locations within 5 km."""
        resp = live_client.get("/api/search?q=52.52,13.40")
        data = resp.get_json()
        assert data["count"] == 0

    def test_radius_field_is_5(self, live_client):
        resp = live_client.get("/api/search?q=55.6761,12.5683")
        assert resp.get_json()["radius_km"] == 5

    def test_missing_query_returns_400(self, live_client):
        resp = live_client.get("/api/search")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 5. End-to-end scenario
# ---------------------------------------------------------------------------


class TestLiveEndToEnd:
    def test_full_user_flow(self, live_client):
        """Load map → list locations → fetch detail → search nearby."""
        # 1. Map page loads
        assert live_client.get("/").status_code == 200

        # 2. Fetch all locations
        locs_resp = live_client.get("/api/locations")
        assert locs_resp.status_code == 200
        locations = locs_resp.get_json()["locations"]
        assert len(locations) >= 8

        # 3. Fetch details for Copenhagen DC
        cph = next(l for l in locations if l["name"] == "Copenhagen DC")
        detail_resp = live_client.get(f"/api/locations/{cph['id']}/detail")
        assert detail_resp.status_code == 200
        detail = detail_resp.get_json()
        assert len(detail["devices"]) == 3

        # 4. Proximity search near Copenhagen
        search_resp = live_client.get(
            f"/api/search?q={cph['latitude']},{cph['longitude']}"
        )
        assert search_resp.status_code == 200
        nearby = search_resp.get_json()
        assert nearby["count"] >= 1
        assert any(l["name"] == "Copenhagen DC" for l in nearby["locations"])
