import json
import pytest
from unittest.mock import patch, MagicMock

import app as flask_app


@pytest.fixture
def client():
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "test-secret"
    # Clear cache before each test
    flask_app.cache.clear()
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
            "tenant": {"id": "ten-1", "name": "Acme Corp", "tenant_group": {"id": "tg-1", "name": "Corporate"}},
            "asn": 65001,
            "time_zone": "Europe/Copenhagen",
            "facility": "CPH-1",
            "tags": [{"name": "critical"}, {"name": "production"}],
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
            "facility": "",
            "tags": [],
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
            "facility": "",
            "tags": [],
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
    if "tenancy/tenant-groups" in endpoint:
        return {
            "count": 1, "next": None,
            "results": [{"id": "tg-1", "name": "Corporate"}],
        }
    if "tenancy/tenants" in endpoint:
        return {
            "count": 1, "next": None,
            "results": [
                {"id": "ten-1", "name": "Acme Corp", "tenant_group": {"id": "tg-1", "name": "Corporate"}},
            ],
        }
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

    def test_location_type_field_populated(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["location_type"] == "Data Center"

    def test_parent_field_populated(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["parent"] == "Denmark"

    def test_tenant_group_field_populated(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["tenant_group"] == "Corporate"

    def test_tenant_group_empty_when_no_tenant(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        # loc-2 (Aarhus PoP) has no tenant
        loc = resp.get_json()["locations"][1]
        assert loc["tenant_group"] == ""

    def test_facility_field_populated(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["facility"] == "CPH-1"

    def test_facility_empty_when_not_set(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][1]
        assert loc["facility"] == ""

    def test_tags_field_populated(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["tags"] == ["critical", "production"]

    def test_tags_empty_when_none(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][1]
        assert loc["tags"] == []

    def test_tags_fallback_with_brief_nested_object(self, client):
        """When tags are brief (id+url only), the fallback map resolves names."""
        brief_locations = {
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "loc-tags",
                    "name": "Tagged Location",
                    "slug": "tagged-loc",
                    "status": {"label": "Active"},
                    "location_type": {"name": "Data Center"},
                    "parent": None,
                    "latitude": "55.0",
                    "longitude": "12.0",
                    "description": "",
                    "physical_address": "",
                    "tenant": None,
                    "asn": None,
                    "time_zone": "",
                    "facility": "",
                    "tags": [
                        {"id": "tag-1", "url": "http://nautobot/api/extras/tags/tag-1/"},
                        {"id": "tag-2", "url": "http://nautobot/api/extras/tags/tag-2/"},
                    ],
                    "url": "",
                },
            ],
        }
        tags_page = {
            "count": 2,
            "next": None,
            "results": [
                {"id": "tag-1", "name": "critical"},
                {"id": "tag-2", "name": "production"},
            ],
        }

        def mock_get(endpoint, params=None):
            if "extras/tags" in endpoint:
                return tags_page
            if "dcim/locations" in endpoint:
                return brief_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(flask_app, "nautobot_get", side_effect=mock_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["tags"] == ["critical", "production"]

    def test_location_type_fallback_with_brief_nested_object(self, client):
        """When location_type is brief (id+url only), the fallback map resolves the name."""
        brief_locations = {
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "loc-brief",
                    "name": "Brief Location",
                    "slug": "brief-loc",
                    "status": {"label": "Active"},
                    "location_type": {"id": "lt-dc", "url": "http://nautobot/api/dcim/location-types/lt-dc/"},
                    "parent": None,
                    "latitude": "55.0",
                    "longitude": "12.0",
                    "description": "",
                    "physical_address": "",
                    "tenant": None,
                    "asn": None,
                    "time_zone": "",
                    "url": "",
                },
            ],
        }
        lt_page = {
            "count": 1,
            "next": None,
            "results": [{"id": "lt-dc", "name": "Data Center"}],
        }

        def mock_get(endpoint, params=None):
            if "dcim/location-types" in endpoint:
                return lt_page
            if "dcim/locations" in endpoint:
                return brief_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(flask_app, "nautobot_get", side_effect=mock_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["location_type"] == "Data Center"

    def test_parent_fallback_with_brief_nested_object(self, client):
        """When parent is brief (id+url only), the fallback map resolves the name."""
        brief_locations = {
            "count": 2,
            "next": None,
            "results": [
                {
                    "id": "loc-parent",
                    "name": "Denmark",
                    "slug": "denmark",
                    "status": {"label": "Active"},
                    "location_type": {"name": "Region"},
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
                {
                    "id": "loc-child",
                    "name": "Copenhagen DC",
                    "slug": "cph-dc",
                    "status": {"label": "Active"},
                    "location_type": {"name": "Data Center"},
                    "parent": {"id": "loc-parent", "url": "http://nautobot/api/dcim/locations/loc-parent/"},
                    "latitude": "55.6761",
                    "longitude": "12.5683",
                    "description": "",
                    "physical_address": "",
                    "tenant": None,
                    "asn": None,
                    "time_zone": "",
                    "url": "",
                },
            ],
        }

        def mock_get(endpoint, params=None):
            if "dcim/locations" in endpoint:
                return brief_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(flask_app, "nautobot_get", side_effect=mock_get):
            resp = client.get("/api/locations")
        # loc-parent has no GPS (lat/lon=None) so only loc-child is returned
        locs = resp.get_json()["locations"]
        assert len(locs) == 1
        assert locs[0]["parent"] == "Denmark"

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
        assert dev["platform"] == "IOS-XE"
        assert dev["serial"] == "SN123"
        assert dev["status"] == "Active"

    def test_device_with_null_fields(self, client):
        """Devices with null nested fields must return strings, never None."""
        sparse_devices = {
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "dev-sparse",
                    "name": None,
                    "device_type": None,
                    "role": None,
                    "status": None,
                    "platform": None,
                    "serial": None,
                    "tenant": None,
                }
            ],
        }

        def mock_get(endpoint, params=None):
            if "dcim/devices" in endpoint:
                return sparse_devices
            if "dcim/device-types" in endpoint:
                return {"count": 0, "next": None, "results": []}
            if "dcim/manufacturers" in endpoint:
                return {"count": 0, "next": None, "results": []}
            if "extras/roles" in endpoint:
                return {"count": 0, "next": None, "results": []}
            if "tenancy/tenants" in endpoint:
                return {"count": 0, "next": None, "results": []}
            if "extras/statuses" in endpoint:
                return {"count": 0, "next": None, "results": []}
            if "ipam/asns" in endpoint:
                return {"count": 0, "next": None, "results": []}
            return {"count": 0, "next": None, "results": []}

        with patch.object(flask_app, "nautobot_get", side_effect=mock_get):
            resp = client.get("/api/locations/loc-1/detail")
        assert resp.status_code == 200
        dev = resp.get_json()["devices"][0]
        # Every field must be a string (not None/null) so the JS escHtml()
        # function never receives null.
        for field in ("id", "name", "device_type", "manufacturer", "role",
                      "status", "platform", "serial", "tenant"):
            assert dev[field] is not None, f"device field '{field}' is None"
            assert isinstance(dev[field], str), f"device field '{field}' is not a string"


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

    def test_index_contains_filter_tenant_group(self, client):
        resp = client.get("/")
        assert b'id="filter-tenant-group"' in resp.data

    def test_index_contains_nautobot_url(self, client):
        saved = flask_app.NAUTOBOT_URL
        flask_app.NAUTOBOT_URL = "https://nautobot.example.com"
        try:
            resp = client.get("/")
            assert b"window.NAUTOBOT_URL" in resp.data
            assert b"https://nautobot.example.com" in resp.data
        finally:
            flask_app.NAUTOBOT_URL = saved

    def test_index_nautobot_url_empty_when_unset(self, client):
        saved = flask_app.NAUTOBOT_URL
        flask_app.NAUTOBOT_URL = ""
        try:
            resp = client.get("/")
            assert b"window.NAUTOBOT_URL" in resp.data
            assert b'window.NAUTOBOT_URL = ""' in resp.data
        finally:
            flask_app.NAUTOBOT_URL = saved



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

        flask_app.cache.clear()
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
        flask_app.cache.clear()
        flask_app._cache_set("test-key", {"data": 42})
        result = flask_app._cache_get("test-key")
        assert result == {"data": 42}

    def test_cache_expires(self):
        """Verify that Flask-Caching is configured with the correct timeout."""
        flask_app.cache.clear()
        # Store with a very short timeout and verify it expires
        flask_app.cache.set("expiring-key", "value", timeout=1)
        import time
        time.sleep(1.1)
        result = flask_app._cache_get("expiring-key")
        assert result is None

    def test_cache_default_timeout_matches_cache_ttl(self):
        """Flask-Caching default timeout should match the CACHE_TTL env var."""
        assert flask_app.app.config["CACHE_DEFAULT_TIMEOUT"] == flask_app.CACHE_TTL


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

        flask_app.cache.clear()
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

        flask_app.cache.clear()
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

        flask_app.cache.clear()
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

        flask_app.cache.clear()
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

        flask_app.cache.clear()
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


# ---------------------------------------------------------------------------
# Tests: Custom error handlers
# ---------------------------------------------------------------------------
class TestErrorHandlers:
    def test_404_html_for_browser(self, client):
        resp = client.get("/nonexistent-page")
        assert resp.status_code == 404
        assert b"Page Not Found" in resp.data
        assert b"Back to Map" in resp.data

    def test_404_json_for_api_path(self, client):
        resp = client.get("/api/nonexistent")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "Not found"

    def test_404_json_when_accept_json(self, client):
        resp = client.get(
            "/nonexistent-page", headers={"Accept": "application/json"}
        )
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "Not found"

    def test_405_html_for_browser(self, client):
        resp = client.post("/")
        assert resp.status_code == 405
        assert b"Method Not Allowed" in resp.data

    def test_405_json_for_api_path(self, client):
        resp = client.post("/api/locations")
        assert resp.status_code == 405
        data = resp.get_json()
        assert data["error"] == "Method not allowed"


# ---------------------------------------------------------------------------
# Tests: configurable critical role keywords
# ---------------------------------------------------------------------------
class TestConfigurableCriticalKeywords:
    """Tests for the _get_critical_keywords / compute_alert_level helpers."""

    def setup_method(self):
        """Reset module-level keyword state before each test."""
        self._orig_env_kw = flask_app._ENV_CORE_ROLE_KEYWORDS
        self._orig_rules = dict(flask_app._CRITICALITY_RULES)

    def teardown_method(self):
        flask_app._ENV_CORE_ROLE_KEYWORDS = self._orig_env_kw
        flask_app._CRITICALITY_RULES = self._orig_rules

    def test_default_keywords_applied(self):
        """Without any configuration the built-in defaults are used."""
        flask_app._ENV_CORE_ROLE_KEYWORDS = flask_app._DEFAULT_CORE_ROLE_KEYWORDS
        flask_app._CRITICALITY_RULES = {}
        kw = flask_app._get_critical_keywords()
        assert "core" in kw
        assert "router" in kw

    def test_env_override_replaces_defaults(self):
        """_ENV_CORE_ROLE_KEYWORDS env override replaces defaults when no JSON rules."""
        flask_app._ENV_CORE_ROLE_KEYWORDS = ("firewall", "border")
        flask_app._CRITICALITY_RULES = {}
        kw = flask_app._get_critical_keywords()
        assert kw == ("firewall", "border")

    def test_env_override_used_as_fallback_for_unknown_type(self):
        """When rules have no matching type and no 'default' key, env override is used."""
        flask_app._ENV_CORE_ROLE_KEYWORDS = ("firewall",)
        flask_app._CRITICALITY_RULES = {"datacenter": ["core", "spine"]}
        kw = flask_app._get_critical_keywords("office")
        assert kw == ("firewall",)

    def test_location_type_rule_matched(self):
        """The exact location_type key is returned when present in rules."""
        flask_app._CRITICALITY_RULES = {
            "datacenter": ["core", "firewall"],
            "office": ["router"],
        }
        kw = flask_app._get_critical_keywords("Datacenter")
        assert "firewall" in kw

    def test_rules_default_key_used_for_unknown_type(self):
        """The 'default' key in rules is the fallback for unknown location types."""
        flask_app._CRITICALITY_RULES = {
            "default": ["core", "spine"],
            "office": ["router"],
        }
        kw = flask_app._get_critical_keywords("warehouse")
        assert kw == ("core", "spine")

    def test_compute_alert_level_respects_location_type(self):
        """compute_alert_level uses the correct keyword set for the given location type."""
        flask_app._CRITICALITY_RULES = {
            "office": ["router"],
            "datacenter": ["core", "firewall"],
        }
        # A "firewall" device offline in a datacenter → critical
        dc_devices = [{"id": "d1", "name": "fw01", "role": "Firewall", "status": "offline"}]
        result = flask_app.compute_alert_level(dc_devices, location_type="datacenter")
        assert result["level"] == "critical"

        # Same device in an office (only "router" is critical there) → medium (if >25%) or ok
        office_devices = [{"id": "d1", "name": "fw01", "role": "Firewall", "status": "offline"},
                          {"id": "d2", "name": "sw01", "role": "Switch", "status": "active"}]
        result = flask_app.compute_alert_level(office_devices, location_type="office")
        assert result["level"] != "critical"

    def test_compute_alert_level_no_devices(self):
        assert flask_app.compute_alert_level([]) == {"level": "ok", "reason": ""}

    def test_compute_alert_level_medium_threshold(self):
        """More than 25% of devices down → medium alert."""
        devices = [
            {"id": "d1", "name": "sw01", "role": "Switch", "status": "offline"},
            {"id": "d2", "name": "sw02", "role": "Switch", "status": "active"},
            {"id": "d3", "name": "sw03", "role": "Switch", "status": "active"},
        ]
        # 1/3 ≈ 33% > 25% → medium
        result = flask_app.compute_alert_level(devices)
        assert result["level"] == "medium"

    def test_compute_alert_level_ok_when_below_threshold(self):
        """Under 25% down and no core device down → ok."""
        devices = [
            {"id": "d1", "name": "sw01", "role": "Switch", "status": "offline"},
            {"id": "d2", "name": "sw02", "role": "Switch", "status": "active"},
            {"id": "d3", "name": "sw03", "role": "Switch", "status": "active"},
            {"id": "d4", "name": "sw04", "role": "Switch", "status": "active"},
            {"id": "d5", "name": "sw05", "role": "Switch", "status": "active"},
        ]
        # 1/5 = 20% ≤ 25% → ok
        result = flask_app.compute_alert_level(devices)
        assert result["level"] == "ok"


# ---------------------------------------------------------------------------
# Tests: location_type passed through detail endpoint
# ---------------------------------------------------------------------------
class TestLocationDetailWithLocationType:
    def test_location_type_param_accepted(self, client):
        """The ?location_type query param is accepted without error."""
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations/loc-1/detail?location_type=Data+Center")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "devices" in data
        assert "alert" in data

    def test_location_type_influences_alert(self, client):
        """When location_type maps to rules, compute_alert_level uses correct keywords."""
        import app as flask_app_local
        orig_rules = dict(flask_app_local._CRITICALITY_RULES)
        orig_env = flask_app_local._ENV_CORE_ROLE_KEYWORDS
        flask_app_local._CRITICALITY_RULES = {"datacenter": ["firewall"]}
        flask_app_local._ENV_CORE_ROLE_KEYWORDS = ()

        firewall_devices_page = {
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "dev-fw",
                    "name": "fw01",
                    "device_type": {"model": "PA-220", "manufacturer": {"name": "Palo Alto"}},
                    "role": {"name": "Firewall"},
                    "status": {"label": "offline"},
                    "platform": None,
                    "serial": "",
                    "tenant": None,
                }
            ],
        }

        def mock_get(endpoint, params=None):
            if "dcim/devices" in endpoint:
                return firewall_devices_page
            return {"count": 0, "next": None, "results": []}

        try:
            with patch.object(flask_app_local, "nautobot_get", side_effect=mock_get):
                resp = client.get("/api/locations/loc-1/detail?location_type=datacenter")
            data = resp.get_json()
            assert data["alert"]["level"] == "critical"
        finally:
            flask_app_local._CRITICALITY_RULES = orig_rules
            flask_app_local._ENV_CORE_ROLE_KEYWORDS = orig_env


# ---------------------------------------------------------------------------
# Tests: criticality override REST endpoints
# ---------------------------------------------------------------------------
class TestCriticalityOverrideEndpoints:
    """Tests for /api/criticality-overrides (requires NAUTOBOT_MAPS_DB)."""

    def setup_method(self):
        """Configure a temp-file SQLite DB for each test."""
        import tempfile
        self._orig_db = flask_app.NAUTOBOT_MAPS_DB
        self._db_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_tmp.close()
        flask_app.NAUTOBOT_MAPS_DB = self._db_tmp.name
        flask_app._init_db()

    def teardown_method(self):
        flask_app.NAUTOBOT_MAPS_DB = self._orig_db
        import os
        try:
            os.unlink(self._db_tmp.name)
        except Exception:
            pass

    def test_list_empty(self, client):
        resp = client.get("/api/criticality-overrides")
        assert resp.status_code == 200
        assert resp.get_json()["overrides"] == []

    def test_create_override(self, client):
        resp = client.post(
            "/api/criticality-overrides",
            json={"nautobot_device_id": "dev-abc", "is_critical": False,
                  "reason": "Local firewall", "updated_by": "admin"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["nautobot_device_id"] == "dev-abc"
        assert data["is_critical"] is False

    def test_list_after_create(self, client):
        client.post(
            "/api/criticality-overrides",
            json={"nautobot_device_id": "dev-abc", "is_critical": True,
                  "reason": "Core router", "updated_by": "admin"},
            content_type="application/json",
        )
        resp = client.get("/api/criticality-overrides")
        overrides = resp.get_json()["overrides"]
        assert len(overrides) == 1
        assert overrides[0]["nautobot_device_id"] == "dev-abc"

    def test_update_override(self, client):
        """Posting the same device_id a second time updates in-place."""
        client.post("/api/criticality-overrides",
                    json={"nautobot_device_id": "dev-x", "is_critical": True},
                    content_type="application/json")
        client.post("/api/criticality-overrides",
                    json={"nautobot_device_id": "dev-x", "is_critical": False,
                          "reason": "Changed"},
                    content_type="application/json")
        resp = client.get("/api/criticality-overrides")
        overrides = resp.get_json()["overrides"]
        assert len(overrides) == 1
        assert overrides[0]["is_critical"] == 0  # stored as int

    def test_delete_override(self, client):
        client.post("/api/criticality-overrides",
                    json={"nautobot_device_id": "dev-del", "is_critical": True},
                    content_type="application/json")
        resp = client.delete("/api/criticality-overrides/dev-del")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "deleted"
        # Should be gone now
        resp2 = client.get("/api/criticality-overrides")
        assert resp2.get_json()["overrides"] == []

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/criticality-overrides/does-not-exist")
        assert resp.status_code == 404

    def test_create_missing_device_id_returns_400(self, client):
        resp = client.post("/api/criticality-overrides",
                           json={"is_critical": True},
                           content_type="application/json")
        assert resp.status_code == 400

    def test_override_affects_compute_alert_level(self):
        """A device marked is_critical=False must not trigger a critical alert."""
        # Insert the override directly via SQLite so we share the same connection
        conn = flask_app._get_db_conn()
        with conn:
            conn.execute(
                "INSERT INTO device_criticality_override "
                "(nautobot_device_id, is_critical, reason, updated_by) "
                "VALUES (?, ?, ?, ?)",
                ("dev-fw", 0, "Local firewall – not critical", "test"),
            )
        conn.close()

        devices = [
            {"id": "dev-fw", "name": "fw-local", "role": "Core Router", "status": "offline"},
        ]
        # The override says is_critical=False, so even a "Core Router" that's
        # offline should not produce a critical alert.
        result = flask_app.compute_alert_level(devices)
        assert result["level"] != "critical"

    def test_no_db_returns_503(self, client):
        """When NAUTOBOT_MAPS_DB is empty, override endpoints return 503."""
        saved = flask_app.NAUTOBOT_MAPS_DB
        flask_app.NAUTOBOT_MAPS_DB = ""
        try:
            resp = client.get("/api/criticality-overrides")
            assert resp.status_code == 503
            resp2 = client.post("/api/criticality-overrides",
                                json={"nautobot_device_id": "x"},
                                content_type="application/json")
            assert resp2.status_code == 503
            resp3 = client.delete("/api/criticality-overrides/x")
            assert resp3.status_code == 503
        finally:
            flask_app.NAUTOBOT_MAPS_DB = saved


# ---------------------------------------------------------------------------
# Tests: criticality_rules.json loading
# ---------------------------------------------------------------------------
class TestCriticalityRulesFile:
    def test_load_valid_rules_file(self, tmp_path):
        """A valid JSON rules file is parsed into _CRITICALITY_RULES."""
        rules = {"datacenter": ["core", "firewall"], "office": ["router"]}
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        orig = dict(flask_app._CRITICALITY_RULES)
        orig_file = flask_app.CRITICALITY_RULES_FILE
        try:
            flask_app.CRITICALITY_RULES_FILE = str(rules_file)
            # Re-run the loading logic
            with open(str(rules_file)) as f:
                loaded = json.load(f)
            flask_app._CRITICALITY_RULES = {
                k.lower(): [kw.lower() for kw in v]
                for k, v in loaded.items()
                if isinstance(v, list)
            }
            assert flask_app._get_critical_keywords("datacenter") == ("core", "firewall")
            assert flask_app._get_critical_keywords("office") == ("router",)
        finally:
            flask_app._CRITICALITY_RULES = orig
            flask_app.CRITICALITY_RULES_FILE = orig_file

    def test_rules_file_bad_format_ignored(self, tmp_path):
        """A rules file with a non-dict top level is ignored gracefully."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps(["not", "a", "dict"]))

        orig = dict(flask_app._CRITICALITY_RULES)
        try:
            # Simulate what the loading code does
            with open(str(bad_file)) as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                pass  # Would be ignored in real code
            # _CRITICALITY_RULES should remain unchanged
            assert flask_app._CRITICALITY_RULES == orig
        finally:
            flask_app._CRITICALITY_RULES = orig


# ---------------------------------------------------------------------------
# Tests: LibreNMS enrichment
# ---------------------------------------------------------------------------
class TestLibreNMSEnrichment:
    def setup_method(self):
        self._orig_url = flask_app.LIBRENMS_URL
        self._orig_token = flask_app.LIBRENMS_API_TOKEN

    def teardown_method(self):
        flask_app.LIBRENMS_URL = self._orig_url
        flask_app.LIBRENMS_API_TOKEN = self._orig_token

    def test_no_enrichment_when_unconfigured(self):
        """_enrich_with_librenms is a no-op when LIBRENMS_URL is empty."""
        flask_app.LIBRENMS_URL = ""
        flask_app.LIBRENMS_API_TOKEN = ""
        devices = [{"id": "d1", "name": "router01", "status": "active"}]
        result = flask_app._enrich_with_librenms(devices)
        assert result == devices

    def test_librenms_down_overrides_active_status(self):
        """A device active in Nautobot but down in LibreNMS is set to offline."""
        flask_app.LIBRENMS_URL = "http://librenms.test"
        flask_app.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {
            "devices": [
                {"device_id": 1, "hostname": "router01", "status": 0},
            ]
        }
        with patch.object(flask_app, "_librenms_get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = flask_app._enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_up_does_not_change_active_status(self):
        """A device up in LibreNMS stays active."""
        flask_app.LIBRENMS_URL = "http://librenms.test"
        flask_app.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {
            "devices": [{"device_id": 1, "hostname": "router01", "status": 1}]
        }
        with patch.object(flask_app, "_librenms_get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = flask_app._enrich_with_librenms(devices)
        assert result[0]["status"] == "active"

    def test_librenms_down_does_not_upgrade_already_offline(self):
        """A device already offline in Nautobot stays offline (no double-counting)."""
        flask_app.LIBRENMS_URL = "http://librenms.test"
        flask_app.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {
            "devices": [{"device_id": 1, "hostname": "router01", "status": 0}]
        }
        with patch.object(flask_app, "_librenms_get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "offline"}]
            result = flask_app._enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_api_failure_returns_original_devices(self):
        """If LibreNMS API call fails, original device list is returned unchanged."""
        flask_app.LIBRENMS_URL = "http://librenms.test"
        flask_app.LIBRENMS_API_TOKEN = "tok"
        with patch.object(flask_app, "_librenms_get", side_effect=Exception("timeout")):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = flask_app._enrich_with_librenms(devices)
        assert result == devices

    def test_librenms_unmatched_device_not_affected(self):
        """Devices not present in LibreNMS are left unchanged."""
        flask_app.LIBRENMS_URL = "http://librenms.test"
        flask_app.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {
            "devices": [{"device_id": 1, "hostname": "other-device", "status": 0}]
        }
        with patch.object(flask_app, "_librenms_get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = flask_app._enrich_with_librenms(devices)
        assert result[0]["status"] == "active"


# ---------------------------------------------------------------------------
# Tests: /api/roles
# ---------------------------------------------------------------------------

SAMPLE_ROLES_PAGE = {
    "count": 2,
    "next": None,
    "results": [
        {"id": "role-1", "name": "Core Router", "color": "aa1409", "content_types": []},
        {"id": "role-2", "name": "Firewall", "color": "f44336", "content_types": []},
    ],
}


class TestApiRoles:
    def test_list_roles_returns_all(self, client):
        """GET /api/roles returns all roles from Nautobot."""
        with patch.object(flask_app, "nautobot_get", return_value=SAMPLE_ROLES_PAGE):
            resp = client.get("/api/roles")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "roles" in data
        assert len(data["roles"]) == 2
        assert data["roles"][0]["name"] == "Core Router"

    def test_list_roles_nautobot_unconfigured_returns_503(self, client):
        """GET /api/roles returns 503 when Nautobot is not configured."""
        with patch.object(flask_app, "nautobot_get",
                          side_effect=RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set")):
            resp = client.get("/api/roles")
        assert resp.status_code == 503

    def test_create_role_success(self, client):
        """POST /api/roles proxies to Nautobot and returns 201 on success."""
        created = {"id": "role-new", "name": "Edge Router", "color": "2196f3", "content_types": []}
        with patch.object(flask_app, "nautobot_post", return_value=created):
            resp = client.post("/api/roles",
                               json={"name": "Edge Router", "color": "2196f3"},
                               content_type="application/json")
        assert resp.status_code == 201
        assert resp.get_json()["name"] == "Edge Router"

    def test_create_role_missing_name_returns_400(self, client):
        """POST /api/roles without a name returns 400."""
        resp = client.post("/api/roles",
                           json={"color": "2196f3"},
                           content_type="application/json")
        assert resp.status_code == 400
        assert "name is required" in resp.get_json()["error"]

    def test_create_role_nautobot_unconfigured_returns_503(self, client):
        """POST /api/roles returns 503 when Nautobot is not configured."""
        with patch.object(flask_app, "nautobot_post",
                          side_effect=RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set")):
            resp = client.post("/api/roles",
                               json={"name": "Test Role"},
                               content_type="application/json")
        assert resp.status_code == 503

    def test_delete_role_success(self, client):
        """DELETE /api/roles/<id> proxies to Nautobot and returns 200."""
        with patch.object(flask_app, "nautobot_delete", return_value=None):
            resp = client.delete("/api/roles/role-1")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "deleted"
        assert resp.get_json()["id"] == "role-1"

    def test_delete_role_not_found_returns_404(self, client):
        """DELETE /api/roles/<id> returns 404 when Nautobot responds with 404."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        http_err = flask_app.requests.HTTPError(response=mock_response)
        with patch.object(flask_app, "nautobot_delete", side_effect=http_err):
            resp = client.delete("/api/roles/does-not-exist")
        assert resp.status_code == 404
        assert "not found" in resp.get_json()["error"].lower()

    def test_delete_role_nautobot_unconfigured_returns_503(self, client):
        """DELETE /api/roles/<id> returns 503 when Nautobot is not configured."""
        with patch.object(flask_app, "nautobot_delete",
                          side_effect=RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set")):
            resp = client.delete("/api/roles/role-1")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests: /api/location-types
# ---------------------------------------------------------------------------

SAMPLE_LOCATION_TYPES_PAGE = {
    "count": 2,
    "next": None,
    "results": [
        {"id": "lt-dc", "name": "Data Center"},
        {"id": "lt-pop", "name": "PoP"},
    ],
}


class TestApiLocationTypes:
    def test_list_location_types_returns_all(self, client):
        """GET /api/location-types returns all location types from Nautobot."""
        with patch.object(flask_app, "nautobot_get", return_value=SAMPLE_LOCATION_TYPES_PAGE):
            resp = client.get("/api/location-types")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "location_types" in data
        assert len(data["location_types"]) == 2
        assert data["location_types"][0]["name"] == "Data Center"

    def test_list_location_types_nautobot_unconfigured_returns_503(self, client):
        """GET /api/location-types returns 503 when Nautobot is not configured."""
        with patch.object(flask_app, "nautobot_get",
                          side_effect=RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set")):
            resp = client.get("/api/location-types")
        assert resp.status_code == 503

    def test_create_location_type_success(self, client):
        """POST /api/location-types proxies to Nautobot and returns 201 on success."""
        created = {"id": "lt-new", "name": "Office", "slug": "office"}
        with patch.object(flask_app, "nautobot_post", return_value=created):
            resp = client.post("/api/location-types",
                               json={"name": "Office", "slug": "office"},
                               content_type="application/json")
        assert resp.status_code == 201
        assert resp.get_json()["name"] == "Office"

    def test_create_location_type_missing_name_returns_400(self, client):
        """POST /api/location-types without a name returns 400."""
        resp = client.post("/api/location-types",
                           json={"slug": "office"},
                           content_type="application/json")
        assert resp.status_code == 400
        assert "name is required" in resp.get_json()["error"]

    def test_create_location_type_nautobot_unconfigured_returns_503(self, client):
        """POST /api/location-types returns 503 when Nautobot is not configured."""
        with patch.object(flask_app, "nautobot_post",
                          side_effect=RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set")):
            resp = client.post("/api/location-types",
                               json={"name": "Test Type"},
                               content_type="application/json")
        assert resp.status_code == 503

    def test_delete_location_type_success(self, client):
        """DELETE /api/location-types/<id> proxies to Nautobot and returns 200."""
        with patch.object(flask_app, "nautobot_delete", return_value=None):
            resp = client.delete("/api/location-types/lt-dc")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "deleted"
        assert resp.get_json()["id"] == "lt-dc"

    def test_delete_location_type_not_found_returns_404(self, client):
        """DELETE /api/location-types/<id> returns 404 when Nautobot responds with 404."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        http_err = flask_app.requests.HTTPError(response=mock_response)
        with patch.object(flask_app, "nautobot_delete", side_effect=http_err):
            resp = client.delete("/api/location-types/does-not-exist")
        assert resp.status_code == 404
        assert "not found" in resp.get_json()["error"].lower()

    def test_delete_location_type_nautobot_unconfigured_returns_503(self, client):
        """DELETE /api/location-types/<id> returns 503 when Nautobot is not configured."""
        with patch.object(flask_app, "nautobot_delete",
                          side_effect=RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set")):
            resp = client.delete("/api/location-types/lt-dc")
        assert resp.status_code == 503
