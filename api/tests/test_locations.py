"""TODO docstring (#786)."""

import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.clients import caching, nautobot
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from app.v1.map import controller as api_locations
from tests import db as adb
from tests import upstream
from tests.fakes import snapshot_device, snapshot_location

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
            "country": {"name": "Denmark"},
            "parent": {"name": "Denmark"},
            "latitude": "55.6761",
            "longitude": "12.5683",
            "description": "Main DC",
            "physical_address": "Somestreet 1, Copenhagen",
            "tenant": {
                "id": "ten-1",
                "name": "Acme Corp",
                "tenant_group": {"id": "tg-1", "name": "Corporate"},
            },
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
            "primary_ip4": {"address": "192.0.2.1/32"},
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
def mock_nautobot_get(endpoint, params=None, **kwargs):
    params = params or {}
    if "dcim/locations" in endpoint:
        return SAMPLE_LOCATIONS_PAGE
    if "dcim/devices" in endpoint:
        return SAMPLE_DEVICES_PAGE
    if "ipam/asns" in endpoint:
        return SAMPLE_ASNS_PAGE
    if "tenancy/tenant-groups" in endpoint:
        return {
            "count": 1,
            "next": None,
            "results": [{"id": "tg-1", "name": "Corporate"}],
        }
    if "tenancy/tenants" in endpoint:
        return {
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "ten-1",
                    "name": "Acme Corp",
                    "tenant_group": {"id": "tg-1", "name": "Corporate"},
                },
            ],
        }
    return {"count": 0, "next": None, "results": []}


# ---------------------------------------------------------------------------
# Tests: pagination helper
# ---------------------------------------------------------------------------


class TestPrimaryIpExtraction:
    def test_prefers_primary_ip4_host_before_address(self):
        device = {
            "primary_ip": None,
            "primary_ip4": {"host": "10.11.12.13", "address": "10.11.12.13/25"},
        }

        assert inventory.extract_primary_ip(device) == "10.11.12.13"

    def test_falls_back_to_primary_ip6_then_legacy_primary_ip(self):
        for device, expected in (
            (
                {
                    "primary_ip": "192.0.2.9/32",
                    "primary_ip4": {"host": "10.11.12.13"},
                    "primary_ip6": {"address": "2001:db8::1/64"},
                },
                "10.11.12.13",
            ),
            (
                {
                    "primary_ip": "192.0.2.9/32",
                    "primary_ip4": None,
                    "primary_ip6": {"address": "2001:db8::1/64"},
                },
                "2001:db8::1/64",
            ),
            (
                {"primary_ip": "192.0.2.9/32", "primary_ip4": None, "primary_ip6": None},
                "192.0.2.9/32",
            ),
        ):
            assert inventory.extract_primary_ip(device) == expected


# ---------------------------------------------------------------------------
# Tests: /api/locations
# ---------------------------------------------------------------------------
class TestApiLocations:
    def test_returns_locations_with_coordinates(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations")
        assert resp.status_code == 200
        data = resp.json()
        assert "locations" in data
        # loc-3 (no GPS) must be excluded
        assert len(data["locations"]) == 2

    def test_location_fields(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations")
        loc = resp.json()["locations"][0]
        assert loc["name"] == "Copenhagen DC"
        assert loc["latitude"] == 55.6761
        assert loc["longitude"] == 12.5683
        assert loc["tenant"] == "Acme Corp"
        assert loc["asn"] == 65001
        assert loc["status"] == "Active"

    def test_location_type_field_populated(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations")
        loc = resp.json()["locations"][0]
        assert loc["location_type"] == "Data Center"

    def test_country_field_populated(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations")
        loc = resp.json()["locations"][0]
        assert loc["country"] == "Denmark"

    def test_country_field_falls_back_to_physical_address(self, client):
        fallback_locations = {
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "loc-fallback-country",
                    "name": "Fallback Site",
                    "slug": "fallback-site",
                    "status": {"label": "Active"},
                    "location_type": {"name": "Office"},
                    "parent": None,
                    "latitude": "55.0",
                    "longitude": "12.0",
                    "description": "",
                    "physical_address": "Examplevej 10, 2100 Copenhagen, Denmark",
                    "tenant": None,
                    "asn": None,
                    "time_zone": "",
                    "facility": "",
                    "tags": [],
                    "url": "",
                },
            ],
        }

        def mock_get(endpoint, params=None, **kwargs):
            if "dcim/locations" in endpoint:
                return fallback_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(nautobot, "get", side_effect=mock_get):
            resp = client.get("/api/v1/locations")
        loc = resp.json()["locations"][0]
        assert loc["country"] == "Denmark"

    def test_parent_field_populated(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations")
        loc = resp.json()["locations"][0]
        assert loc["parent"] == "Denmark"

    def test_tenant_group_field_populated(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations")
        loc = resp.json()["locations"][0]
        assert loc["tenant_group"] == "Corporate"

    def test_tenant_group_empty_when_no_tenant(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations")
        # loc-2 (Aarhus PoP) has no tenant
        loc = resp.json()["locations"][1]
        assert loc["tenant_group"] == ""

    def test_facility_field_populated(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations")
        loc = resp.json()["locations"][0]
        assert loc["facility"] == "CPH-1"

    def test_facility_empty_when_not_set(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations")
        loc = resp.json()["locations"][1]
        assert loc["facility"] == ""

    def test_tags_field_populated(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations")
        loc = resp.json()["locations"][0]
        assert loc["tags"] == ["critical", "production"]

    def test_tags_empty_when_none(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations")
        loc = resp.json()["locations"][1]
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

        def mock_get(endpoint, params=None, **kwargs):
            if "extras/tags" in endpoint:
                return tags_page
            if "dcim/locations" in endpoint:
                return brief_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(nautobot, "get", side_effect=mock_get):
            resp = client.get("/api/v1/locations")
        loc = resp.json()["locations"][0]
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
                    "location_type": {
                        "id": "lt-dc",
                        "url": "http://nautobot/api/dcim/location-types/lt-dc/",
                    },
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

        def mock_get(endpoint, params=None, **kwargs):
            if "dcim/location-types" in endpoint:
                return lt_page
            if "dcim/locations" in endpoint:
                return brief_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(nautobot, "get", side_effect=mock_get):
            resp = client.get("/api/v1/locations")
        loc = resp.json()["locations"][0]
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
                    "parent": {
                        "id": "loc-parent",
                        "url": "http://nautobot/api/dcim/locations/loc-parent/",
                    },
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

        def mock_get(endpoint, params=None, **kwargs):
            if "dcim/locations" in endpoint:
                return brief_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(nautobot, "get", side_effect=mock_get):
            resp = client.get("/api/v1/locations")
        # loc-parent has no GPS (lat/lon=None) so only loc-child is returned
        locs = resp.json()["locations"]
        assert len(locs) == 1
        assert locs[0]["parent"] == "Denmark"

    def test_missing_env_vars_returns_503(self, client, override_settings):
        override_settings(NAUTOBOT_URL="", NAUTOBOT_TOKEN="")
        resp = client.get("/api/v1/locations")
        assert resp.status_code == 503
        assert resp.json()["error"] == "Nautobot service unavailable"

    def test_nautobot_http_error_returns_502(self, client):

        http_err = upstream.status_error(500)
        with patch.object(nautobot, "get", side_effect=http_err):
            resp = client.get("/api/v1/locations")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Tests: /api/locations/<id>/detail
# ---------------------------------------------------------------------------
class TestApiLocationDetail:
    def test_returns_devices_and_asns(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations/loc-1/detail")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["devices"]) == 1
        assert data["devices"][0]["name"] == "router01"
        assert len(data["asns"]) == 1
        assert data["asns"][0]["asn"] == 65001

    def test_device_fields(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations/loc-1/detail")
        dev = resp.json()["devices"][0]
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

        def mock_get(endpoint, params=None, **kwargs):
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

        with patch.object(nautobot, "get", side_effect=mock_get):
            resp = client.get("/api/v1/locations/loc-1/detail")
        assert resp.status_code == 200
        dev = resp.json()["devices"][0]
        # Every field must be a string (not None/null) so the JS escHtml()
        # function never receives null.
        for field in (
            "id",
            "name",
            "device_type",
            "manufacturer",
            "role",
            "status",
            "platform",
            "serial",
            "tenant",
        ):
            assert dev[field] is not None, f"device field '{field}' is None"
            assert isinstance(dev[field], str), f"device field '{field}' is not a string"

    def test_device_lookup_failure_returns_503(self, client):
        with patch.object(
            alerts, "get_location_devices_and_alert", side_effect=RuntimeError("lookup failed")
        ):
            resp = client.get("/api/v1/locations/loc-1/detail")

        assert resp.status_code == 503
        assert resp.json()["error"] == "Nautobot service unavailable"

    def test_asn_lookup_http_error_returns_502(self, client):
        http_err = upstream.status_error(502)
        with (
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=([], {"level": "ok", "reason": ""}),
            ),
            patch.object(nautobot, "fetch_all_pages", side_effect=http_err),
        ):
            resp = client.get("/api/v1/locations/loc-1/detail")

        assert resp.status_code == 502
        assert resp.json()["error"] == "Failed to communicate with Nautobot API"

    def _get_detail_with_asn_endpoint_missing(self, client, cached_locations, location_get):
        """Request a detail while ``ipam/asns/`` returns 404 (Nautobot 3.x core)."""
        not_found = upstream.status_error(404)
        with (
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=([], {"level": "ok", "reason": ""}),
            ),
            patch.object(nautobot, "fetch_all_pages", side_effect=not_found),
            patch.object(inventory, "read_locations", return_value=cached_locations),
            patch.object(nautobot, "get", side_effect=location_get) as mock_get,
        ):
            resp = client.get("/api/v1/locations/loc-1/detail")
        return resp, mock_get

    def test_missing_asn_endpoint_falls_back_to_location_asn(self, client):
        resp, mock_get = self._get_detail_with_asn_endpoint_missing(
            client, [], lambda endpoint, params=None: {"id": "loc-1", "asn": 65001}
        )
        assert resp.status_code == 200
        assert resp.json()["asns"] == [{"asn": 65001, "description": "", "tenant": ""}]
        mock_get.assert_called_once_with("dcim/locations/loc-1/")

    def test_missing_asn_endpoint_prefers_cached_location_asn(self, client):
        resp, mock_get = self._get_detail_with_asn_endpoint_missing(
            client,
            [{"id": "loc-2", "asn": 65002}, {"id": "loc-1", "asn": 65001}],
            AssertionError("should use the cached inventory"),
        )
        assert resp.status_code == 200
        assert resp.json()["asns"] == [{"asn": 65001, "description": "", "tenant": ""}]
        mock_get.assert_not_called()

    def test_missing_asn_endpoint_and_no_location_asn_returns_empty(self, client):
        resp, _ = self._get_detail_with_asn_endpoint_missing(
            client,
            [{"id": "loc-1", "asn": None}],
            AssertionError("should use the cached inventory"),
        )
        assert resp.status_code == 200
        assert resp.json()["asns"] == []

    def test_missing_asn_endpoint_still_fails_when_location_lookup_fails(self, client):
        upstream_err = upstream.status_error(500)
        resp, _ = self._get_detail_with_asn_endpoint_missing(client, [], upstream_err)
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Tests: /api/search
# ---------------------------------------------------------------------------
class TestApiSearch:
    def test_missing_query_returns_400(self, client):
        resp = client.get("/api/v1/search")
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_gps_coordinates_search(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            # Copenhagen coordinates – loc-1 is exactly at 55.6761,12.5683 (distance 0)
            resp = client.get("/api/v1/search?q=55.6761,12.5683")
        assert resp.status_code == 200
        data = resp.json()
        assert data["search_lat"] == pytest.approx(55.6761)
        assert data["search_lon"] == pytest.approx(12.5683)
        assert data["radius_km"] == 5
        # loc-1 is at the exact point
        names = [loc["name"] for loc in data["locations"]]
        assert "Copenhagen DC" in names

    def test_gps_no_results_far_away(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            # Tokyo – far from all test locations
            resp = client.get("/api/v1/search?q=35.6895,139.6917")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["locations"] == []

    def test_search_results_sorted_by_distance(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            # Point very close to loc-1 (within 5 km)
            resp = client.get("/api/v1/search?q=55.678,12.571")
        data = resp.json()
        distances = [loc["distance_km"] for loc in data["locations"]]
        assert distances == sorted(distances)

    def test_address_geocoding(self, client):
        mock_geo_result = MagicMock()
        mock_geo_result.latitude = 55.6761
        mock_geo_result.longitude = 12.5683
        mock_geolocator = MagicMock()
        mock_geolocator.geocode.return_value = mock_geo_result

        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            with patch("app.v1.map.controller.Nominatim", return_value=mock_geolocator):
                resp = client.get("/api/v1/search?q=Copenhagen")
        assert resp.status_code == 200
        data = resp.json()
        assert data["search_lat"] == pytest.approx(55.6761)

    def test_address_not_found_returns_404(self, client):
        mock_geolocator = MagicMock()
        mock_geolocator.geocode.return_value = None

        with patch("app.v1.map.controller.Nominatim", return_value=mock_geolocator):
            resp = client.get("/api/v1/search?q=ThisPlaceDoesNotExist12345")
        assert resp.status_code == 404
        assert "error" in resp.json()

    def test_distance_km_field_present(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/search?q=55.6761,12.5683")
        data = resp.json()
        for loc in data["locations"]:
            assert "distance_km" in loc


class TestNautobotRuntimeErrors:
    def test_runtime_errors_do_not_leak_internal_messages(self, client):
        secret = "NAUTOBOT_URL and NAUTOBOT_TOKEN must be set"
        cases = [
            ("get", "/api/v1/locations", (inventory, "get_locations"), {}),
            ("get", "/api/v1/locations/loc-1/detail", (alerts, "get_location_detail"), {}),
            ("get", "/api/v1/search?q=55.6761,12.5683", (inventory, "get_locations"), {}),
        ]

        for method, url, (module, name), kwargs in cases:
            with patch.object(module, name, side_effect=RuntimeError(secret)):
                resp = getattr(client, method)(url, **kwargs)

            assert resp.status_code == 503
            assert resp.json()["error"] == "Nautobot service unavailable"
            assert secret not in resp.text


# ---------------------------------------------------------------------------
# Tests: location_type passed through detail endpoint
# ---------------------------------------------------------------------------
class TestLocationDetailWithLocationType:
    def test_location_type_param_accepted(self, client):
        """The ?location_type query param is accepted without error."""
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/v1/locations/loc-1/detail?location_type=Data+Center")
        assert resp.status_code == 200
        data = resp.json()
        assert "devices" in data
        assert "alert" in data

    def test_location_type_influences_alert(self, client, override_settings):
        """When location_type maps to rules, compute_alert_level uses correct keywords."""
        override_settings(CRITICALITY_RULES={"datacenter": ["firewall"]})

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
                    # Monitored: only devices with a primary IP count (#316).
                    "primary_ip4": {"address": "10.0.0.1/24"},
                    "platform": None,
                    "serial": "",
                    "tenant": None,
                }
            ],
        }

        def mock_get(endpoint, params=None, **kwargs):
            if "dcim/devices" in endpoint:
                return firewall_devices_page
            return {"count": 0, "next": None, "results": []}

        with patch.object(nautobot, "get", side_effect=mock_get):
            resp = client.get("/api/v1/locations/loc-1/detail?location_type=datacenter")
        data = resp.json()
        assert data["alert"]["level"] == "critical"


# ---------------------------------------------------------------------------
# Tests: configurable geocoder, cache and rate limit (#197)
# ---------------------------------------------------------------------------
class TestGeocoder:
    @pytest.fixture(autouse=True)
    def _fresh_cache(self, monkeypatch):
        caching.cache.clear()
        monkeypatch.setattr(inventory, "get_locations", adb.async_of(lambda *a, **k: []))
        yield
        caching.cache.clear()

    def _geolocator(self, lat=55.68, lon=12.57):
        geolocator = MagicMock()
        geolocator.geocode.return_value = MagicMock(latitude=lat, longitude=lon)
        return geolocator

    def test_disabled_geocoder_only_accepts_coordinates(self, client, override_settings):
        override_settings(GEOCODER_ENABLED=False)
        with patch(
            "app.v1.map.controller.Nominatim", side_effect=AssertionError("must not geocode")
        ):
            resp = client.get("/api/v1/search?q=Copenhagen")
            assert resp.status_code == 400 and "coordinates" in resp.json()["error"]
            assert client.get("/api/v1/search?q=55.68,12.57").status_code == 200

    def test_uses_the_configured_service(self, client, override_settings):
        override_settings(
            GEOCODER_URL="http://nominatim.internal:8080/nominatim",
            GEOCODER_USER_AGENT="noc-maps/2",
        )
        with patch("app.v1.map.controller.Nominatim", return_value=self._geolocator()) as nominatim:
            assert client.get("/api/v1/search?q=Copenhagen").status_code == 200
        nominatim.assert_called_once_with(
            user_agent="noc-maps/2", domain="nominatim.internal:8080/nominatim", scheme="http"
        )

    def test_results_are_cached(self, client):
        geolocator = self._geolocator()
        with patch("app.v1.map.controller.Nominatim", return_value=geolocator):
            first = client.get("/api/v1/search?q=Copenhagen").json()
            caching.cache.delete("geocode-rate-limit")
            second = client.get("/api/v1/search?q=copenhagen ").json()
        assert geolocator.geocode.call_count == 1
        assert first["search_lat"] == second["search_lat"] == pytest.approx(55.68)

    def test_at_most_one_request_per_second(self, client, monkeypatch):
        # A minute, not a second: a slow CI runner took over a second between
        # the two requests, and the second one was let through (#332).
        monkeypatch.setattr(api_locations, "GEOCODE_MIN_INTERVAL_SECONDS", 60)
        with patch("app.v1.map.controller.Nominatim", return_value=self._geolocator()):
            assert client.get("/api/v1/search?q=Copenhagen").status_code == 200
            busy = client.get("/api/v1/search?q=Aarhus")
            assert busy.status_code == 429 and "try again" in busy.json()["error"]
            # Once the interval is over (its key expired), the next search is asked.
            caching.cache.delete("geocode-rate-limit")
            assert client.get("/api/v1/search?q=Aarhus").status_code == 200

    def test_the_interval_is_one_second(self):
        assert api_locations.GEOCODE_MIN_INTERVAL_SECONDS == 1

    def test_geocoder_error_is_not_passed_to_the_client(self, client):
        geolocator = MagicMock()
        geolocator.geocode.side_effect = OSError("connect to 10.0.0.5 refused")
        with patch("app.v1.map.controller.Nominatim", return_value=geolocator):
            resp = client.get("/api/v1/search?q=Copenhagen")
        assert resp.status_code == 503
        assert resp.json() == {"error": "Geocoding service unavailable"}

    def test_not_found_is_cached_too(self, client):
        geolocator = MagicMock()
        geolocator.geocode.return_value = None
        with patch("app.v1.map.controller.Nominatim", return_value=geolocator):
            assert client.get("/api/v1/search?q=Nowhere").status_code == 404
            caching.cache.delete("geocode-rate-limit")
            assert client.get("/api/v1/search?q=Nowhere").status_code == 404
        assert geolocator.geocode.call_count == 1


class TestLocationAlertLevels:
    """/api/v1/location-alerts colours the map markers without a click (#234)."""

    @pytest.fixture(autouse=True)
    def _database(self, override_settings):
        override_settings(
            NAUTOBOT_URL="", NAUTOBOT_TOKEN="", LIBRENMS_URL="", LIBRENMS_API_TOKEN=""
        )
        adb.call(
            inventory.write_locations,
            [
                snapshot_location("loc-core", "Core down"),
                snapshot_location("loc-few", "One of five down"),
                snapshot_location("loc-ok", "All up"),
                snapshot_location("loc-empty", "No devices"),
            ],
        )
        adb.call(
            inventory.write_devices,
            [
                snapshot_device("core-1", "loc-core", "Core Router", "Offline"),
                snapshot_device("acc-1", "loc-core", "Access Switch", "Active"),
                snapshot_device("few-1", "loc-few", "Access Switch", "Offline"),
                *[
                    snapshot_device(f"few-{n}", "loc-few", "Access Switch", "Active")
                    for n in range(2, 6)
                ],
                snapshot_device("ok-1", "loc-ok", "Access Switch", "Active"),
            ],
        )
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at="2026-01-01T00:00:00Z",
            last_completed_at="2026-01-01T00:01:00Z",
            last_successful_sync="2026-01-01T00:01:00Z",
            status="success",
            error_message="",
        )
        caching.cache.clear()

    def test_returns_every_location_with_monitored_devices(self, client):
        """OK too, so the map tells OK (green) from no data (grey) (#311)."""
        with patch.object(
            nautobot, "fetch_all_pages", side_effect=AssertionError("no upstream calls")
        ):
            resp = client.get("/api/v1/location-alerts")
        assert resp.status_code == 200
        levels = resp.json()["levels"]
        # With the counts the board shows, "N down · M monitored" (#316).
        assert levels == {
            "loc-core": {
                "level": "critical",
                "reason": "Core device(s) offline: core-1",
                "down": 1,
                "monitored": 2,
            },
            "loc-few": {
                "level": "low",
                "reason": "1/5 devices offline (20%)",
                "down": 1,
                "monitored": 5,
            },
            "loc-ok": {"level": "ok", "reason": "", "down": 0, "monitored": 1},
            # Grey, as on the board: copied too, so the panel doesn't score it itself.
            "loc-empty": {
                "level": "no_data",
                "reason": "No monitored devices",
                "down": 0,
                "monitored": 0,
            },
        }

    def test_matches_the_site_panel(self, client):
        levels = client.get("/api/v1/location-alerts").json()["levels"]
        with patch.object(nautobot, "fetch_all_pages", return_value=[]):
            for location_id in ("loc-core", "loc-few", "loc-ok"):
                detail = asyncio.run(
                    alerts.get_location_detail(location_id, location_type="Data Center")
                )
                expected = levels.get(location_id, {}).get("level", "ok")
                assert detail["alert"]["level"] == expected

    def test_the_site_panel_marks_core_devices(self, client):
        """Core devices first in the panel's down list (#394): by role, or an override."""
        with patch.object(nautobot, "fetch_all_pages", return_value=[]):
            detail = asyncio.run(
                alerts.get_location_detail("loc-core", location_type="Data Center")
            )
        assert {d["name"]: d["core"] for d in detail["devices"]} == {"core-1": True, "acc-1": False}
        # And the level each gives the site, as the board weighs it (#377).
        assert {d["name"]: d["level"] for d in detail["devices"]} == {
            "core-1": "critical",
            "acc-1": "",
        }
        adb.sql(
            "INSERT INTO device_criticality_override (nautobot_device_id, is_critical, updated_by, updated_at)"
            " VALUES ('few-1', 1, 'test', '2026-01-01T00:00:00Z')"
        )
        with patch.object(nautobot, "fetch_all_pages", return_value=[]):
            detail = asyncio.run(alerts.get_location_detail("loc-few", location_type="Data Center"))
        assert [d["id"] for d in detail["devices"] if d["core"]] == ["few-1"]

    def test_criticality_override_is_applied(self, client):
        adb.sql(
            "INSERT INTO device_criticality_override (nautobot_device_id, is_critical, updated_by, updated_at)"
            " VALUES ('few-1', 1, 'test', '2026-01-01T00:00:00Z')"
        )
        assert (
            client.get("/api/v1/location-alerts").json()["levels"]["loc-few"]["level"] == "critical"
        )

    def test_cached_until_a_sync_invalidates_it(self, client):
        assert client.get("/api/v1/location-alerts").json()["levels"]["loc-ok"]["level"] == "ok"
        adb.sql("UPDATE nautobot_device_cache SET status = 'Offline' WHERE device_id = 'ok-1'")
        assert client.get("/api/v1/location-alerts").json()["levels"]["loc-ok"]["level"] == "ok", (
            "cached"
        )
        caching.invalidate_alert_board()
        assert (
            client.get("/api/v1/location-alerts").json()["levels"]["loc-ok"]["level"] == "critical"
        )


SAMPLE_CIRCUIT_TERMINATIONS = [
    {
        "id": "term-2",
        "circuit": {
            "id": "cir-2",
            "cid": "GTT-7781",
            "provider": {"id": "p2", "name": "GTT"},
            "circuit_type": {"id": "t2", "name": "MPLS"},
            "status": {"id": "s2", "name": "Offline"},
            "tenant": None,
            "commit_rate": 1000000,
        },
        "term_side": "Z",
        "port_speed": 1000000,
        "upstream_speed": 500000,
        "xconnect_id": "",
        "pp_info": "",
        "description": "",
    },
    {
        "id": "term-1",
        "circuit": {
            "id": "cir-1",
            "cid": "TEL-0001",
            "provider": {"id": "p1", "display": "Telia"},
            "circuit_type": {"id": "t1", "name": "Transit"},
            "status": {"id": "s1", "name": "Active"},
            "tenant": {"id": "ten-1", "name": "Acme Corp"},
            "commit_rate": None,
        },
        "term_side": "A",
        "port_speed": 10000000,
        "upstream_speed": None,
        "xconnect_id": "XC-1",
        "pp_info": "PP-02 port 7",
        "description": "Primary transit",
    },
]


class TestLocationCircuits:
    """The map's site panel lists every circuit at the location (#235)."""

    def _mock(self, terminations=None, error=None):
        def fetch(endpoint, params=None, **kwargs):
            if endpoint.startswith("circuits/"):
                if error is not None:
                    raise error
                assert params["location"] == "loc-1"
                assert params["depth"] == 2
                return terminations or []
            return [page for page in mock_nautobot_get(endpoint, params)["results"]]

        return patch.object(nautobot, "fetch_all_pages", side_effect=fetch)

    def test_every_circuit_is_listed(self, client):
        with self._mock(SAMPLE_CIRCUIT_TERMINATIONS):
            data = client.get("/api/v1/locations/loc-1/detail").json()
        assert data["circuits_error"] == ""
        assert [c["cid"] for c in data["circuits"]] == ["GTT-7781", "TEL-0001"]
        assert data["circuits"][1] == {
            "id": "cir-1",
            "cid": "TEL-0001",
            "provider": "Telia",
            "circuit_type": "Transit",
            "status": "Active",
            "tenant": "Acme Corp",
            "commit_rate": "",
            "term_side": "A",
            "port_speed": "10 Gbps",
            "upstream_speed": "",
            "xconnect_id": "XC-1",
            "pp_info": "PP-02 port 7",
            "description": "Primary transit",
        }
        assert data["circuits"][0]["status"] == "Offline"
        assert data["circuits"][0]["upstream_speed"] == "500 Mbps"
        assert data["devices"] and data["asns"]

    def test_no_circuits_app_is_not_an_error(self, client):
        with self._mock(error=upstream.status_error(404)):
            data = client.get("/api/v1/locations/loc-1/detail").json()
        assert data["circuits"] == []
        assert data["circuits_error"] == ""

    def test_a_failed_call_keeps_the_rest_of_the_panel(self, client):
        with self._mock(error=httpx.ConnectError("boom")):
            resp = client.get("/api/v1/locations/loc-1/detail")
        assert resp.status_code == 200
        data = resp.json()
        assert data["circuits"] == []
        assert data["circuits_error"] == "Circuit information unavailable"
        assert "boom" not in resp.text
        assert data["devices"]

    @pytest.mark.parametrize(
        ("kbps", "expected"),
        [
            (None, ""),
            (0, ""),
            ("bad", ""),
            (512, "512 Kbps"),
            (1500, "1.5 Mbps"),
            (100000, "100 Mbps"),
            (10000000, "10 Gbps"),
            (2500000, "2.5 Gbps"),
        ],
    )
    def test_circuit_speed(self, kbps, expected):
        assert inventory.circuit_speed(kbps) == expected


LOCATION_TENANT_RELATIONSHIPS = [
    {
        "id": "rel-1",
        "key": "site_customers",
        "label": "Site customers",
        "source_type": "dcim.location",
        "destination_type": "tenancy.tenant",
    },
    # The other direction counts too.
    {
        "id": "rel-2",
        "key": "hosted_at",
        "label": "Hosted at",
        "source_type": "tenancy.tenant",
        "destination_type": "dcim.location",
    },
    {
        "id": "rel-3",
        "key": "backup",
        "label": "Backup",
        "source_type": "dcim.device",
        "destination_type": "dcim.device",
    },
]


RELATIONSHIP_ASSOCIATIONS = {
    "rel-1": [
        {"source_id": "loc-1", "destination_id": "ten-2"},
        {"source_id": "loc-1", "destination_id": "ten-3"},
    ],
    "rel-2": [{"source_id": "ten-3", "destination_id": "loc-2"}],
}


TENANT_NAMES = {"ten-1": "Acme Corp", "ten-2": "Nordic Net", "ten-3": "EuroIX"}


# Nautobot tenant descriptions (#263); EuroIX has none.
TENANT_DESCRIPTIONS = {"ten-1": "Bank, 24/7 SLA", "ten-2": "  Shared rack  "}


def _relationship_fetch(error_on=None):
    calls = []

    def fetch(endpoint, params=None, **kwargs):
        calls.append((endpoint, dict(params or {}), kwargs.get("use_cache", True)))
        if error_on and endpoint.startswith(error_on):
            raise upstream.status_error(403)
        if endpoint == "extras/relationships/":
            return LOCATION_TENANT_RELATIONSHIPS
        if endpoint == "extras/relationship-associations/":
            return RELATIONSHIP_ASSOCIATIONS.get(params["relationship"], [])
        if endpoint == "tenancy/tenants/":
            return [
                {"id": tid, "name": name, "description": TENANT_DESCRIPTIONS.get(tid, "")}
                for tid, name in TENANT_NAMES.items()
            ]
        return []

    return fetch, calls


class TestLocationTenantRelationships:
    """Every tenant of a site, from Nautobot relationships (#238)."""

    def test_both_directions_of_location_tenant_relationships(self, monkeypatch):
        fetch, calls = _relationship_fetch()
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        links = inventory.fetch_location_tenant_links()
        assert sorted(
            (link["location_id"], link["tenant"], link["relationship"]) for link in links
        ) == [
            ("loc-1", "EuroIX", "Site customers"),
            ("loc-1", "Nordic Net", "Site customers"),
            ("loc-2", "EuroIX", "Hosted at"),
        ]
        # Only Location <-> Tenant relationships are read, never from the cache.
        read = [
            (endpoint, params.get("relationship"))
            for endpoint, params, _ in calls
            if "associations" in endpoint
        ]
        assert read == [
            ("extras/relationship-associations/", "rel-1"),
            ("extras/relationship-associations/", "rel-2"),
        ]
        assert all(
            use_cache is False for endpoint, _, use_cache in calls if endpoint.startswith("extras/")
        )

    @pytest.mark.parametrize("wanted", ["site_customers", "site customers"])
    def test_setting_narrows_by_key_or_label(self, monkeypatch, wanted, override_settings):
        fetch, _ = _relationship_fetch()
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        override_settings(SITE_TENANT_RELATIONSHIPS={wanted})
        links = inventory.fetch_location_tenant_links()
        assert {link["relationship"] for link in links} == {"Site customers"}

    def test_no_relationship_is_an_empty_list(self, monkeypatch, override_settings):
        fetch, _ = _relationship_fetch()
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        override_settings(SITE_TENANT_RELATIONSHIPS={"nothing-matches"})
        assert inventory.fetch_location_tenant_links() == []

    def test_a_failed_read_is_none(self, monkeypatch):
        fetch, _ = _relationship_fetch(error_on="extras/relationship-associations/")
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        assert inventory.fetch_location_tenant_links() is None

    def test_site_tenants_puts_its_own_tenant_first(self):
        assert inventory.site_tenants("Acme", ["Beta", "Acme", "Gamma"]) == [
            "Acme",
            "Beta",
            "Gamma",
        ]
        assert inventory.site_tenants("", ["Beta"]) == ["Beta"]
        assert inventory.site_tenants("", []) == []


class TestLocationTenantSync:
    @pytest.fixture(autouse=True)
    def _database(self, override_settings):
        override_settings(
            NAUTOBOT_URL="https://nautobot.example.com",
            NAUTOBOT_TOKEN="token",
            LIBRENMS_URL="",
            LIBRENMS_API_TOKEN="",
        )

    def _sync(self, monkeypatch, error_on=None):
        fetch, _ = _relationship_fetch(error_on)

        def with_inventory(endpoint, params=None, **kwargs):
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "London",
                        "tenant": {"id": "ten-1", "name": "Acme Corp"},
                        "latitude": "51.5",
                        "longitude": "-0.1",
                        "location_type": {"id": "lt", "name": "Data Center"},
                    }
                ]
            if endpoint == "dcim/devices/":
                return [
                    {
                        "id": "dev-1",
                        "name": "lon-core",
                        "location": {"id": "loc-1"},
                        "role": {"name": "Core Router"},
                        "status": {"name": "Offline"},
                        "primary_ip4": {"address": "10.0.0.1/32"},
                    }
                ]
            return fetch(endpoint, params, **kwargs)

        monkeypatch.setattr(nautobot, "fetch_all_pages", with_inventory)
        asyncio.run(inventory.sync_nautobot(force=True))
        caching.cache.clear()

    def test_sync_fills_the_board_and_the_map(self, client, monkeypatch):
        self._sync(monkeypatch)
        (site,) = client.get("/api/v1/alerts").json()["alerts"]
        assert site["tenants"] == ["Acme Corp", "EuroIX", "Nordic Net"]
        (location,) = client.get("/api/v1/locations").json()["locations"]
        assert location["tenants"] == ["Acme Corp", "EuroIX", "Nordic Net"]

    def test_a_failed_read_keeps_the_links_a_removed_one_goes(self, client, monkeypatch):
        self._sync(monkeypatch)
        self._sync(monkeypatch, error_on="extras/relationships/")
        (site,) = client.get("/api/v1/alerts").json()["alerts"]
        assert site["tenants"] == ["Acme Corp", "EuroIX", "Nordic Net"]

        monkeypatch.setitem(
            RELATIONSHIP_ASSOCIATIONS, "rel-1", [{"source_id": "loc-1", "destination_id": "ten-2"}]
        )
        self._sync(monkeypatch)
        (site,) = client.get("/api/v1/alerts").json()["alerts"]
        assert site["tenants"] == ["Acme Corp", "Nordic Net"]


class TestTenantDescriptions:
    """The tenant (i) shows the Nautobot description (#263)."""

    @pytest.fixture(autouse=True)
    def _sync_setup(self, override_settings):
        override_settings(
            NAUTOBOT_URL="https://nautobot.example.com",
            NAUTOBOT_TOKEN="token",
            LIBRENMS_URL="",
            LIBRENMS_API_TOKEN="",
        )

    _sync = TestLocationTenantSync._sync

    def test_fetch_reads_every_tenant_fresh(self, monkeypatch):
        fetch, calls = _relationship_fetch()
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        tenants = {tenant["name"]: tenant["description"] for tenant in inventory.fetch_tenants()}
        assert tenants == {"Acme Corp": "Bank, 24/7 SLA", "Nordic Net": "Shared rack", "EuroIX": ""}
        assert ("tenancy/tenants/", {}, False) in calls

    def test_fetch_failure_is_none(self, monkeypatch):
        fetch, _ = _relationship_fetch(error_on="tenancy/tenants/")
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        assert inventory.fetch_tenants() is None

    def test_board_and_map_get_the_descriptions_of_their_tenants(self, client, monkeypatch):
        self._sync(monkeypatch)
        (site,) = client.get("/api/v1/alerts").json()["alerts"]
        # Its own tenant and a related one; EuroIX has no description, so no entry.
        assert site["tenant_descriptions"] == {
            "Acme Corp": "Bank, 24/7 SLA",
            "Nordic Net": "Shared rack",
        }
        (location,) = client.get("/api/v1/locations").json()["locations"]
        assert location["tenant_descriptions"] == site["tenant_descriptions"]

    def test_an_edited_description_replaces_the_old_one(self, client, monkeypatch):
        self._sync(monkeypatch)
        monkeypatch.setitem(TENANT_DESCRIPTIONS, "ten-1", "Bank, business hours")
        monkeypatch.delitem(TENANT_DESCRIPTIONS, "ten-2")
        self._sync(monkeypatch)
        (location,) = client.get("/api/v1/locations").json()["locations"]
        assert location["tenant_descriptions"] == {"Acme Corp": "Bank, business hours"}

    def test_map_without_a_database_reads_them_from_nautobot(self, monkeypatch):
        """Map-only setups (no PostgreSQL) get the (i) too (Copilot review on #264)."""
        fetch, _ = _relationship_fetch()

        def live(endpoint, params=None, **kwargs):
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "London",
                        "tenant": {"id": "ten-1"},
                        "latitude": "51.5",
                        "longitude": "-0.1",
                    },
                    {
                        "id": "loc-2",
                        "name": "Paris",
                        "tenant": {"id": "ten-3"},
                        "latitude": "48.8",
                        "longitude": "2.3",
                    },
                ]
            return fetch(endpoint, params, **kwargs)

        monkeypatch.setattr(inventory, "read_locations", adb.async_of(lambda **kwargs: []))
        monkeypatch.setattr(
            inventory, "ensure_snapshot", adb.async_of(lambda *args, **kwargs: False)
        )
        monkeypatch.setattr(nautobot, "fetch_all_pages", live)
        by_name = {loc["name"]: loc for loc in asyncio.run(inventory.get_locations())}
        assert by_name["London"]["tenant_descriptions"] == {"Acme Corp": "Bank, 24/7 SLA"}
        assert by_name["Paris"]["tenant_descriptions"] == {}  # EuroIX has no description

    def test_a_failed_read_keeps_the_descriptions(self, monkeypatch):
        self._sync(monkeypatch)
        monkeypatch.setattr(inventory, "fetch_tenants", lambda: None)
        self._sync(monkeypatch)
        assert asyncio.run(inventory.read_tenant_descriptions()) == {
            "Acme Corp": "Bank, 24/7 SLA",
            "Nordic Net": "Shared rack",
        }
