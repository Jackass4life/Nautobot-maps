import json
import pytest
from unittest.mock import patch, MagicMock

import app as flask_app


@pytest.fixture
def client():
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "test-secret"
    # Clear cache before each test
    flask_app._cache.clear()
    with flask_app.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Sample Nautobot API fixtures
# ---------------------------------------------------------------------------
SAMPLE_LOCATIONS_PAGE = {
    "count": 2,
    "next": None,
    "results": [
        {
            "id": "loc-1",
            "name": "Copenhagen DC",
            "slug": "cph-dc",
            "status": {"label": "Active"},
            "location_type": {"name": "Data Center"},
            "parent": {"name": "Denmark"},
            "latitude": "55.6761",
            "longitude": "12.5683",
            "description": "Main DC",
            "physical_address": "Somestreet 1, Copenhagen",
            "tenant": {"id": "ten-1", "name": "Acme Corp"},
            "asn": 65001,
            "time_zone": "Europe/Copenhagen",
            "url": "https://nautobot.example.com/api/dcim/locations/loc-1/",
        },
        {
            "id": "loc-2",
            "name": "Aarhus PoP",
            "slug": "aar-pop",
            "status": {"label": "Planned"},
            "location_type": {"name": "PoP"},
            "parent": None,
            "latitude": "56.1629",
            "longitude": "10.2039",
            "description": "",
            "physical_address": "",
            "tenant": None,
            "asn": None,
            "time_zone": "Europe/Copenhagen",
            "url": "https://nautobot.example.com/api/dcim/locations/loc-2/",
        },
        # Location without coordinates – should be excluded
        {
            "id": "loc-3",
            "name": "No GPS",
            "slug": "no-gps",
            "status": {"label": "Active"},
            "location_type": {"name": "Office"},
            "parent": None,
            "latitude": None,
            "longitude": None,
            "description": "",
            "physical_address": "",
            "tenant": None,
            "asn": None,
            "time_zone": "",
            "url": "",
        },
    ],
}

SAMPLE_DEVICES_PAGE = {
    "count": 1,
    "next": None,
    "results": [
        {
            "id": "dev-1",
            "name": "router01",
            "device_type": {
                "model": "ASR1001-X",
                "manufacturer": {"name": "Cisco"},
            },
            "role": {"name": "Core Router"},
            "status": {"label": "Active"},
            "platform": {"name": "IOS-XE"},
            "serial": "SN123",
            "tenant": {"name": "Acme Corp"},
        }
    ],
}

SAMPLE_ASNS_PAGE = {
    "count": 1,
    "next": None,
    "results": [
        {
            "asn": 65001,
            "description": "Main ASN",
            "tenant": {"name": "Acme Corp"},
        }
    ],
}


# ---------------------------------------------------------------------------
# Helper – mock nautobot_get to return fixture data
# ---------------------------------------------------------------------------
def mock_nautobot_get(endpoint, params=None):
    params = params or {}
    if "dcim/locations" in endpoint:
        return SAMPLE_LOCATIONS_PAGE
    if "dcim/devices" in endpoint:
        return SAMPLE_DEVICES_PAGE
    if "ipam/asns" in endpoint:
        return SAMPLE_ASNS_PAGE
    return {"count": 0, "next": None, "results": []}


# ---------------------------------------------------------------------------
# Tests: /api/locations
# ---------------------------------------------------------------------------
class TestApiLocations:
    def test_returns_locations_with_coordinates(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "locations" in data
        # loc-3 (no GPS) must be excluded
        assert len(data["locations"]) == 2

    def test_location_fields(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["name"] == "Copenhagen DC"
        assert loc["latitude"] == 55.6761
        assert loc["longitude"] == 12.5683
        assert loc["tenant"] == "Acme Corp"
        assert loc["asn"] == 65001
        assert loc["status"] == "Active"

    def test_missing_env_vars_returns_503(self, client):
        original_url = flask_app.NAUTOBOT_URL
        original_token = flask_app.NAUTOBOT_TOKEN
        flask_app.NAUTOBOT_URL = ""
        flask_app.NAUTOBOT_TOKEN = ""
        try:
            resp = client.get("/api/locations")
            assert resp.status_code == 503
            assert "error" in resp.get_json()
        finally:
            flask_app.NAUTOBOT_URL = original_url
            flask_app.NAUTOBOT_TOKEN = original_token

    def test_nautobot_http_error_returns_502(self, client):
        import requests as req_lib

        http_err = req_lib.HTTPError(response=MagicMock(status_code=500))
        with patch.object(flask_app, "nautobot_get", side_effect=http_err):
            resp = client.get("/api/locations")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Tests: /api/locations/<id>/detail
# ---------------------------------------------------------------------------
class TestApiLocationDetail:
    def test_returns_devices_and_asns(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations/loc-1/detail")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["devices"]) == 1
        assert data["devices"][0]["name"] == "router01"
        assert len(data["asns"]) == 1
        assert data["asns"][0]["asn"] == 65001

    def test_device_fields(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations/loc-1/detail")
        dev = resp.get_json()["devices"][0]
        assert dev["manufacturer"] == "Cisco"
        assert dev["device_type"] == "ASR1001-X"
        assert dev["role"] == "Core Router"
        assert dev["tenant"] == "Acme Corp"


# ---------------------------------------------------------------------------
# Tests: /api/search
# ---------------------------------------------------------------------------
class TestApiSearch:
    def test_missing_query_returns_400(self, client):
        resp = client.get("/api/search")
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_gps_coordinates_search(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            # Copenhagen coordinates – loc-1 is exactly at 55.6761,12.5683 (distance 0)
            resp = client.get("/api/search?q=55.6761,12.5683")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["search_lat"] == pytest.approx(55.6761)
        assert data["search_lon"] == pytest.approx(12.5683)
        assert data["radius_km"] == 5
        # loc-1 is at the exact point
        names = [l["name"] for l in data["locations"]]
        assert "Copenhagen DC" in names

    def test_gps_no_results_far_away(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            # Tokyo – far from all test locations
            resp = client.get("/api/search?q=35.6895,139.6917")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 0
        assert data["locations"] == []

    def test_search_results_sorted_by_distance(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            # Point very close to loc-1 (within 5 km)
            resp = client.get("/api/search?q=55.678,12.571")
        data = resp.get_json()
        distances = [l["distance_km"] for l in data["locations"]]
        assert distances == sorted(distances)

    def test_address_geocoding(self, client):
        mock_geo_result = MagicMock()
        mock_geo_result.latitude = 55.6761
        mock_geo_result.longitude = 12.5683
        mock_geolocator = MagicMock()
        mock_geolocator.geocode.return_value = mock_geo_result

        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            with patch("app.Nominatim", return_value=mock_geolocator):
                resp = client.get("/api/search?q=Copenhagen")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["search_lat"] == pytest.approx(55.6761)

    def test_address_not_found_returns_404(self, client):
        mock_geolocator = MagicMock()
        mock_geolocator.geocode.return_value = None

        with patch("app.Nominatim", return_value=mock_geolocator):
            resp = client.get("/api/search?q=ThisPlaceDoesNotExist12345")
        assert resp.status_code == 404
        assert "error" in resp.get_json()

    def test_distance_km_field_present(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/search?q=55.6761,12.5683")
        data = resp.get_json()
        for loc in data["locations"]:
            assert "distance_km" in loc


# ---------------------------------------------------------------------------
# Tests: index page
# ---------------------------------------------------------------------------
class TestIndex:
    def test_index_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Nautobot" in resp.data

    def test_index_contains_map_div(self, client):
        resp = client.get("/")
        assert b'id="map"' in resp.data

    def test_index_contains_search_input(self, client):
        resp = client.get("/")
        assert b'id="search-input"' in resp.data

    def test_index_contains_filter_type(self, client):
        resp = client.get("/")
        assert b'id="filter-type"' in resp.data

    def test_index_contains_filter_tenant(self, client):
        resp = client.get("/")
        assert b'id="filter-tenant"' in resp.data

    def test_index_contains_filter_status(self, client):
        resp = client.get("/")
        assert b'id="filter-status"' in resp.data

    def test_index_contains_filter_parent(self, client):
        resp = client.get("/")
        assert b'id="filter-parent"' in resp.data

    def test_index_contains_filter_section(self, client):
        resp = client.get("/")
        assert b'id="filter-section"' in resp.data



# ---------------------------------------------------------------------------
# Tests: caching
# ---------------------------------------------------------------------------
class TestCaching:
    def test_cache_reduces_api_calls(self):
        """Calling nautobot_get twice with the same args should only make one HTTP request."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        flask_app._cache.clear()
        with patch.object(req_lib, "get", return_value=mock_resp) as mock_get:
            # Patch env vars so nautobot_get doesn't raise RuntimeError
            flask_app.NAUTOBOT_URL = "http://nautobot.test"
            flask_app.NAUTOBOT_TOKEN = "test-token"
            try:
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
            finally:
                flask_app.NAUTOBOT_URL = ""
                flask_app.NAUTOBOT_TOKEN = ""

        # Second call should have been served from cache – only 1 HTTP request made
        assert mock_get.call_count == 1

    def test_cache_set_and_get(self):
        flask_app._cache.clear()
        flask_app._cache_set("test-key", {"data": 42})
        result = flask_app._cache_get("test-key")
        assert result == {"data": 42}

    def test_cache_expires(self):
        import time

        flask_app._cache.clear()
        flask_app._cache_set("expiring-key", "value")
        # Manually expire the entry
        flask_app._cache["expiring-key"]["ts"] -= flask_app.CACHE_TTL + 1
        result = flask_app._cache_get("expiring-key")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: SSL verification configuration
# ---------------------------------------------------------------------------
class TestSSLVerification:
    def test_verify_ssl_defaults_to_true(self):
        """When NAUTOBOT_VERIFY_SSL is not set, verify should default to True."""
        # The module-level NAUTOBOT_VERIFY_SSL is parsed at import time from
        # the env var (default "true"), so it should be True.
        assert flask_app.NAUTOBOT_VERIFY_SSL is True

    def test_verify_ssl_false_disables_verification(self):
        """Setting NAUTOBOT_VERIFY_SSL=false should pass verify=False to requests."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        flask_app._cache.clear()
        original_url = flask_app.NAUTOBOT_URL
        original_token = flask_app.NAUTOBOT_TOKEN
        original_verify = flask_app.NAUTOBOT_VERIFY_SSL
        flask_app.NAUTOBOT_URL = "https://nautobot.test"
        flask_app.NAUTOBOT_TOKEN = "test-token"
        flask_app.NAUTOBOT_VERIFY_SSL = False
        try:
            with patch.object(req_lib, "get", return_value=mock_resp) as mock_get:
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["verify"] is False
        finally:
            flask_app.NAUTOBOT_URL = original_url
            flask_app.NAUTOBOT_TOKEN = original_token
            flask_app.NAUTOBOT_VERIFY_SSL = original_verify

    def test_verify_ssl_true_enables_verification(self):
        """Setting NAUTOBOT_VERIFY_SSL=true should pass verify=True to requests."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        flask_app._cache.clear()
        original_url = flask_app.NAUTOBOT_URL
        original_token = flask_app.NAUTOBOT_TOKEN
        original_verify = flask_app.NAUTOBOT_VERIFY_SSL
        flask_app.NAUTOBOT_URL = "https://nautobot.test"
        flask_app.NAUTOBOT_TOKEN = "test-token"
        flask_app.NAUTOBOT_VERIFY_SSL = True
        try:
            with patch.object(req_lib, "get", return_value=mock_resp) as mock_get:
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["verify"] is True
        finally:
            flask_app.NAUTOBOT_URL = original_url
            flask_app.NAUTOBOT_TOKEN = original_token
            flask_app.NAUTOBOT_VERIFY_SSL = original_verify

    def test_verify_ssl_custom_ca_bundle_path(self):
        """Setting NAUTOBOT_VERIFY_SSL to a path should pass that path to requests."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        flask_app._cache.clear()
        original_url = flask_app.NAUTOBOT_URL
        original_token = flask_app.NAUTOBOT_TOKEN
        original_verify = flask_app.NAUTOBOT_VERIFY_SSL
        flask_app.NAUTOBOT_URL = "https://nautobot.test"
        flask_app.NAUTOBOT_TOKEN = "test-token"
        flask_app.NAUTOBOT_VERIFY_SSL = "/etc/ssl/certs/custom-ca.pem"
        try:
            with patch.object(req_lib, "get", return_value=mock_resp) as mock_get:
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["verify"] == "/etc/ssl/certs/custom-ca.pem"
        finally:
            flask_app.NAUTOBOT_URL = original_url
            flask_app.NAUTOBOT_TOKEN = original_token
            flask_app.NAUTOBOT_VERIFY_SSL = original_verify


# ---------------------------------------------------------------------------
# Tests: Accept header / API version configuration
# ---------------------------------------------------------------------------
class TestApiVersionHeader:
    def test_default_accept_header_has_no_version(self):
        """When NAUTOBOT_API_VERSION is empty, Accept should be plain application/json."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        flask_app._cache.clear()
        original_url = flask_app.NAUTOBOT_URL
        original_token = flask_app.NAUTOBOT_TOKEN
        original_version = flask_app.NAUTOBOT_API_VERSION
        flask_app.NAUTOBOT_URL = "https://nautobot.test"
        flask_app.NAUTOBOT_TOKEN = "test-token"
        flask_app.NAUTOBOT_API_VERSION = ""
        try:
            with patch.object(req_lib, "get", return_value=mock_resp) as mock_get:
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["headers"]["Accept"] == "application/json"
        finally:
            flask_app.NAUTOBOT_URL = original_url
            flask_app.NAUTOBOT_TOKEN = original_token
            flask_app.NAUTOBOT_API_VERSION = original_version

    def test_accept_header_includes_version_when_set(self):
        """When NAUTOBOT_API_VERSION is set, Accept should include the version."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        flask_app._cache.clear()
        original_url = flask_app.NAUTOBOT_URL
        original_token = flask_app.NAUTOBOT_TOKEN
        original_version = flask_app.NAUTOBOT_API_VERSION
        flask_app.NAUTOBOT_URL = "https://nautobot.test"
        flask_app.NAUTOBOT_TOKEN = "test-token"
        flask_app.NAUTOBOT_API_VERSION = "3.0"
        try:
            with patch.object(req_lib, "get", return_value=mock_resp) as mock_get:
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["headers"]["Accept"] == "application/json; version=3.0"
        finally:
            flask_app.NAUTOBOT_URL = original_url
            flask_app.NAUTOBOT_TOKEN = original_token
            flask_app.NAUTOBOT_API_VERSION = original_version
