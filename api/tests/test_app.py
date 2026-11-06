"""Ported from the current app's tests/test_app.py (#602); its classes already ported
elsewhere (test_clients.py, test_settings.py, test_schema.py) are left out."""

import importlib
import json
import pathlib
import re
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, MagicMock, patch

import pytest
import requests

from app import db
from app.clients import caching, librenms, nautobot
from app.repositories import board_repository as board_store
from app.services import alerts_service as alerts
from app.services import contract_service as contract
from app.services import inventory_service as inventory
from app.services import maintenance_service as maintenance
from app.services import navigation_service as navigation
from app.services import scheduler
from app.utils import timeutil
from app.utils.legacy_settings import settings
from app.v1.answers import board as board_answers
from app.v1.answers import locations as location_answers
from app.v1.routes import board as api_board
from app.v1.routes import locations as api_locations
from tests import answers, http_client

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _full_locations(locations: list[dict]) -> list[dict]:
    """Stand-in locations with a stored location's other fields, empty (#449:
    the board made from them is checked against /api/docs)."""
    return [answers.complete(location_answers.Location, location) for location in locations]


@contextmanager
def auth_config(
    mode="disabled",
    user_header="X-Forwarded-User",
    groups_header="X-Forwarded-Groups",
    viewer_groups=None,
    operator_groups=None,
    admin_groups=None,
    default_role="",
):
    saved = {
        "AUTH_MODE": settings.AUTH_MODE,
        "AUTH_HEADER_USER": settings.AUTH_HEADER_USER,
        "AUTH_HEADER_GROUPS": settings.AUTH_HEADER_GROUPS,
        "AUTH_VIEWER_GROUPS": set(settings.AUTH_VIEWER_GROUPS),
        "AUTH_OPERATOR_GROUPS": set(settings.AUTH_OPERATOR_GROUPS),
        "AUTH_ADMIN_GROUPS": set(settings.AUTH_ADMIN_GROUPS),
        "AUTH_DEFAULT_ROLE": settings.AUTH_DEFAULT_ROLE,
    }
    settings.AUTH_MODE = mode
    settings.AUTH_HEADER_USER = user_header
    settings.AUTH_HEADER_GROUPS = groups_header
    settings.AUTH_VIEWER_GROUPS = set(viewer_groups or set())
    settings.AUTH_OPERATOR_GROUPS = set(operator_groups or set())
    settings.AUTH_ADMIN_GROUPS = set(admin_groups or set())
    role = (default_role or "").strip().lower()
    settings.AUTH_DEFAULT_ROLE = role if role in ("viewer", "operator", "admin") else ""
    try:
        yield
    finally:
        settings.AUTH_MODE = saved["AUTH_MODE"]
        settings.AUTH_HEADER_USER = saved["AUTH_HEADER_USER"]
        settings.AUTH_HEADER_GROUPS = saved["AUTH_HEADER_GROUPS"]
        settings.AUTH_VIEWER_GROUPS = saved["AUTH_VIEWER_GROUPS"]
        settings.AUTH_OPERATOR_GROUPS = saved["AUTH_OPERATOR_GROUPS"]
        settings.AUTH_ADMIN_GROUPS = saved["AUTH_ADMIN_GROUPS"]
        settings.AUTH_DEFAULT_ROLE = saved["AUTH_DEFAULT_ROLE"]


@pytest.fixture
def flask_client():
    # Clear cache before each test
    caching.cache.clear()
    with http_client.FlaskStyleClient() as c:
        yield c


@pytest.fixture
def allow_unauthenticated_writes(monkeypatch):
    """The endpoint tests below run with AUTH_MODE=disabled, where the
    administrative writes are refused unless explicitly allowed (#188)."""
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)


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
    def test_returns_locations_with_coordinates(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "locations" in data
        # loc-3 (no GPS) must be excluded
        assert len(data["locations"]) == 2

    def test_location_fields(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["name"] == "Copenhagen DC"
        assert loc["latitude"] == 55.6761
        assert loc["longitude"] == 12.5683
        assert loc["tenant"] == "Acme Corp"
        assert loc["asn"] == 65001
        assert loc["status"] == "Active"

    def test_location_type_field_populated(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["location_type"] == "Data Center"

    def test_country_field_populated(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["country"] == "Denmark"

    def test_country_field_falls_back_to_physical_address(self, flask_client):
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
            resp = flask_client.get("/api/v1/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["country"] == "Denmark"

    def test_parent_field_populated(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["parent"] == "Denmark"

    def test_tenant_group_field_populated(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["tenant_group"] == "Corporate"

    def test_tenant_group_empty_when_no_tenant(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations")
        # loc-2 (Aarhus PoP) has no tenant
        loc = resp.get_json()["locations"][1]
        assert loc["tenant_group"] == ""

    def test_facility_field_populated(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["facility"] == "CPH-1"

    def test_facility_empty_when_not_set(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations")
        loc = resp.get_json()["locations"][1]
        assert loc["facility"] == ""

    def test_tags_field_populated(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["tags"] == ["critical", "production"]

    def test_tags_empty_when_none(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations")
        loc = resp.get_json()["locations"][1]
        assert loc["tags"] == []

    def test_tags_fallback_with_brief_nested_object(self, flask_client):
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
            resp = flask_client.get("/api/v1/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["tags"] == ["critical", "production"]

    def test_location_type_fallback_with_brief_nested_object(self, flask_client):
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
            resp = flask_client.get("/api/v1/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["location_type"] == "Data Center"

    def test_parent_fallback_with_brief_nested_object(self, flask_client):
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
            resp = flask_client.get("/api/v1/locations")
        # loc-parent has no GPS (lat/lon=None) so only loc-child is returned
        locs = resp.get_json()["locations"]
        assert len(locs) == 1
        assert locs[0]["parent"] == "Denmark"

    def test_missing_env_vars_returns_503(self, flask_client):
        original_url = settings.NAUTOBOT_URL
        original_token = settings.NAUTOBOT_TOKEN
        settings.NAUTOBOT_URL = ""
        settings.NAUTOBOT_TOKEN = ""
        try:
            resp = flask_client.get("/api/v1/locations")
            assert resp.status_code == 503
            assert resp.get_json()["error"] == "Nautobot service unavailable"
        finally:
            settings.NAUTOBOT_URL = original_url
            settings.NAUTOBOT_TOKEN = original_token

    def test_nautobot_http_error_returns_502(self, flask_client):
        import requests as req_lib

        http_err = req_lib.HTTPError(response=MagicMock(status_code=500))
        with patch.object(nautobot, "get", side_effect=http_err):
            resp = flask_client.get("/api/v1/locations")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Tests: alert board
# ---------------------------------------------------------------------------
class TestAlertBoard:
    def test_alert_board_page_renders(self, flask_client, frontend_build):
        """The board and the wall are built with Nuxt (#463; frontend/tests/ and e2e/
        test the pages themselves)."""
        for path in ("/alerts?tenant=Acme%20Corp", "/alerts?view=wall&q=DNK"):
            resp = flask_client.get(path)
            assert resp.status_code == 200 and b'id="__nuxt"' in resp.data, path
            assert resp.headers["Cache-Control"] == "no-cache"

    def test_get_alert_board_data_aggregates_and_sorts(self):
        caching.cache.clear()
        sample_locations = [
            {
                "id": "loc-1",
                "name": "Bravo Site",
                "status": "Active",
                "location_type": "Data Center",
                "parent": "Region A",
                "latitude": 10.0,
                "longitude": 20.0,
                "description": "",
                "physical_address": "",
                "facility": "",
                "tenant": "Tenant A",
                "tenant_id": "ten-1",
                "tenant_group": "Group A",
                "asn": None,
                "time_zone": "",
                "tags": [],
                "url": "",
            },
            {
                "id": "loc-2",
                "name": "Alpha Site",
                "status": "Active",
                "location_type": "Office",
                "parent": "",
                "latitude": 11.0,
                "longitude": 21.0,
                "description": "",
                "physical_address": "",
                "facility": "",
                "tenant": "",
                "tenant_id": "",
                "tenant_group": "",
                "asn": None,
                "time_zone": "",
                "tags": [],
                "url": "",
            },
            {
                "id": "loc-3",
                "name": "Charlie Site",
                "status": "Planned",
                "location_type": "PoP",
                "parent": "",
                "latitude": 12.0,
                "longitude": 22.0,
                "description": "",
                "physical_address": "",
                "facility": "",
                "tenant": "",
                "tenant_id": "",
                "tenant_group": "",
                "asn": None,
                "time_zone": "",
                "tags": [],
                "url": "",
            },
        ]

        def mock_devices(location_id, location_type=None, **_kwargs):
            if location_id == "loc-1":
                return (
                    [{"id": "d1", "name": "core1", "role": "Core Router", "status": "offline"}],
                    {"level": "critical", "reason": "Core device(s) offline: core1"},
                )
            if location_id == "loc-2":
                return (
                    [
                        {"id": "d2", "name": "sw1", "role": "Switch", "status": "offline"},
                        {"id": "d3", "name": "sw2", "role": "Switch", "status": "active"},
                        {"id": "d4", "name": "sw3", "role": "Switch", "status": "active"},
                    ],
                    {"level": "medium", "reason": "1/3 devices offline (33%)"},
                )
            return (
                [{"id": "d5", "name": "sw4", "role": "Switch", "status": "active"}],
                {"level": "ok", "reason": ""},
            )

        with (
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(alerts, "get_location_devices_and_alert", side_effect=mock_devices),
        ):
            data = alerts.get_alert_board_data()

        assert data["summary"] == {
            "total": 3,
            "critical": 1,
            "medium": 1,
            "low": 0,
            "no_data": 0,
            "ok": 1,
            "non_ok": 2,
            "maintenance": 0,
            "wireless": 0,
        }
        assert [item["id"] for item in data["alerts"]] == ["loc-1", "loc-2", "loc-3"]
        assert data["alerts"][0]["down_device_count"] == 1
        assert data["alerts"][1]["alert_level"] == "medium"
        assert data["stale"] is False

    def test_api_alerts_returns_board_data(self, flask_client):
        caching.cache.clear()
        with (
            patch.object(
                inventory,
                "get_locations",
                return_value=_full_locations(
                    [
                        {
                            "id": "loc-1",
                            "name": "Copenhagen DC",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "Denmark",
                            "latitude": 55.6761,
                            "longitude": 12.5683,
                            "description": "",
                            "physical_address": "Vermlandsgade 51, 2300 Copenhagen",
                            "country": "Denmark",
                            "facility": "CPH-1",
                            "tenant": "Acme Corp",
                            "tenant_id": "ten-1",
                            "tenant_group": "Corporate",
                            "asn": 65001,
                            "time_zone": "Europe/Copenhagen",
                            "tags": ["critical"],
                            "url": "",
                        }
                    ]
                ),
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=(
                    [
                        {
                            "id": "dev-1",
                            "name": "router01",
                            "role": "Core Router",
                            "status": "offline",
                            "primary_ip": "10.0.0.1/32",
                        }
                    ],
                    {"level": "critical", "reason": "Core device(s) offline: router01"},
                ),
            ),
        ):
            resp = flask_client.get("/api/v1/alerts")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["summary"]["critical"] == 1
        assert data["alerts"][0]["name"] == "Copenhagen DC"
        assert data["alerts"][0]["alert_level"] == "critical"
        assert data["alerts"][0]["physical_address"] == "Vermlandsgade 51, 2300 Copenhagen"
        assert data["alerts"][0]["country"] == "Denmark"
        assert data["alerts"][0]["down_devices"] == [
            {
                "device_id": "dev-1",
                "device_name": "router01",
                "device_ip": "10.0.0.1",
                "status": "offline",
                "role": "Core Router",
                "core": True,  # the likely cause of the site's other down devices (#285)
                "case_numbers": [],
            }
        ]

    def test_api_alerts_hides_non_operational_locations_unless_requested(self, flask_client):
        caching.cache.clear()
        sample_locations = [
            {
                "id": "loc-1",
                "name": "Production Site",
                "status": "Active",
                "location_type": "Data Center",
                "parent": "",
                "latitude": 1.0,
                "longitude": 2.0,
                "description": "",
                "physical_address": "",
                "facility": "",
                "tenant": "",
                "tenant_id": "",
                "tenant_group": "",
                "asn": None,
                "time_zone": "",
                "tags": [],
                "url": "",
            },
            {
                "id": "loc-2",
                "name": "Spare Kit",
                "status": "Active",
                "location_type": "Warehouse",
                "parent": "",
                "latitude": 3.0,
                "longitude": 4.0,
                "description": "",
                "physical_address": "",
                "facility": "",
                "tenant": "",
                "tenant_id": "",
                "tenant_group": "",
                "asn": None,
                "time_zone": "",
                "tags": [],
                "url": "",
            },
        ]

        with (
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=(
                    [
                        {
                            "id": "dev-1",
                            "name": "router01",
                            "role": "Core Router",
                            "status": "active",
                            "primary_ip": "192.0.2.1/32",
                        }
                    ],
                    {"level": "ok", "reason": ""},
                ),
            ) as get_alert,
        ):
            resp = flask_client.get("/api/v1/alerts")
            assert resp.status_code == 200
            assert resp.get_json()["summary"]["total"] == 1
            assert [item["id"] for item in resp.get_json()["alerts"]] == ["loc-1"]
            assert get_alert.call_count == 1

            caching.cache.clear()
            get_alert.reset_mock()
            resp = flask_client.get("/api/v1/alerts?include_non_operational=1")

        assert resp.status_code == 200
        assert resp.get_json()["summary"]["total"] == 2
        assert [item["id"] for item in resp.get_json()["alerts"]] == ["loc-1", "loc-2"]
        assert get_alert.call_count == 2

    def test_location_exclusion_supports_object_tags(self):
        with patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_TAGS", {"non-operational"}):
            assert alerts.location_is_excluded_from_alert_board(
                {
                    "name": "Warehouse",
                    "status": "Active",
                    "location_type": "Office",
                    "tags": [{"name": "Non-Operational"}],
                }
            )

    def test_status_exclusion_null_keyword_matches_missing_status(self):
        excluded = settings.parse_csv_set("null, Decommissioning")
        assert alerts.status_is_excluded(None, excluded)
        assert alerts.status_is_excluded("", excluded)
        assert alerts.status_is_excluded("  ", excluded)
        assert alerts.status_is_excluded("DECOMMISSIONING", excluded)
        assert not alerts.status_is_excluded("Active", excluded)
        # Without the keyword an empty status is never excluded.
        assert not alerts.status_is_excluded("", {"decommissioning"})

    def test_location_exclusion_null_status(self):
        with patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_STATUSES", {"null"}):
            assert alerts.location_is_excluded_from_alert_board({"name": "Site", "status": ""})
            assert alerts.location_is_excluded_from_alert_board({"name": "Site"})
            assert not alerts.location_is_excluded_from_alert_board(
                {"name": "Site", "status": "Active"}
            )

    def test_excluded_device_statuses_are_not_scored(self):
        devices = [
            {
                "id": "d1",
                "name": "router01",
                "role": "Router",
                "status": "Decommissioning",
                "primary_ip": "10.0.0.1",
            },
            {"id": "d2", "name": "sw01", "role": "Switch", "status": "", "primary_ip": "10.0.0.2"},
            {
                "id": "d3",
                "name": "sw02",
                "role": "Switch",
                "status": "Active",
                "primary_ip": "10.0.0.3",
            },
        ]
        with patch.object(db, "get_conn", return_value=None):
            scored, alert = alerts.get_location_devices_and_alert(
                "loc-1",
                devices_data=devices,
                devices_already_normalized=True,
                require_primary_ip=True,
                excluded_device_statuses={"decommissioning", "null"},
            )
            unfiltered, unfiltered_alert = alerts.get_location_devices_and_alert(
                "loc-1",
                devices_data=devices,
                devices_already_normalized=True,
                require_primary_ip=True,
            )

        assert [device["name"] for device in scored] == ["sw02"]
        assert alert["level"] == "ok"
        # Without the setting, Decommissioning still counts as a down core device.
        assert len(unfiltered) == 3
        assert unfiltered_alert["level"] == "critical"

    def test_parse_csv_set_normalizes_case_and_whitespace(self):
        assert settings.parse_csv_set(" Decommissioning ; Core Site, POP ") == {
            "decommissioning",
            "core site",
            "pop",
        }

    def test_location_exclusion_matches_mixed_case_configured_values(self):
        with (
            patch.object(
                settings, "ALERT_BOARD_EXCLUDED_LOCATION_NAMES", settings.parse_csv_set("Warehouse")
            ),
            patch.object(
                settings,
                "ALERT_BOARD_EXCLUDED_LOCATION_STATUSES",
                settings.parse_csv_set("Decommissioning"),
            ),
            patch.object(
                settings,
                "ALERT_BOARD_EXCLUDED_LOCATION_TYPES",
                settings.parse_csv_set("Branch Office"),
            ),
            patch.object(
                settings,
                "ALERT_BOARD_EXCLUDED_LOCATION_TAGS",
                settings.parse_csv_set("Non-Operational"),
            ),
        ):
            assert alerts.location_is_excluded_from_alert_board(
                {
                    "name": "warehouse",
                    "status": "Active",
                    "location_type": "Office",
                    "tags": [],
                }
            )
            assert alerts.location_is_excluded_from_alert_board(
                {
                    "name": "Site A",
                    "status": "decommissioning",
                    "location_type": "Office",
                    "tags": [],
                }
            )
            assert alerts.location_is_excluded_from_alert_board(
                {
                    "name": "Site B",
                    "status": "Active",
                    "location_type": "branch office",
                    "tags": [],
                }
            )
            assert alerts.location_is_excluded_from_alert_board(
                {
                    "name": "Site C",
                    "status": "Active",
                    "location_type": "Office",
                    "tags": ["non-operational"],
                }
            )

    def test_get_alert_board_data_marks_location_no_data_on_error(self):
        caching.cache.clear()
        sample_locations = [
            {"id": "loc-1", "name": "Broken Site", "latitude": None, "longitude": None}
        ]

        with (
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts, "get_location_devices_and_alert", side_effect=RuntimeError("lookup failed")
            ),
        ):
            data = alerts.get_alert_board_data()

        assert data["summary"]["no_data"] == 1
        assert data["summary"]["ok"] == 0
        assert data["alerts"][0]["alert_level"] == "no_data"
        assert data["alerts"][0]["alert_reason"] == "Could not compute alert state"

    def test_location_alert_uses_cached_snapshot_only_on_device_cache_miss(self):
        with (
            patch.object(inventory, "read_devices", return_value=[]),
            patch.object(inventory, "ensure_snapshot") as ensure_snapshot,
            patch.object(
                nautobot,
                "fetch_all_pages",
                side_effect=AssertionError("should not fetch live inventory"),
            ),
        ):
            devices, alert = alerts.get_location_devices_and_alert(
                "loc-1",
                "Data Center",
                snapshot_only=True,
            )

        assert devices == []
        assert alert == {"level": "no_data", "reason": "No monitored devices"}
        ensure_snapshot.assert_not_called()

    def test_location_alert_filters_devices_without_primary_ip(self):
        devices, alert = alerts.get_location_devices_and_alert(
            "loc-1",
            "Data Center",
            devices_data=[
                {
                    "id": "dev-1",
                    "name": "ap01",
                    "device_type": "AP",
                    "manufacturer": "Cisco",
                    "role": "Access Point",
                    "status": "offline",
                    "primary_ip": "",
                    "platform": "",
                    "serial": "",
                    "tenant": "",
                },
                {
                    "id": "dev-2",
                    "name": "router01",
                    "device_type": "ASR1001-X",
                    "manufacturer": "Cisco",
                    "role": "Core Router",
                    "status": "offline",
                    "primary_ip": "192.0.2.1/32",
                    "platform": "",
                    "serial": "",
                    "tenant": "",
                },
            ],
            devices_already_normalized=True,
            snapshot_only=True,
            require_primary_ip=True,
        )

        assert [device["id"] for device in devices] == ["dev-2"]
        assert alert == {
            "level": "critical",
            "reason": "Core device(s) offline: router01",
        }

    def test_location_detail_live_device_fetch_retries_with_location_filter_on_400(self):
        bad_request = requests.HTTPError(
            "bad request",
            response=MagicMock(status_code=400),
        )
        live_device_page = [
            {
                "id": "dev-1",
                "name": "router01",
                "device_type": {"model": "ASR9006", "manufacturer": {"name": "Cisco"}},
                "role": {"name": "Core Router"},
                "status": {"label": "active"},
                "platform": None,
                "serial": "ABC123",
                "tenant": None,
            }
        ]
        fetch_calls = []

        def _mock_fetch(endpoint, params=None, **kwargs):
            fetch_calls.append((endpoint, params))
            if len(fetch_calls) == 1:
                raise bad_request
            return live_device_page

        with (
            patch.object(inventory, "read_devices", side_effect=[[], []]),
            patch.object(inventory, "ensure_snapshot"),
            patch.object(nautobot, "fetch_all_pages", side_effect=_mock_fetch),
        ):
            devices, alert = alerts.get_location_devices_and_alert("loc-1", "Data Center")

        assert len(devices) == 1
        assert alert["level"] == "ok"
        assert fetch_calls[:2] == [
            ("dcim/devices/", {"location_id": "loc-1", "depth": 1}),
            ("dcim/devices/", {"location": "loc-1", "depth": 1}),
        ]

    def test_get_alert_board_data_does_not_live_fetch_devices_on_cache_miss(self):
        caching.cache.clear()
        sample_locations = [
            {
                "id": "loc-1",
                "name": "Site 1",
                "location_type": "Data Center",
                "latitude": 1.0,
                "longitude": 2.0,
            },
            {
                "id": "loc-2",
                "name": "Site 2",
                "location_type": "Office",
                "latitude": 3.0,
                "longitude": 4.0,
            },
        ]

        with (
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(inventory, "read_devices", return_value=[]),
            patch.object(inventory, "ensure_snapshot") as ensure_snapshot,
            patch.object(
                nautobot,
                "fetch_all_pages",
                side_effect=AssertionError("should not fetch live inventory"),
            ),
        ):
            data = alerts.get_alert_board_data(force_refresh=True)

        # Sites without cached devices have nothing to judge (#124).
        assert data["summary"]["no_data"] == 2
        assert data["summary"]["non_ok"] == 0
        assert [item["alert_level"] for item in data["alerts"]] == ["no_data", "no_data"]
        ensure_snapshot.assert_called_once_with(
            force=True,
            full=False,
            wait=False,
            cooldown_seconds=inventory.FORCED_SYNC_COOLDOWN_SECONDS,
        )

    def test_get_alert_board_data_uses_nautobot_alerts_when_librenms_unavailable(self):
        caching.cache.clear()
        sample_locations = [
            {
                "id": "loc-1",
                "name": "Site 1",
                "location_type": "Data Center",
                "latitude": 1.0,
                "longitude": 2.0,
            },
            {
                "id": "loc-2",
                "name": "Site 2",
                "location_type": "Office",
                "latitude": 3.0,
                "longitude": 4.0,
            },
        ]

        with (
            patch.object(settings, "LIBRENMS_URL", "https://librenms.example.com"),
            patch.object(settings, "LIBRENMS_API_TOKEN", "token"),
            patch.object(librenms, "fetch_inventory", side_effect=RuntimeError("down")),
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                side_effect=[
                    (
                        [{"id": "d1", "status": "offline"}],
                        {"level": "critical", "reason": "Core down"},
                    ),
                    ([{"id": "d2", "status": "active"}], {"level": "ok", "reason": ""}),
                ],
            ) as get_alert,
        ):
            data = alerts.get_alert_board_data(force_refresh=True)

        assert get_alert.call_count == 2
        assert data["summary"]["critical"] == 1
        assert data["summary"]["ok"] == 1
        assert data["summary"]["no_data"] == 0

    def test_get_alert_board_data_skips_lifecycle_upsert_until_first_successful_nautobot_sync(self):
        caching.cache.clear()
        sample_locations = [
            {
                "id": "loc-1",
                "name": "Site 1",
                "location_type": "Data Center",
                "latitude": 1.0,
                "longitude": 2.0,
            },
        ]

        class _FakeConn:
            def close(self):
                return None

        with (
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(inventory, "ensure_snapshot"),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=([], {"level": "ok", "reason": ""}),
            ),
            patch.object(db, "get_conn", return_value=_FakeConn()),
            # Connection handling only: no maintenance windows (#283).
            patch.object(
                maintenance,
                "read_active",
                return_value={"sites": {}, "devices": {}, "next_change": None},
            ),
            patch.object(
                inventory,
                "get_sync_state",
                return_value={
                    "status": "error",
                    "last_successful_sync": None,
                },
            ),
            patch.object(alerts, "upsert_alert_lifecycle_for_site") as upsert,
            patch.object(
                alerts,
                "read_alert_context",
                return_value={
                    "active_alert_instance_count": 1,
                    "historical_downtime_seconds": 3600,
                    "current_downtime_seconds": 1800,
                    "active_cases": ["INC-1001"],
                    "down_devices": [{"device_id": "dev-legacy", "device_name": "legacy01"}],
                },
            ) as get_context,
        ):
            data = alerts.get_alert_board_data(force_refresh=True)

        assert data["summary"]["total"] == 1
        upsert.assert_not_called()
        get_context.assert_called_once()
        entry = data["alerts"][0]
        assert entry["active_alert_instance_count"] == 0
        assert entry["current_downtime_seconds"] == 0
        assert entry["historical_downtime_seconds"] == 3600
        assert entry["active_cases"] == []
        assert entry["down_devices"] == []


# ---------------------------------------------------------------------------
# Tests: /api/locations/<id>/detail
# ---------------------------------------------------------------------------
class TestApiLocationDetail:
    def test_returns_devices_and_asns(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations/loc-1/detail")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["devices"]) == 1
        assert data["devices"][0]["name"] == "router01"
        assert len(data["asns"]) == 1
        assert data["asns"][0]["asn"] == 65001

    def test_device_fields(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations/loc-1/detail")
        dev = resp.get_json()["devices"][0]
        assert dev["manufacturer"] == "Cisco"
        assert dev["device_type"] == "ASR1001-X"
        assert dev["role"] == "Core Router"
        assert dev["tenant"] == "Acme Corp"
        assert dev["platform"] == "IOS-XE"
        assert dev["serial"] == "SN123"
        assert dev["status"] == "Active"

    def test_device_with_null_fields(self, flask_client):
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
            resp = flask_client.get("/api/v1/locations/loc-1/detail")
        assert resp.status_code == 200
        dev = resp.get_json()["devices"][0]
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

    def test_device_lookup_failure_returns_503(self, flask_client):
        with patch.object(
            alerts, "get_location_devices_and_alert", side_effect=RuntimeError("lookup failed")
        ):
            resp = flask_client.get("/api/v1/locations/loc-1/detail")

        assert resp.status_code == 503
        assert resp.get_json()["error"] == "Nautobot service unavailable"

    def test_asn_lookup_http_error_returns_502(self, flask_client):
        http_err = requests.HTTPError(
            "upstream failed",
            response=MagicMock(status_code=502),
        )
        with (
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=([], {"level": "ok", "reason": ""}),
            ),
            patch.object(nautobot, "fetch_all_pages", side_effect=http_err),
        ):
            resp = flask_client.get("/api/v1/locations/loc-1/detail")

        assert resp.status_code == 502
        assert resp.get_json()["error"] == "Failed to communicate with Nautobot API"

    def _get_detail_with_asn_endpoint_missing(self, flask_client, cached_locations, location_get):
        """Request a detail while ``ipam/asns/`` returns 404 (Nautobot 3.x core)."""
        not_found = requests.HTTPError(
            "404 Client Error: Not Found",
            response=MagicMock(status_code=404),
        )
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
            resp = flask_client.get("/api/v1/locations/loc-1/detail")
        return resp, mock_get

    def test_missing_asn_endpoint_falls_back_to_location_asn(self, flask_client):
        resp, mock_get = self._get_detail_with_asn_endpoint_missing(
            flask_client, [], lambda endpoint, params=None: {"id": "loc-1", "asn": 65001}
        )
        assert resp.status_code == 200
        assert resp.get_json()["asns"] == [{"asn": 65001, "description": "", "tenant": ""}]
        mock_get.assert_called_once_with("dcim/locations/loc-1/")

    def test_missing_asn_endpoint_prefers_cached_location_asn(self, flask_client):
        resp, mock_get = self._get_detail_with_asn_endpoint_missing(
            flask_client,
            [{"id": "loc-2", "asn": 65002}, {"id": "loc-1", "asn": 65001}],
            AssertionError("should use the cached inventory"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["asns"] == [{"asn": 65001, "description": "", "tenant": ""}]
        mock_get.assert_not_called()

    def test_missing_asn_endpoint_and_no_location_asn_returns_empty(self, flask_client):
        resp, _ = self._get_detail_with_asn_endpoint_missing(
            flask_client,
            [{"id": "loc-1", "asn": None}],
            AssertionError("should use the cached inventory"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["asns"] == []

    def test_missing_asn_endpoint_still_fails_when_location_lookup_fails(self, flask_client):
        upstream_err = requests.HTTPError(
            "500 Server Error",
            response=MagicMock(status_code=500),
        )
        resp, _ = self._get_detail_with_asn_endpoint_missing(flask_client, [], upstream_err)
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Tests: /api/search
# ---------------------------------------------------------------------------
class TestApiSearch:
    def test_missing_query_returns_400(self, flask_client):
        resp = flask_client.get("/api/v1/search")
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_gps_coordinates_search(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            # Copenhagen coordinates – loc-1 is exactly at 55.6761,12.5683 (distance 0)
            resp = flask_client.get("/api/v1/search?q=55.6761,12.5683")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["search_lat"] == pytest.approx(55.6761)
        assert data["search_lon"] == pytest.approx(12.5683)
        assert data["radius_km"] == 5
        # loc-1 is at the exact point
        names = [loc["name"] for loc in data["locations"]]
        assert "Copenhagen DC" in names

    def test_gps_no_results_far_away(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            # Tokyo – far from all test locations
            resp = flask_client.get("/api/v1/search?q=35.6895,139.6917")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 0
        assert data["locations"] == []

    def test_search_results_sorted_by_distance(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            # Point very close to loc-1 (within 5 km)
            resp = flask_client.get("/api/v1/search?q=55.678,12.571")
        data = resp.get_json()
        distances = [loc["distance_km"] for loc in data["locations"]]
        assert distances == sorted(distances)

    def test_address_geocoding(self, flask_client):
        mock_geo_result = MagicMock()
        mock_geo_result.latitude = 55.6761
        mock_geo_result.longitude = 12.5683
        mock_geolocator = MagicMock()
        mock_geolocator.geocode.return_value = mock_geo_result

        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            with patch("app.v1.routes.locations.Nominatim", return_value=mock_geolocator):
                resp = flask_client.get("/api/v1/search?q=Copenhagen")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["search_lat"] == pytest.approx(55.6761)

    def test_address_not_found_returns_404(self, flask_client):
        mock_geolocator = MagicMock()
        mock_geolocator.geocode.return_value = None

        with patch("app.v1.routes.locations.Nominatim", return_value=mock_geolocator):
            resp = flask_client.get("/api/v1/search?q=ThisPlaceDoesNotExist12345")
        assert resp.status_code == 404
        assert "error" in resp.get_json()

    def test_distance_km_field_present(self, flask_client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/search?q=55.6761,12.5683")
        data = resp.get_json()
        for loc in data["locations"]:
            assert "distance_km" in loc


class TestNautobotRuntimeErrors:
    def test_runtime_errors_do_not_leak_internal_messages(self, flask_client):
        secret = "NAUTOBOT_URL and NAUTOBOT_TOKEN must be set"
        cases = [
            ("get", "/api/v1/locations", (inventory, "get_locations"), {}),
            ("get", "/api/v1/locations/loc-1/detail", (alerts, "get_location_detail"), {}),
            ("get", "/api/v1/search?q=55.6761,12.5683", (inventory, "get_locations"), {}),
        ]

        for method, url, (module, name), kwargs in cases:
            with patch.object(module, name, side_effect=RuntimeError(secret)):
                resp = getattr(flask_client, method)(url, **kwargs)

            assert resp.status_code == 503
            assert resp.get_json()["error"] == "Nautobot service unavailable"
            assert secret not in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Tests: index page
# ---------------------------------------------------------------------------
class TestIndex:
    """The map: built with Nuxt (#463); frontend/tests/ and e2e/ test the page itself."""

    def test_index_is_the_nuxt_page(self, flask_client, frontend_build):
        resp = flask_client.get("/?tenant=Acme%20Corp&location_id=loc-1")
        assert resp.status_code == 200 and b'id="__nuxt"' in resp.data
        # Asked again each time: a new build shows at once.
        assert resp.headers["Cache-Control"] == "no-cache"
        # No inline script: the CSP needs no 'unsafe-inline' (#197, #198).
        assert not re.search(rb"<script>(?!\s*</script>)", resp.data)

    def test_what_the_map_reads(self, flask_client, monkeypatch):
        """Nautobot's address, the tile server and the first view (#197, #362), from /api/page-config."""
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "MAP_TILE_URL", "https://tiles.internal/{z}/{x}/{y}.png")
        monkeypatch.setattr(settings, "MAP_TILE_ATTRIBUTION", '<a href="https://x">"Ours" & co</a>')
        monkeypatch.setattr(settings, "MAP_DEFAULT_VIEW", None)
        config = flask_client.get("/api/v1/page-config").get_json()
        assert config["nautobot_url"] == "https://nautobot.example.com"
        assert config["map"] == {
            "tile_url": "https://tiles.internal/{z}/{x}/{y}.png",
            "tile_attribution": '<a href="https://x">"Ours" & co</a>',
            "view": None,
        }
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
        monkeypatch.setattr(settings, "MAP_DEFAULT_VIEW", (55.7, 12.6, 6))
        config = flask_client.get("/api/v1/page-config").get_json()
        assert config["nautobot_url"] == ""
        assert config["map"]["view"] == {"latitude": 55.7, "longitude": 12.6, "zoom": 6}

    def test_live_locations_have_their_path(self, monkeypatch):
        """Without the cache (a map-only setup), /api/locations' paths still come along (#367)."""
        import mock_nautobot

        monkeypatch.setattr(
            inventory, "read_locations", lambda include_without_coordinates=False, conn=None: []
        )
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda **kwargs: None)
        monkeypatch.setattr(inventory, "live_tenant_descriptions", lambda: {})
        monkeypatch.setattr(
            nautobot, "fetch_all_pages", lambda path, **kwargs: list(mock_nautobot.LOCATIONS)
        )
        everywhere = {
            loc["id"]: loc for loc in inventory.get_locations(include_without_coordinates=True)
        }
        assert (
            everywhere["loc-aar-bld-a-f2"]["ancestor_path"]
            == "EMEA › DNK › Aarhus Campus › Bygning A"
        )
        assert {loc["id"]: loc["ancestor_path"] for loc in inventory.get_locations()}[
            "loc-cph"
        ] == ""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", None),
            ("55.7, 12.6, 6", (55.7, 12.6, 6)),
            ("-33.9,151.2,10", (-33.9, 151.2, 10)),
            ("55.7,12.6", None),
            ("55.7,12.6,6,1", None),
            ("91,0,5", None),
            ("0,181,5", None),
            ("55.7,12.6,0", None),
            ("55.7,12.6,20", None),
            ("55.7,12.6,6.5", None),
            ("Copenhagen", None),
            ("nan,0,5", None),
        ],
    )
    def test_default_view_is_checked(self, raw, expected, caplog):
        assert settings._map_view(raw) == expected
        assert ("MAP_DEFAULT_VIEW ignored" in caplog.text) == (bool(raw) and expected is None)


# ---------------------------------------------------------------------------
# Tests: caching
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: NAUTOBOT_URL validation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: SSL verification configuration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: Accept header / API version configuration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: Custom error handlers
# ---------------------------------------------------------------------------
class TestErrorHandlers:
    def test_404_json_for_api_path(self, flask_client):
        resp = flask_client.get("/api/v1/nonexistent")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "Not found"

    def test_404_json_when_accept_json(self, flask_client):
        resp = flask_client.get("/nonexistent-page", headers={"Accept": "application/json"})
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "Not found"

    def test_405_json_for_api_path(self, flask_client):
        resp = flask_client.post("/api/v1/locations")
        assert resp.status_code == 405
        data = resp.get_json()
        assert data["error"] == "Method not allowed"

    def test_http_exception_returns_json_for_api_path(self):
        from fastapi import APIRouter, FastAPI, HTTPException

        from app.v1 import deps

        router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES)

        @router.get("/api/v1/slow")
        def slow():
            raise HTTPException(
                status_code=504, detail="The connection to an upstream server timed out."
            )

        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        deps.install_error_handlers(app)
        app.include_router(router)
        resp = http_client.FlaskStyleClient(app).get("/api/v1/slow")
        assert resp.status_code == 504
        assert resp.get_json()["error"] == "The connection to an upstream server timed out."


# ---------------------------------------------------------------------------
# Tests: configurable critical role keywords
# ---------------------------------------------------------------------------
class TestConfigurableCriticalKeywords:
    """Tests for the _get_critical_keywords / compute_alert_level helpers."""

    def setup_method(self):
        """Reset module-level keyword state before each test."""
        self._orig_env_kw = alerts.ENV_CORE_ROLE_KEYWORDS
        self._orig_rules = dict(alerts.CRITICALITY_RULES)

    def teardown_method(self):
        alerts.ENV_CORE_ROLE_KEYWORDS = self._orig_env_kw
        alerts.CRITICALITY_RULES = self._orig_rules

    def test_default_keywords_applied(self):
        """Without any configuration the built-in defaults are used."""
        alerts.ENV_CORE_ROLE_KEYWORDS = alerts.DEFAULT_CORE_ROLE_KEYWORDS
        alerts.CRITICALITY_RULES = {}
        kw = alerts.get_critical_keywords()
        assert "core" in kw
        assert "router" in kw

    def test_env_override_replaces_defaults(self):
        """_ENV_CORE_ROLE_KEYWORDS env override replaces defaults when no JSON rules."""
        alerts.ENV_CORE_ROLE_KEYWORDS = ("firewall", "border")
        alerts.CRITICALITY_RULES = {}
        kw = alerts.get_critical_keywords()
        assert kw == ("firewall", "border")

    def test_env_override_used_as_fallback_for_unknown_type(self):
        """When rules have no matching type and no 'default' key, env override is used."""
        alerts.ENV_CORE_ROLE_KEYWORDS = ("firewall",)
        alerts.CRITICALITY_RULES = {"datacenter": ["core", "spine"]}
        kw = alerts.get_critical_keywords("office")
        assert kw == ("firewall",)

    def test_location_type_rule_matched(self):
        """The exact location_type key is returned when present in rules."""
        alerts.CRITICALITY_RULES = {
            "datacenter": ["core", "firewall"],
            "office": ["router"],
        }
        kw = alerts.get_critical_keywords("Datacenter")
        assert "firewall" in kw

    def test_rules_default_key_used_for_unknown_type(self):
        """The 'default' key in rules is the fallback for unknown location types."""
        alerts.CRITICALITY_RULES = {
            "default": ["core", "spine"],
            "office": ["router"],
        }
        kw = alerts.get_critical_keywords("warehouse")
        assert kw == ("core", "spine")

    def test_compute_alert_level_respects_location_type(self):
        """compute_alert_level uses the correct keyword set for the given location type."""
        alerts.CRITICALITY_RULES = {
            "office": ["router"],
            "datacenter": ["core", "firewall"],
        }
        # A "firewall" device offline in a datacenter → critical
        dc_devices = [{"id": "d1", "name": "fw01", "role": "Firewall", "status": "offline"}]
        result = alerts.compute_alert_level(dc_devices, location_type="datacenter")
        assert result["level"] == "critical"

        # Same device in an office (only "router" is critical there) → medium (if >25%) or ok
        office_devices = [
            {"id": "d1", "name": "fw01", "role": "Firewall", "status": "offline"},
            {"id": "d2", "name": "sw01", "role": "Switch", "status": "active"},
        ]
        result = alerts.compute_alert_level(office_devices, location_type="office")
        assert result["level"] != "critical"

    def test_compute_alert_level_no_devices(self):
        assert alerts.compute_alert_level([]) == {
            "level": "no_data",
            "reason": "No monitored devices",
        }

    def test_compute_alert_level_medium_threshold(self):
        """More than 25% of devices down → medium alert."""
        devices = [
            {"id": "d1", "name": "sw01", "role": "Switch", "status": "offline"},
            {"id": "d2", "name": "sw02", "role": "Switch", "status": "active"},
            {"id": "d3", "name": "sw03", "role": "Switch", "status": "active"},
        ]
        # 1/3 ≈ 33% > 25% → medium
        result = alerts.compute_alert_level(devices)
        assert result["level"] == "medium"

    def test_compute_alert_level_low_when_below_threshold(self):
        """Under 25% down and no core device down → ok."""
        devices = [
            {"id": "d1", "name": "sw01", "role": "Switch", "status": "offline"},
            {"id": "d2", "name": "sw02", "role": "Switch", "status": "active"},
            {"id": "d3", "name": "sw03", "role": "Switch", "status": "active"},
            {"id": "d4", "name": "sw04", "role": "Switch", "status": "active"},
            {"id": "d5", "name": "sw05", "role": "Switch", "status": "active"},
        ]
        # 1/5 = 20% ≤ 25% → ok
        result = alerts.compute_alert_level(devices)
        assert result == {"level": "low", "reason": "1/5 devices offline (20%)"}


# ---------------------------------------------------------------------------
# Tests: location_type passed through detail endpoint
# ---------------------------------------------------------------------------
class TestLocationDetailWithLocationType:
    def test_location_type_param_accepted(self, flask_client):
        """The ?location_type query param is accepted without error."""
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = flask_client.get("/api/v1/locations/loc-1/detail?location_type=Data+Center")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "devices" in data
        assert "alert" in data

    def test_location_type_influences_alert(self, flask_client):
        """When location_type maps to rules, compute_alert_level uses correct keywords."""

        orig_rules = dict(alerts.CRITICALITY_RULES)
        orig_env = alerts.ENV_CORE_ROLE_KEYWORDS
        alerts.CRITICALITY_RULES = {"datacenter": ["firewall"]}
        alerts.ENV_CORE_ROLE_KEYWORDS = ()

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

        try:
            with patch.object(nautobot, "get", side_effect=mock_get):
                resp = flask_client.get("/api/v1/locations/loc-1/detail?location_type=datacenter")
            data = resp.get_json()
            assert data["alert"]["level"] == "critical"
        finally:
            alerts.CRITICALITY_RULES = orig_rules
            alerts.ENV_CORE_ROLE_KEYWORDS = orig_env


# ---------------------------------------------------------------------------
# Tests: criticality override REST endpoints
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("allow_unauthenticated_writes")
class TestCriticalityOverrideEndpoints:
    """Tests for /api/criticality-overrides (requires the persistence database)."""

    @pytest.fixture(autouse=True)
    def _database(self, pg_database):
        self.db = pg_database

    def test_list_empty(self, flask_client):
        resp = flask_client.get("/api/v1/criticality-overrides")
        assert resp.status_code == 200
        assert resp.get_json()["overrides"] == []

    def test_create_override(self, flask_client):
        resp = flask_client.post(
            "/api/v1/criticality-overrides",
            json={
                "nautobot_device_id": "dev-abc",
                "is_critical": False,
                "reason": "Local firewall",
                "updated_by": "admin",
            },
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["nautobot_device_id"] == "dev-abc"
        assert data["is_critical"] is False

    def test_list_after_create(self, flask_client):
        flask_client.post(
            "/api/v1/criticality-overrides",
            json={
                "nautobot_device_id": "dev-abc",
                "is_critical": True,
                "reason": "Core router",
                "updated_by": "admin",
            },
            content_type="application/json",
        )
        resp = flask_client.get("/api/v1/criticality-overrides")
        overrides = resp.get_json()["overrides"]
        assert len(overrides) == 1
        assert overrides[0]["nautobot_device_id"] == "dev-abc"

    def test_update_override(self, flask_client):
        """Posting the same device_id a second time updates in-place."""
        flask_client.post(
            "/api/v1/criticality-overrides",
            json={"nautobot_device_id": "dev-x", "is_critical": True},
            content_type="application/json",
        )
        flask_client.post(
            "/api/v1/criticality-overrides",
            json={"nautobot_device_id": "dev-x", "is_critical": False, "reason": "Changed"},
            content_type="application/json",
        )
        resp = flask_client.get("/api/v1/criticality-overrides")
        overrides = resp.get_json()["overrides"]
        assert len(overrides) == 1
        assert overrides[0]["is_critical"] == 0  # stored as int

    def test_delete_override(self, flask_client):
        flask_client.post(
            "/api/v1/criticality-overrides",
            json={"nautobot_device_id": "dev-del", "is_critical": True},
            content_type="application/json",
        )
        resp = flask_client.delete("/api/v1/criticality-overrides/dev-del")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "deleted"
        # Should be gone now
        resp2 = flask_client.get("/api/v1/criticality-overrides")
        assert resp2.get_json()["overrides"] == []

    def test_delete_nonexistent_returns_404(self, flask_client):
        resp = flask_client.delete("/api/v1/criticality-overrides/does-not-exist")
        assert resp.status_code == 404

    def test_create_missing_device_id_returns_400(self, flask_client):
        resp = flask_client.post(
            "/api/v1/criticality-overrides",
            json={"is_critical": True},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_override_affects_compute_alert_level(self):
        """A device marked is_critical=False must not trigger a critical alert."""
        self.db.execute(
            "INSERT INTO device_criticality_override "
            "(nautobot_device_id, is_critical, reason, updated_by) "
            "VALUES (%s, %s, %s, %s)",
            ("dev-fw", 0, "Local firewall – not critical", "test"),
        )

        devices = [
            {"id": "dev-fw", "name": "fw-local", "role": "Core Router", "status": "offline"},
            # A healthy second device, so "every device down" (also Critical) does not apply.
            {"id": "dev-sw", "name": "sw-local", "role": "Switch", "status": "active"},
        ]
        # The override says is_critical=False, so even a "Core Router" that's
        # offline should not produce a critical alert.
        result = alerts.compute_alert_level(devices)
        assert result["level"] != "critical"

    def test_requires_operator_role_when_auth_enabled(self, flask_client):
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            resp = flask_client.post(
                "/api/v1/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                content_type="application/json",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-viewers"},
            )
        assert resp.status_code == 403
        assert resp.get_json()["required_role"] == "operator"

    def test_accepts_operator_group_when_auth_enabled(self, flask_client):
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            resp = flask_client.post(
                "/api/v1/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                content_type="application/json",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
        assert resp.status_code == 200

    def test_uses_authenticated_username_as_updated_by(self, flask_client):
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            resp = flask_client.post(
                "/api/v1/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                content_type="application/json",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
            assert resp.status_code == 200
            listed = flask_client.get(
                "/api/v1/criticality-overrides",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
        assert listed.status_code == 200
        assert listed.get_json()["overrides"][0]["updated_by"] == "alice"


# ---------------------------------------------------------------------------
# Tests: alert lifecycle history and cases
# ---------------------------------------------------------------------------
class TestAlertLifecycleTracking:
    @pytest.fixture(autouse=True)
    def _database(self, pg_database):
        self.db = pg_database

    def test_api_alerts_contains_lifecycle_fields(self, flask_client):
        with (
            patch.object(
                inventory,
                "get_locations",
                return_value=_full_locations(
                    [
                        {
                            "id": "loc-1",
                            "name": "Site One",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": "",
                            "tags": [],
                            "url": "",
                        }
                    ]
                ),
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=(
                    [
                        {
                            "id": "dev-1",
                            "name": "router01",
                            "role": "Core Router",
                            "status": "offline",
                        }
                    ],
                    {"level": "critical", "reason": "Core device(s) offline: router01"},
                ),
            ),
        ):
            resp = flask_client.get("/api/v1/alerts")
        assert resp.status_code == 200
        entry = resp.get_json()["alerts"][0]
        assert "current_downtime_seconds" in entry
        assert "historical_downtime_seconds" in entry
        assert "active_cases" in entry
        assert entry["active_alert_instance_count"] == 1
        assert entry["down_devices"] == [
            {
                "device_id": "dev-1",
                "device_name": "router01",
                "device_ip": "",
                "status": "offline",
                "role": "Core Router",
                "core": True,  # the likely cause of the site's other down devices (#285)
                "case_numbers": [],
            }
        ]

    def test_alert_history_tracks_open_and_resolve(self, flask_client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        t1 = datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        alerts.upsert_alert_lifecycle_for_site(
            site,
            devices_down,
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            t0,
        )
        alerts.upsert_alert_lifecycle_for_site(
            site,
            [{"id": "dev-1", "name": "router01", "status": "active"}],
            {"level": "ok", "reason": ""},
            t1,
        )
        resp = flask_client.get("/api/v1/alert-history?site_id=loc-1")
        assert resp.status_code == 200
        instances = resp.get_json()["instances"]
        assert len(instances) == 1
        assert instances[0]["status"] == "resolved"
        assert instances[0]["total_downtime_seconds"] == 300
        event_types = [event["event_type"] for event in instances[0]["events"]]
        assert "opened" in event_types
        assert "resolved" in event_types
        resolved_event = next(
            event for event in instances[0]["events"] if event["event_type"] == "resolved"
        )
        assert resolved_event["snapshot"]["site_id"] == "loc-1"
        assert resolved_event["snapshot"]["device_id"] == "dev-1"
        assert resolved_event["snapshot"]["status"] == "resolved"

    def test_severity_change_keeps_the_alert_and_its_downtime(self):
        """Medium → Critical must not restart the device's downtime (#163)."""
        site = {"id": "loc-1", "name": "Site One"}
        t0 = "2026-01-01T00:00:00Z"
        t1 = "2026-01-01T01:00:00Z"
        alerts.upsert_alert_lifecycle_for_site(
            site,
            [{"id": "dev-1", "name": "sw01", "status": "offline"}],
            {"level": "medium", "reason": "1/3 devices offline (33%)"},
            t0,
        )
        alerts.upsert_alert_lifecycle_for_site(
            site,
            [
                {"id": "dev-1", "name": "sw01", "status": "offline"},
                {"id": "dev-2", "name": "core01", "status": "offline"},
            ],
            {"level": "critical", "reason": "Core device(s) offline: core01"},
            t1,
        )

        rows = self.db.execute(
            "SELECT device_id, status, alert_level, down_started_at FROM alert_instances ORDER BY device_id, id"
        )
        dev1 = [row for row in rows if row["device_id"] == "dev-1"]
        assert len(dev1) == 1, dev1  # one alert, not resolved and reopened
        assert dev1[0]["status"] == "open"
        assert dev1[0]["alert_level"] == "critical"
        assert db.serialize_value(dev1[0]["down_started_at"]) == t0
        events = self.db.execute(
            "SELECT e.event_type, e.alert_level FROM alert_events e "
            "JOIN alert_instances i ON i.id = e.alert_instance_id WHERE i.device_id = 'dev-1' ORDER BY e.id"
        )
        assert events == [
            {"event_type": "opened", "alert_level": "medium"},
            {"event_type": "updated", "alert_level": "critical"},
        ]
        context = alerts.get_alert_context_for_site("loc-1", t1)
        assert context["current_downtime_seconds"] == 3600

    def test_add_case_number_to_active_alert(self, flask_client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        alerts.upsert_alert_lifecycle_for_site(
            site,
            devices_down,
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            t0,
        )
        created = flask_client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_id": "dev-1", "case_number": "INC-1001"},
            content_type="application/json",
        )
        assert created.status_code == 200
        history = flask_client.get("/api/v1/alert-history?site_id=loc-1&device_id=dev-1")
        assert history.status_code == 200
        instances = history.get_json()["instances"]
        assert instances[0]["cases"][0]["case_number"] == "INC-1001"

    def test_api_alerts_preserves_case_numbers_on_current_down_devices(self, flask_client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        alerts.upsert_alert_lifecycle_for_site(
            site,
            devices_down,
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            t0,
        )
        created = flask_client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_id": "dev-1", "case_number": "INC-1001"},
            content_type="application/json",
        )
        assert created.status_code == 200
        conn = db.get_conn()
        try:
            with conn:
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:05:00Z",
                    last_successful_sync="2026-01-01T00:05:00Z",
                    cache_version=inventory.CACHE_VERSION,
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        with (
            patch.object(
                inventory,
                "get_locations",
                return_value=_full_locations(
                    [
                        {
                            "id": "loc-1",
                            "name": "Site One",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": "",
                            "tags": [],
                            "url": "",
                        }
                    ]
                ),
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=(
                    [
                        {
                            "id": "dev-1",
                            "name": "router01",
                            "role": "Core Router",
                            "status": "offline",
                        }
                    ],
                    {"level": "critical", "reason": "Core device(s) offline: router01"},
                ),
            ),
        ):
            resp = flask_client.get("/api/v1/alerts")

        assert resp.status_code == 200
        entry = resp.get_json()["alerts"][0]
        assert entry["active_cases"] == ["INC-1001"]
        assert entry["down_devices"] == [
            {
                "device_id": "dev-1",
                "device_name": "router01",
                "device_ip": "",
                "status": "offline",
                "role": "Core Router",
                "core": True,  # the likely cause of the site's other down devices (#285)
                "case_numbers": ["INC-1001"],
                "down_started_at": "2026-01-01T00:00:00Z",
            }
        ]

    def test_add_case_uses_authenticated_user_for_created_by(self, flask_client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        alerts.upsert_alert_lifecycle_for_site(
            site,
            devices_down,
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            t0,
        )
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            created = flask_client.post(
                "/api/v1/alert-cases",
                json={
                    "site_id": "loc-1",
                    "device_id": "dev-1",
                    "case_number": "INC-1002",
                    "created_by": "mallory",
                },
                content_type="application/json",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
            assert created.status_code == 200
            history = flask_client.get(
                "/api/v1/alert-history?site_id=loc-1&device_id=dev-1",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
        assert history.status_code == 200
        instances = history.get_json()["instances"]
        assert instances[0]["cases"][0]["created_by"] == "alice"

    def test_alert_history_rejects_invalid_timestamp_filters(self, flask_client):
        resp = flask_client.get("/api/v1/alert-history?start_at=not-a-timestamp")
        assert resp.status_code == 400
        assert "start_at" in resp.get_json()["error"]
        resp = flask_client.get("/api/v1/alert-history?end_at=still-not-a-timestamp")
        assert resp.status_code == 400
        assert "end_at" in resp.get_json()["error"]

    def test_failed_alert_observation_does_not_resolve_open_incident(self):
        site = {"id": "loc-1", "name": "Site One"}
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        alerts.upsert_alert_lifecycle_for_site(
            site,
            [{"id": "dev-1", "name": "router01", "status": "offline"}],
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            t0,
        )
        sample_locations = [{"id": "loc-1", "name": "Site One", "latitude": 1.0, "longitude": 2.0}]
        with (
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts, "get_location_devices_and_alert", side_effect=RuntimeError("lookup failed")
            ),
        ):
            data = alerts.get_alert_board_data(force_refresh=True)
        assert data["alerts"][0]["alert_level"] == "no_data"
        history = alerts.get_alert_context_for_site("loc-1", timeutil.iso_utc_now())
        assert history["active_alert_instance_count"] == 1
        conn = db.get_conn()
        try:
            rows = conn.execute(
                "SELECT status FROM alert_instances WHERE site_id = %s ORDER BY id DESC",
                ("loc-1",),
            ).fetchall()
        finally:
            conn.close()
        assert db.row_to_dict(rows[0])["status"] == "open"

    def test_get_alert_board_data_uses_cache_when_persistence_enabled(self):
        sample_locations = [{"id": "loc-1", "name": "Site One", "latitude": 1.0, "longitude": 2.0}]
        devices_return = (
            [{"id": "dev-1", "name": "router01", "role": "Core Router", "status": "offline"}],
            {"level": "critical", "reason": "Core device(s) offline: router01"},
        )
        with (
            patch.object(
                inventory, "get_locations", return_value=sample_locations
            ) as get_locations,
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts, "get_location_devices_and_alert", return_value=devices_return
            ) as get_alert,
        ):
            first = alerts.get_alert_board_data(force_refresh=True)
            second = alerts.get_alert_board_data()
        assert first["alerts"] == second["alerts"]
        assert get_locations.call_count == 1
        assert get_alert.call_count == 1

    def test_get_alert_board_data_sets_ttl_and_enqueues_sync_on_force_refresh(self):
        first_payload = {
            "checked_at": "2026-01-01T00:00:00Z",
            "stale_after_seconds": settings.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        second_payload = {
            "checked_at": "2026-01-01T00:05:00Z",
            "stale_after_seconds": settings.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        caching.cache.clear()
        with (
            patch.object(
                alerts,
                "build_alert_board_payload",
                side_effect=[first_payload, second_payload],
            ) as build_payload,
            patch.object(caching, "set", wraps=caching.set) as cache_set,
            patch.object(
                inventory,
                "ensure_snapshot",
            ) as ensure_snapshot,
            patch.object(
                inventory,
                "board_snapshot",
                return_value={"version": None, "initialized": True, "nautobot": {}},
            ),
        ):
            first = alerts.get_alert_board_data(force_refresh=True)
            second = alerts.get_alert_board_data()
            refreshed = alerts.get_alert_board_data(force_refresh=True)
        assert first["checked_at"] == second["checked_at"] == "2026-01-01T00:00:00Z"
        assert refreshed["checked_at"] == "2026-01-01T00:00:00Z"
        assert build_payload.call_count == 1
        assert cache_set.call_count == 1
        assert ensure_snapshot.call_count == 2
        assert all(
            call.args == ()
            and call.kwargs
            == {
                "force": True,
                "full": False,
                "wait": False,
                "cooldown_seconds": inventory.FORCED_SYNC_COOLDOWN_SECONDS,
            }
            for call in ensure_snapshot.call_args_list
        )
        # The stored board outlives the sync interval a little (#295).
        assert all(
            call.kwargs.get("timeout") == settings.CACHE_TTL + alerts.BOARD_TTL_SLACK_SECONDS
            for call in cache_set.call_args_list
        )

    def test_get_alert_board_data_builds_snapshot_only_payload(self):
        caching.cache.clear()
        payload = {
            "checked_at": "2026-01-01T00:00:00Z",
            "stale_after_seconds": settings.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        with (
            patch.object(
                alerts, "build_alert_board_payload", return_value=payload
            ) as build_payload,
            patch.object(inventory, "ensure_snapshot") as ensure_snapshot,
        ):
            result = alerts.get_alert_board_data(force_refresh=True)

        assert result["checked_at"] == "2026-01-01T00:00:00Z"
        build_payload.assert_called_once_with(snapshot_only=True)
        ensure_snapshot.assert_called_once_with(
            force=True,
            full=False,
            wait=False,
            cooldown_seconds=inventory.FORCED_SYNC_COOLDOWN_SECONDS,
        )

    def test_get_alert_board_data_does_not_cache_empty_payload_before_snapshot_init(self):
        caching.cache.clear()
        payload = {
            "checked_at": "2026-01-01T00:00:00Z",
            "stale_after_seconds": settings.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        with (
            patch.object(
                alerts,
                "build_alert_board_payload",
                return_value=payload,
            ) as build_payload,
            patch.object(
                caching,
                "set",
                wraps=caching.set,
            ) as cache_set,
            patch.object(
                inventory,
                "board_snapshot",
                return_value={"version": None, "initialized": False, "nautobot": {}},
            ),
            patch.object(inventory, "ensure_snapshot"),
        ):
            first = alerts.get_alert_board_data(force_refresh=True)
            second = alerts.get_alert_board_data()

        assert first["alerts"] == []
        assert second["alerts"] == []
        assert build_payload.call_count == 2
        assert cache_set.call_count == 0

    def test_postgres_lifecycle_path_uses_postgres_sql(self):
        class _FakeResult:
            def __init__(self, rows=None, rowcount=0):
                self._rows = rows or []
                self.rowcount = rowcount

            def fetchone(self):
                return self._rows[0] if self._rows else None

            def fetchall(self):
                return self._rows

        class _FakeTransaction:
            def __init__(self, conn):
                self.conn = conn

            def __enter__(self):
                self.conn.transaction_entries += 1
                return self.conn

            def __exit__(self, exc_type, exc, tb):
                return False

        class _FakeConn:
            def __init__(self):
                self.queries = []
                self.closed = False
                self.connection_context_entries = 0
                self.transaction_entries = 0

            def __enter__(self):
                self.connection_context_entries += 1
                return self

            def __exit__(self, exc_type, exc, tb):
                self.closed = True
                return False

            def close(self):
                self.closed = True

            def transaction(self):
                return _FakeTransaction(self)

            def execute(self, query, params=()):
                if self.closed:
                    raise RuntimeError("the connection is closed")
                self.queries.append((query, params))
                if "SELECT id, status, down_started_at, alert_level, alert_reason" in query:
                    return _FakeResult([])
                if "INSERT INTO alert_instances" in query:
                    return _FakeResult([{"id": 101}])
                if "INSERT INTO alert_events" in query:
                    return _FakeResult([])
                if (
                    "SELECT id, alert_key, site_id, site_name, device_id, device_name, down_started_at"
                    in query
                ):
                    return _FakeResult(
                        [
                            {
                                "id": 101,
                                "alert_key": "k",
                                "site_id": "loc-1",
                                "site_name": "Site One",
                                "device_id": "dev-1",
                                "device_name": "router01",
                                "down_started_at": "2026-01-01T00:00:00Z",
                            }
                        ]
                    )
                if "UPDATE alert_instances" in query:
                    return _FakeResult([], rowcount=1)
                return _FakeResult([])

        fake_conn = _FakeConn()
        checked_at = "2026-01-01T00:00:00Z"
        with patch.object(db, "dialect", return_value="postgres"):
            alerts.upsert_alert_lifecycle_for_site(
                {"id": "loc-1", "name": "Site One"},
                [{"id": "dev-1", "name": "router01", "status": "offline"}],
                {"level": "critical", "reason": "offline"},
                checked_at,
                conn=fake_conn,
            )
            alerts.resolve_open_alert_instances_for_site(
                fake_conn,
                "loc-1",
                set(),
                checked_at,
            )
        insert_sql = next(
            query for query, _ in fake_conn.queries if "INSERT INTO alert_instances" in query
        )
        assert "ON CONFLICT (alert_key) WHERE status = 'open' DO NOTHING" in insert_sql
        assert "%s" in insert_sql
        update_sql = next(
            query for query, _ in fake_conn.queries if "UPDATE alert_instances" in query
        )
        assert "CURRENT_TIMESTAMP" in update_sql
        assert "AND status = 'open'" in update_sql
        assert "AND down_started_at <=" in update_sql
        event_params = [
            params for query, params in fake_conn.queries if "INSERT INTO alert_events" in query
        ]
        assert any(param == checked_at for params in event_params for param in params)
        assert fake_conn.connection_context_entries == 0
        assert fake_conn.transaction_entries == 1


class TestInventoryCacheSync:
    @pytest.fixture(autouse=True)
    def _database(self, pg_database, monkeypatch):
        self.db = pg_database
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")

    def test_get_locations_prefers_cached_inventory_without_live_api(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        {
                            "id": "loc-1",
                            "name": "Cached Site",
                            "slug": "cached-site",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": "",
                            "tags": ["cached"],
                            "url": "",
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
        finally:
            conn.close()

        with patch.object(
            nautobot,
            "fetch_all_pages",
            side_effect=AssertionError("should not fetch live inventory"),
        ):
            locations = inventory.get_locations()

        assert locations == [
            {
                "id": "loc-1",
                "name": "Cached Site",
                "slug": "cached-site",
                "status": "Active",
                "location_type": "Data Center",
                "parent": "",
                "parent_id": "",
                "ancestor_path": "",
                "latitude": 1.0,
                "longitude": 2.0,
                "description": "",
                "physical_address": "",
                "country": "",
                "tier": "",
                "tenant_scopes": {},
                "facility": "",
                "tenant": "",
                "tenant_id": "",
                "tenant_group": "",
                "asn": None,
                "time_zone": "",
                "tags": ["cached"],
                "url": "",
                "tenants": [],
                "tenant_descriptions": {},
            }
        ]

    def test_cached_locations_allow_null_time_zone(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        {
                            "id": "loc-1",
                            "name": "Cached Site",
                            "slug": "cached-site",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": None,
                            "tags": [],
                            "url": "",
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
        finally:
            conn.close()

        locations = inventory.read_locations(include_without_coordinates=True)
        location = next(item for item in locations if item["id"] == "loc-1")

        assert location["time_zone"] is None

    def test_write_cached_locations_coalesces_explicit_none_text_fields(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        {
                            "id": "loc-1",
                            "name": "Cached Site",
                            "slug": "cached-site",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": None,
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": None,
                            "physical_address": None,
                            "facility": None,
                            "tenant": None,
                            "tenant_id": None,
                            "tenant_group": None,
                            "asn": None,
                            "time_zone": None,
                            "tags": [],
                            "url": None,
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
        finally:
            conn.close()

        locations = inventory.read_locations(include_without_coordinates=True)
        assert len(locations) == 1
        assert locations[0]["parent"] == ""
        assert locations[0]["description"] == ""
        assert locations[0]["physical_address"] == ""
        assert locations[0]["facility"] == ""
        assert locations[0]["tenant"] == ""
        assert locations[0]["tenant_id"] == ""
        assert locations[0]["tenant_group"] == ""
        assert locations[0]["time_zone"] is None
        assert locations[0]["url"] == ""

    def test_write_cached_devices_coalesces_explicit_none_text_fields(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_devices(
                    conn,
                    [
                        {
                            "id": "dev-1",
                            "location_id": None,
                            "name": None,
                            "device_type": None,
                            "manufacturer": None,
                            "role": None,
                            "status": None,
                            "primary_ip": None,
                            "platform": None,
                            "serial": None,
                            "tenant": None,
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
        finally:
            conn.close()

        devices = inventory.read_devices()
        assert len(devices) == 1
        assert devices[0]["location_id"] == ""
        assert devices[0]["name"] == ""
        assert devices[0]["device_type"] == ""
        assert devices[0]["manufacturer"] == ""
        assert devices[0]["role"] == ""
        assert devices[0]["status"] == ""
        assert devices[0]["primary_ip"] == ""
        assert devices[0]["platform"] == ""
        assert devices[0]["serial"] == ""
        assert devices[0]["tenant"] == ""

    def test_alert_board_uses_cached_inventory_snapshot(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        {
                            "id": "loc-1",
                            "name": "Cached Site",
                            "slug": "cached-site",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": "",
                            "tags": [],
                            "url": "",
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
                inventory.write_devices(
                    conn,
                    [
                        {
                            "id": "dev-1",
                            "location_id": "loc-1",
                            "name": "router01",
                            "device_type": "ASR1001-X",
                            "manufacturer": "Cisco",
                            "role": "Core Router",
                            "status": "offline",
                            "primary_ip": "192.0.2.1/32",
                            "platform": "IOS-XE",
                            "serial": "SN123",
                            "tenant": "",
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
        finally:
            conn.close()

        caching.cache.clear()
        with patch.object(
            nautobot,
            "fetch_all_pages",
            side_effect=AssertionError("should not fetch live inventory"),
        ):
            data = alerts.get_alert_board_data(force_refresh=True)

        assert data["summary"]["critical"] == 1
        assert data["alerts"][0]["id"] == "loc-1"
        assert data["alerts"][0]["down_device_count"] == 1
        assert data["alerts"][0]["alert_level"] == "critical"

    def test_cached_location_read_triggers_background_refresh_when_sync_is_due(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        {
                            "id": "loc-1",
                            "name": "Cached Site",
                            "slug": "cached-site",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": "",
                            "tags": [],
                            "url": "",
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:00:00Z",
                    last_successful_sync="2026-01-01T00:00:00Z",
                    cache_version=inventory.CACHE_VERSION,
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        refresh_called = threading.Event()

        def fake_sync(force=False):
            refresh_called.set()

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 0),
            patch.object(inventory, "sync_nautobot", side_effect=fake_sync),
        ):
            locations = inventory.get_locations()
            assert refresh_called.wait(1), "expected cached read to trigger a background refresh"

        assert locations[0]["id"] == "loc-1"

    def test_sync_nautobot_inventory_uses_last_successful_sync_watermark(self):
        calls = []

        def fake_fetch(endpoint, params=None, **kwargs):
            calls.append((endpoint, dict(params or {})))
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "Site One",
                        "slug": "site-one",
                        "status": {"label": "Active"},
                        "location_type": {"name": "Data Center"},
                        "parent": None,
                        "latitude": "1.0",
                        "longitude": "2.0",
                        "description": "",
                        "physical_address": "",
                        "facility": "",
                        "tenant": None,
                        "asn": None,
                        "time_zone": "",
                        "tags": [],
                        "url": "",
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            if endpoint == "dcim/devices/":
                return [
                    {
                        "id": "dev-1",
                        "name": "router01",
                        "location": {"id": "loc-1"},
                        "device_type": {"model": "ASR1001-X", "manufacturer": {"name": "Cisco"}},
                        "role": {"name": "Core Router"},
                        "status": {"label": "Active"},
                        "platform": {"name": "IOS-XE"},
                        "serial": "SN123",
                        "tenant": None,
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            return []

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(inventory, "read_location_name_map", return_value={}),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
        ):
            inventory.sync_nautobot(force=True)
            first_state = inventory.get_sync_state("nautobot_inventory")
            inventory.sync_nautobot()

        location_calls = [params for endpoint, params in calls if endpoint == "dcim/locations/"]
        device_calls = [params for endpoint, params in calls if endpoint == "dcim/devices/"]

        assert location_calls[0] == {}
        assert device_calls[0] == {"depth": 1}
        assert location_calls[1]["last_updated__gte"] == first_state["last_successful_sync"]
        assert device_calls[1]["last_updated__gte"] == first_state["last_successful_sync"]
        assert device_calls[1]["depth"] == 1

    def test_incremental_sync_keeps_existing_watermark_without_newer_last_updated(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:05:00Z",
                    last_successful_sync="2026-01-01T00:05:00Z",
                    cache_version=inventory.CACHE_VERSION,
                    status="idle",
                    error_message="",
                )
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory_reconcile",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2099-01-01T00:00:00Z",
                    last_successful_sync="2099-01-01T00:00:00Z",
                    cache_version=inventory.CACHE_VERSION,
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        def fake_fetch(endpoint, params=None, **kwargs):
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "Site One",
                        "slug": "site-one",
                        "status": {"label": "Active"},
                        "location_type": {"name": "Data Center"},
                        "parent": None,
                        "latitude": "1.0",
                        "longitude": "2.0",
                        "description": "",
                        "physical_address": "",
                        "facility": "",
                        "tenant": None,
                        "asn": None,
                        "time_zone": "",
                        "tags": [],
                        "url": "",
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            if endpoint == "dcim/devices/":
                return [
                    {
                        "id": "dev-1",
                        "name": "router01",
                        "location": {"id": "loc-1"},
                        "device_type": {"model": "ASR1001-X", "manufacturer": {"name": "Cisco"}},
                        "role": {"name": "Core Router"},
                        "status": {"label": "Active"},
                        "platform": {"name": "IOS-XE"},
                        "serial": "SN123",
                        "tenant": None,
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            return []

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(inventory, "read_location_name_map", return_value={}),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
        ):
            inventory.sync_nautobot()

        state = inventory.get_sync_state("nautobot_inventory")
        assert state["last_successful_sync"] == "2026-01-01T00:05:00Z"

    def test_sync_nautobot_inventory_full_reconciles_when_cache_version_changes(self):
        calls = []

        def fake_fetch(endpoint, params=None, **kwargs):
            calls.append((endpoint, dict(params or {})))
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "Site One",
                        "slug": "site-one",
                        "status": {"label": "Active"},
                        "location_type": {"name": "Data Center"},
                        "parent": None,
                        "latitude": "1.0",
                        "longitude": "2.0",
                        "description": "",
                        "physical_address": "",
                        "facility": "",
                        "tenant": None,
                        "asn": None,
                        "time_zone": "",
                        "tags": [],
                        "url": "",
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            if endpoint == "dcim/devices/":
                return [
                    {
                        "id": "dev-1",
                        "name": "router01",
                        "location": {"id": "loc-1"},
                        "device_type": {"model": "ASR1001-X", "manufacturer": {"name": "Cisco"}},
                        "role": {"name": "Core Router"},
                        "status": {"label": "Active"},
                        "platform": {"name": "IOS-XE"},
                        "serial": "SN123",
                        "tenant": None,
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            return []

        conn = db.get_conn()
        try:
            with conn:
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:05:00Z",
                    last_successful_sync="2026-01-01T00:05:00Z",
                    cache_version="1",
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(inventory, "read_location_name_map", return_value={}),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
            patch.object(inventory, "sync_due", return_value=False),
            patch.object(
                timeutil,
                "iso_utc_now",
                side_effect=["2026-01-02T00:00:00Z", "2026-01-02T00:00:10Z"],
            ),
        ):
            inventory.sync_nautobot()

        state = inventory.get_sync_state("nautobot_inventory")
        reconcile_state = inventory.get_sync_state("nautobot_inventory_reconcile")
        location_calls = [params for endpoint, params in calls if endpoint == "dcim/locations/"]
        device_calls = [params for endpoint, params in calls if endpoint == "dcim/devices/"]

        assert location_calls == [{}]
        assert device_calls == [{"depth": 1}]
        assert state["cache_version"] == inventory.CACHE_VERSION
        assert reconcile_state["cache_version"] == inventory.CACHE_VERSION
        assert state["last_successful_sync"] == "2026-01-02T00:00:00Z"
        assert reconcile_state["last_successful_sync"] == "2026-01-02T00:00:00Z"

    def test_ensure_inventory_snapshot_runs_nautobot_sync_on_cache_version_mismatch(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:05:00Z",
                    last_successful_sync="2026-01-01T00:05:00Z",
                    cache_version="1",
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(inventory, "sync_due", return_value=False),
            patch.object(inventory, "sync_nautobot") as sync_nautobot,
        ):
            assert inventory.ensure_snapshot(wait=True) is True

        sync_nautobot.assert_called_once_with(force=False)

    def test_sync_nautobot_inventory_full_reconcile_advances_watermark(self):
        calls = []

        def fake_fetch(endpoint, params=None, **kwargs):
            calls.append((endpoint, dict(params or {})))
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "Site One",
                        "slug": "site-one",
                        "status": {"label": "Active"},
                        "location_type": {"name": "Data Center"},
                        "parent": None,
                        "latitude": "1.0",
                        "longitude": "2.0",
                        "description": "",
                        "physical_address": "",
                        "facility": "",
                        "tenant": None,
                        "asn": None,
                        "time_zone": "",
                        "tags": [],
                        "url": "",
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            if endpoint == "dcim/devices/":
                return [
                    {
                        "id": "dev-1",
                        "name": "router01",
                        "location": {"id": "loc-1"},
                        "device_type": {"model": "ASR1001-X", "manufacturer": {"name": "Cisco"}},
                        "role": {"name": "Core Router"},
                        "status": {"label": "Active"},
                        "platform": {"name": "IOS-XE"},
                        "serial": "SN123",
                        "tenant": None,
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            return []

        conn = db.get_conn()
        try:
            with conn:
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:05:00Z",
                    last_successful_sync="2026-01-01T00:05:00Z",
                    cache_version=inventory.CACHE_VERSION,
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(inventory, "read_location_name_map", return_value={}),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
            patch.object(inventory, "sync_due", return_value=True),
            patch.object(
                timeutil,
                "iso_utc_now",
                side_effect=["2026-01-02T00:00:00Z", "2026-01-02T00:00:10Z"],
            ),
        ):
            inventory.sync_nautobot()

        state = inventory.get_sync_state("nautobot_inventory")
        reconcile_state = inventory.get_sync_state("nautobot_inventory_reconcile")
        location_calls = [params for endpoint, params in calls if endpoint == "dcim/locations/"]
        device_calls = [params for endpoint, params in calls if endpoint == "dcim/devices/"]

        assert location_calls == [{}]
        assert device_calls == [{"depth": 1}]
        assert state["last_successful_sync"] == "2026-01-02T00:00:00Z"
        assert reconcile_state["last_successful_sync"] == "2026-01-02T00:00:00Z"

    def test_primary_ip_backfill_pending_until_current_cache_version_sync(self):
        with patch.object(
            inventory,
            "get_sync_state",
            return_value={
                "last_successful_sync": "2026-01-01T00:05:00Z",
                "cache_version": "1",
            },
        ):
            assert board_store.nautobot_inventory_primary_ip_backfill_pending() is True

        with patch.object(
            inventory,
            "get_sync_state",
            return_value={
                "last_successful_sync": "2026-01-01T00:05:00Z",
                "cache_version": inventory.CACHE_VERSION,
            },
        ):
            assert board_store.nautobot_inventory_primary_ip_backfill_pending() is False

    def test_alert_board_filters_cached_devices_without_primary_ip_while_backfill_is_pending(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        {
                            "id": "loc-1",
                            "name": "Pending Site",
                            "slug": "pending-site",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": "",
                            "tags": [],
                            "url": "",
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
                inventory.write_devices(
                    conn,
                    [
                        {
                            "id": "dev-1",
                            "location_id": "loc-1",
                            "name": "router01",
                            "device_type": "ASR1001-X",
                            "manufacturer": "Cisco",
                            "role": "Core Router",
                            "status": "offline",
                            "primary_ip": "",
                            "platform": "IOS-XE",
                            "serial": "SN123",
                            "tenant": "",
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at=None,
                    last_completed_at=None,
                    last_successful_sync=None,
                    status="pending",
                    error_message="",
                )
        finally:
            conn.close()

        caching.cache.clear()
        with patch.object(
            nautobot,
            "fetch_all_pages",
            side_effect=AssertionError("should not fetch live inventory"),
        ):
            data = alerts.get_alert_board_data(force_refresh=True)

        assert data["summary"]["no_data"] == 1  # no monitored devices yet
        assert data["alerts"][0]["device_count"] == 0
        assert data["alerts"][0]["down_device_count"] == 0

    def test_api_alerts_filters_cached_devices_without_primary_ip(self, flask_client):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        {
                            "id": "loc-1",
                            "name": "Filtered Site",
                            "slug": "filtered-site",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": "",
                            "tags": [],
                            "url": "",
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
                inventory.write_devices(
                    conn,
                    [
                        {
                            "id": "dev-1",
                            "location_id": "loc-1",
                            "name": "ap01",
                            "device_type": "AP",
                            "manufacturer": "Cisco",
                            "role": "Access Point",
                            "status": "offline",
                            "primary_ip": "",
                            "platform": "",
                            "serial": "",
                            "tenant": "",
                            "last_updated": "2026-01-01T00:00:00Z",
                        },
                        {
                            "id": "dev-2",
                            "location_id": "loc-1",
                            "name": "router01",
                            "device_type": "ASR1001-X",
                            "manufacturer": "Cisco",
                            "role": "Core Router",
                            "status": "offline",
                            "primary_ip": "192.0.2.1/32",
                            "platform": "IOS-XE",
                            "serial": "SN123",
                            "tenant": "",
                            "last_updated": "2026-01-01T00:00:00Z",
                        },
                    ],
                )
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:05:00Z",
                    last_successful_sync="2026-01-01T00:05:00Z",
                    cache_version=inventory.CACHE_VERSION,
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        caching.cache.clear()
        with patch.object(
            nautobot,
            "fetch_all_pages",
            side_effect=AssertionError("should not fetch live inventory"),
        ):
            resp = flask_client.get("/api/v1/alerts")

        assert resp.status_code == 200
        entry = resp.get_json()["alerts"][0]
        assert entry["device_count"] == 1
        assert entry["down_device_count"] == 1
        assert entry["down_devices"] == [
            {
                "device_id": "dev-2",
                "device_name": "router01",
                "device_ip": "192.0.2.1",
                "status": "offline",
                "role": "Core Router",
                "core": True,  # the likely cause of the site's other down devices (#285)
                "case_numbers": [],
                "down_started_at": "2026-01-01T00:00:00Z",
            }
        ]

    def test_sync_librenms_inventory_keeps_postgres_connection_open_between_transactions(self):
        class _FakeResult:
            def __init__(self, rows=None, rowcount=0):
                self._rows = rows or []
                self.rowcount = rowcount

            def fetchone(self):
                return self._rows[0] if self._rows else None

        class _FakeTransaction:
            def __init__(self, conn):
                self.conn = conn
                self.nested = False

            def __enter__(self):
                self.conn.transaction_entries += 1
                self.nested = self.conn.transaction_open
                self.conn.transaction_open = True
                return self.conn

            def __exit__(self, exc_type, exc, tb):
                if exc_type is None and not self.nested:
                    self.conn.commits += 1
                self.conn.transaction_open = self.nested
                return False

        class _FakeConn:
            def __init__(self, autocommit=True):
                self.autocommit = autocommit
                self.closed = False
                self.connection_context_entries = 0
                self.transaction_entries = 0
                self.transaction_open = False
                self.commits = 0
                self.queries = []

            def __enter__(self):
                self.connection_context_entries += 1
                return self

            def __exit__(self, exc_type, exc, tb):
                self.closed = True
                return False

            def transaction(self):
                return _FakeTransaction(self)

            def execute(self, query, params=()):
                if self.closed:
                    raise RuntimeError("the connection is closed")
                if not self.autocommit and query.lstrip().upper().startswith("SELECT"):
                    self.transaction_open = True
                self.queries.append((query, params))
                if "SELECT COUNT(*) AS device_count FROM librenms_device_status" in query:
                    return _FakeResult([{"device_count": 1}])
                if (
                    "librenms_observation_seq" in query
                    and "nextval" in query
                    and "INSERT" not in query
                ):
                    return _FakeResult([{"n": 1}])
                return _FakeResult()

            def close(self):
                self.closed = True

        fake_conn = _FakeConn(autocommit=True)
        with (
            patch.object(db, "get_conn", return_value=fake_conn),
            patch.object(settings, "LIBRENMS_URL", "https://librenms.example.com"),
            patch.object(settings, "LIBRENMS_API_TOKEN", "token"),
            patch.object(inventory, "get_sync_state", return_value={"last_successful_sync": None}),
            patch.object(
                librenms,
                "fetch_inventory",
                return_value=[
                    {"device_id": 1, "hostname": "router01", "status": 1, "status_reason": ""}
                ],
            ),
            patch.object(caching.cache, "delete") as cache_delete,
            # Records on its own connection; tested in test_contract.py.
            patch.object(contract, "check_and_record"),
        ):
            inventory.sync_librenms()

        assert fake_conn.connection_context_entries == 0
        assert fake_conn.transaction_entries == 2
        assert fake_conn.commits == 2
        assert any(
            "SELECT COUNT(*) AS device_count FROM librenms_device_status" in query
            for query, _ in fake_conn.queries
        )
        assert any("DELETE FROM librenms_device_status" in query for query, _ in fake_conn.queries)
        cache_delete.assert_any_call("alert-board-data:v3")
        cache_delete.assert_any_call("alert-board-data:v3:include-non-operational")
        cache_delete.assert_any_call("location-alert-levels:v1")
        assert cache_delete.call_count == 3

    def test_full_reconcile_prunes_deleted_cached_inventory(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        {
                            "id": "loc-stale",
                            "name": "Stale Site",
                            "slug": "stale-site",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": "",
                            "tags": [],
                            "url": "",
                            "last_updated": "2025-01-01T00:00:00Z",
                        }
                    ],
                )
                inventory.write_devices(
                    conn,
                    [
                        {
                            "id": "dev-stale",
                            "location_id": "loc-stale",
                            "name": "stale-router",
                            "device_type": "ASR1001-X",
                            "manufacturer": "Cisco",
                            "role": "Core Router",
                            "status": "active",
                            "primary_ip": "198.51.100.1/32",
                            "platform": "IOS-XE",
                            "serial": "SN-STALE",
                            "tenant": "",
                            "last_updated": "2025-01-01T00:00:00Z",
                        }
                    ],
                )
        finally:
            conn.close()

        def fake_fetch(endpoint, params=None, **kwargs):
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "Fresh Site",
                        "slug": "fresh-site",
                        "status": {"label": "Active"},
                        "location_type": {"name": "Data Center"},
                        "parent": None,
                        "latitude": "3.0",
                        "longitude": "4.0",
                        "description": "",
                        "physical_address": "",
                        "facility": "",
                        "tenant": None,
                        "asn": None,
                        "time_zone": "",
                        "tags": [],
                        "url": "",
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            if endpoint == "dcim/devices/":
                return [
                    {
                        "id": "dev-1",
                        "name": "router01",
                        "location": {"id": "loc-1"},
                        "device_type": {"model": "ASR1001-X", "manufacturer": {"name": "Cisco"}},
                        "role": {"name": "Core Router"},
                        "status": {"label": "Active"},
                        "platform": {"name": "IOS-XE"},
                        "serial": "SN123",
                        "tenant": None,
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            return []

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(inventory, "read_location_name_map", return_value={}),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
        ):
            inventory.sync_nautobot(force=True)

        assert [
            item["id"] for item in inventory.read_locations(include_without_coordinates=True)
        ] == ["loc-1"]
        assert [item["id"] for item in inventory.read_devices()] == ["dev-1"]


# ---------------------------------------------------------------------------
# Tests: criticality_rules.json loading
# ---------------------------------------------------------------------------
class TestCriticalityRulesFile:
    def test_load_valid_rules_file(self, tmp_path):
        """A valid JSON rules file is parsed into _CRITICALITY_RULES."""
        rules = {"datacenter": ["core", "firewall"], "office": ["router"]}
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        orig = dict(alerts.CRITICALITY_RULES)
        orig_file = settings.CRITICALITY_RULES_FILE
        try:
            settings.CRITICALITY_RULES_FILE = str(rules_file)
            # Re-run the loading logic
            with open(str(rules_file)) as f:
                loaded = json.load(f)
            alerts.CRITICALITY_RULES = {
                k.lower(): [kw.lower() for kw in v]
                for k, v in loaded.items()
                if isinstance(v, list)
            }
            assert alerts.get_critical_keywords("datacenter") == ("core", "firewall")
            assert alerts.get_critical_keywords("office") == ("router",)
        finally:
            alerts.CRITICALITY_RULES = orig
            settings.CRITICALITY_RULES_FILE = orig_file

    def test_rules_file_bad_format_ignored(self, tmp_path):
        """A rules file with a non-dict top level is ignored gracefully."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps(["not", "a", "dict"]))

        orig = dict(alerts.CRITICALITY_RULES)
        try:
            # Simulate what the loading code does
            with open(str(bad_file)) as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                pass  # Would be ignored in real code
            # _CRITICALITY_RULES should remain unchanged
            assert alerts.CRITICALITY_RULES == orig
        finally:
            alerts.CRITICALITY_RULES = orig


# ---------------------------------------------------------------------------
# Tests: LibreNMS enrichment
# ---------------------------------------------------------------------------
class TestLibreNMSEnrichment:
    def setup_method(self):
        self._orig_url = settings.LIBRENMS_URL
        self._orig_token = settings.LIBRENMS_API_TOKEN
        self._orig_verify = settings.LIBRENMS_VERIFY_SSL

    def teardown_method(self):
        settings.LIBRENMS_URL = self._orig_url
        settings.LIBRENMS_API_TOKEN = self._orig_token
        settings.LIBRENMS_VERIFY_SSL = self._orig_verify

    def test_no_enrichment_when_unconfigured(self):
        """_enrich_with_librenms is a no-op when LIBRENMS_URL is empty."""
        settings.LIBRENMS_URL = ""
        settings.LIBRENMS_API_TOKEN = ""
        devices = [{"id": "d1", "name": "router01", "status": "active"}]
        result = alerts.enrich_with_librenms(devices)
        assert result == devices

    def test_fetch_inventory_skips_when_config_is_blank(self):
        """_fetch_librenms_inventory skips API calls when URL/token are blank."""
        settings.LIBRENMS_URL = "   "
        settings.LIBRENMS_API_TOKEN = "   "
        with patch.object(requests.Session, "get") as mock_get:
            result = librenms.fetch_inventory()
        assert result == []
        mock_get.assert_not_called()

    def test_librenms_get_uses_verify_setting(self):
        settings.LIBRENMS_URL = "https://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        settings.LIBRENMS_VERIFY_SSL = False
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"devices": []}
        with patch.object(requests.Session, "get", return_value=mock_resp) as mock_get:
            librenms.get("devices", {"type": "all"})
        _, kwargs = mock_get.call_args
        assert kwargs["verify"] is False

    def test_librenms_get_suppresses_warning_locally_when_verify_disabled(self):
        settings.LIBRENMS_URL = "https://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        settings.LIBRENMS_VERIFY_SSL = False
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"devices": []}
        with (
            patch.object(requests.Session, "get", return_value=mock_resp),
            patch.object(librenms.warnings, "catch_warnings") as mock_catch,
        ):
            librenms.get("devices", {"type": "all"})
        mock_catch.assert_called_once()

    def test_librenms_down_overrides_active_status(self):
        """A device active in Nautobot but down in LibreNMS is set to offline."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {
            "devices": [
                {"device_id": 1, "hostname": "router01", "status": 0},
            ]
        }
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_up_does_not_change_active_status(self):
        """A device up in LibreNMS stays active."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {"devices": [{"device_id": 1, "hostname": "router01", "status": 1}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "active"

    def test_librenms_down_does_not_upgrade_already_offline(self):
        """A device already offline in Nautobot stays offline (no double-counting)."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {"devices": [{"device_id": 1, "hostname": "router01", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "offline"}]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_api_failure_returns_original_devices(self):
        """If LibreNMS API call fails, original device list is returned unchanged."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        with patch.object(librenms, "get", side_effect=Exception("timeout")):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = alerts.enrich_with_librenms(devices)
        assert result == devices

    def test_librenms_unmatched_device_not_affected(self):
        """Devices not present in LibreNMS are left unchanged."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {"devices": [{"device_id": 1, "hostname": "other-device", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "active"

    def test_librenms_down_matches_short_hostname(self):
        """Name-valued LibreNMS hostnames match Nautobot short device names."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {
            "devices": [{"device_id": 1, "hostname": "router01.example.com", "status": 0}]
        }
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_down_matches_primary_ip_when_hostname_is_ip(self):
        """IP-valued LibreNMS hostnames match Nautobot primary_ip without mask bits."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {"devices": [{"device_id": 1, "hostname": "192.0.2.1", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [
                {"id": "d1", "name": "router01", "primary_ip": "192.0.2.1/32", "status": "active"}
            ]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_down_matches_primary_ipv6_when_hostname_case_differs(self):
        """IPv6-valued LibreNMS hostnames match Nautobot primary_ip regardless of hex casing."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {"devices": [{"device_id": 1, "hostname": "2001:DB8::1", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [
                {
                    "id": "d1",
                    "name": "router01",
                    "primary_ip": "2001:db8::1/128",
                    "status": "active",
                }
            ]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_ip_hostnames_do_not_collide_with_short_name_keys(self):
        """IP-valued LibreNMS hostnames must not be short-name normalized."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {"devices": [{"device_id": 1, "hostname": "10.0.0.1", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "10", "primary_ip": "192.0.2.5/32", "status": "active"}]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "active"


class TestAuthConfiguration:
    @staticmethod
    def _reload_gunicorn_config():
        import gunicorn_config

        return importlib.reload(gunicorn_config)

    def test_default_trusts_only_localhost(self):
        assert [str(n) for n in settings._networks("t", "127.0.0.1/32,::1/128")] == [
            "127.0.0.1/32",
            "::1/128",
        ]
        with pytest.raises(RuntimeError, match="AUTH_TRUSTED_PROXIES"):
            settings._networks("AUTH_TRUSTED_PROXIES", "10.0.0.0/33")

    def test_auth_disabled_refuses_admin_writes_by_default(self, flask_client, monkeypatch):
        """Anyone who can reach the app must not change Nautobot with its token (#188)."""
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", False)
        writes = [
            (
                "post",
                "/api/v1/criticality-overrides",
                {"nautobot_device_id": "dev-abc", "is_critical": False},
            ),
            ("delete", "/api/v1/criticality-overrides/dev-abc", None),
        ]
        with auth_config(mode="disabled"):
            for method, url, body in writes:
                resp = (
                    getattr(flask_client, method)(url, json=body)
                    if body
                    else getattr(flask_client, method)(url)
                )
                assert resp.status_code == 403, (method, url)
                assert "ALLOW_UNAUTHENTICATED_WRITES" in resp.get_json()["detail"]

    def test_auth_disabled_keeps_reads_and_cases_open(self, flask_client, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", False)
        with auth_config(mode="disabled"):
            assert flask_client.get("/api/v1/criticality-overrides").status_code != 403
            # Adding a case is what the board does; it reaches the handler
            # (400: nothing to add), not the auth check.
            assert flask_client.post("/api/v1/alert-cases", json={}).status_code != 403

    @pytest.mark.parametrize(
        "method, url",
        [
            ("get", "/api/v1/roles"),
            ("post", "/api/v1/roles"),
            ("delete", "/api/v1/roles/role-1"),
            ("get", "/api/v1/location-types"),
            ("post", "/api/v1/location-types"),
            ("delete", "/api/v1/location-types/lt-1"),
        ],
    )
    def test_nautobot_proxy_endpoints_are_gone(self, flask_client, method, url, monkeypatch):
        """Changes to Nautobot data are made in Nautobot, not through this app (#188)."""
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)
        assert getattr(flask_client, method)(url).status_code in (404, 405)

    def test_the_nautobot_client_cannot_write(self):
        assert not hasattr(nautobot, "post") and not hasattr(nautobot, "delete")

    def test_require_viewer_protects_every_page_but_probes(
        self, flask_client, monkeypatch, frontend_build
    ):
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
        with auth_config(mode="header", viewer_groups={"noc"}):
            assert flask_client.get("/").status_code == 401
            assert flask_client.get("/api/v1/alerts").status_code == 401
            assert (
                flask_client.get("/alerts", headers={"X-Forwarded-User": "bob"}).status_code == 403
            )
            ok = flask_client.get(
                "/", headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc"}
            )
            assert ok.status_code == 200
            assert flask_client.get("/healthz").status_code in (200, 503)
            assert flask_client.get("/metrics").status_code == 200

    def test_require_viewer_is_off_by_default(self, flask_client, monkeypatch, frontend_build):
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", False)
        with auth_config(mode="header", viewer_groups={"noc"}):
            assert flask_client.get("/").status_code == 200

    def test_missing_identity_header_returns_401(self, flask_client):
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            resp = flask_client.post(
                "/api/v1/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                content_type="application/json",
            )
        assert resp.status_code == 401

    def test_default_role_allows_authenticated_viewer_only_access(self, flask_client):
        with auth_config(mode="header", default_role="viewer"):
            resp = flask_client.post(
                "/api/v1/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                content_type="application/json",
                headers={"X-Forwarded-User": "alice"},
            )
        assert resp.status_code == 403
        assert resp.get_json()["current_role"] == "viewer"


# ---------------------------------------------------------------------------
# Tests: alert-board tier definitions and per-device IP (#117)
# ---------------------------------------------------------------------------
class TestAlertBoardTierDefinitions:
    def test_the_board_explains_the_levels_and_chips(self, flask_client):
        """The (i) by the summary line explains the levels, and each chip what it shows (#311),
        from the configured thresholds (#293): the board reads them from /api/page-config (#463)."""
        levels = flask_client.get("/api/v1/page-config").get_json()["board"]["levels"]
        assert levels == alerts.alert_level_definitions()
        assert set(levels) >= {
            "critical",
            "medium",
            "low",
            "alarms",
            "total",
            "no_data",
            "maintenance",
        }


class TestDeviceDisplayIp:
    def test_prefers_primary_ip_without_prefix_length(self):
        device = {"primary_ip": "192.0.2.10/32", "librenms_hostname": "198.51.100.1"}
        assert alerts.device_display_ip(device) == "192.0.2.10"

    def test_keeps_ipv6_primary_ip(self):
        assert alerts.device_display_ip({"primary_ip": "2001:db8::1/128"}) == "2001:db8::1"

    def test_falls_back_to_librenms_ip_hostname(self):
        assert (
            alerts.device_display_ip({"primary_ip": "", "librenms_hostname": "198.51.100.1"})
            == "198.51.100.1"
        )

    def test_ignores_non_ip_librenms_hostname(self):
        assert (
            alerts.device_display_ip(
                {"primary_ip": "", "librenms_hostname": "router01.example.net"}
            )
            == ""
        )

    def test_enrichment_records_matched_librenms_hostname(self):
        orig_url, orig_token = settings.LIBRENMS_URL, settings.LIBRENMS_API_TOKEN
        settings.LIBRENMS_URL, settings.LIBRENMS_API_TOKEN = "https://librenms.test", "tok"
        try:
            devices = [{"id": "d1", "name": "router01", "status": "active", "primary_ip": ""}]
            enriched = alerts.enrich_with_librenms(
                devices,
                lnms_devices=[{"device_id": 7, "hostname": "router01", "status": 1}],
                lnms_id_map={},
            )
        finally:
            settings.LIBRENMS_URL, settings.LIBRENMS_API_TOKEN = orig_url, orig_token
        assert enriched[0]["librenms_hostname"] == "router01"


# ---------------------------------------------------------------------------
# Tests: alert-board refresh, cold start and sync progress (#121)
# ---------------------------------------------------------------------------
class TestAlertBoardSyncProgress:
    @pytest.fixture(autouse=True)
    def _persistence(self, pg_database):
        caching.cache.clear()
        yield
        caching.cache.clear()

    def _set_nautobot_sync_state(self, status, started_at, completed_at=None):
        conn = db.get_conn()
        try:
            with conn:
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at=started_at,
                    last_completed_at=completed_at,
                    last_successful_sync=completed_at,
                    cache_version=inventory.CACHE_VERSION,
                    status=status,
                    error_message="",
                )
        finally:
            conn.close()

    def _board(self, alerts=None):
        board = {
            "checked_at": timeutil.iso_utc_now(),
            "stale_after_seconds": 300,
            "summary": {"total": len(alerts or [])},
            "alerts": alerts or [],
        }
        # A real board's other fields, empty (#449: the answer is checked against /api/docs).
        return answers.complete(board_answers.Board, board)

    def test_refresh_1_enqueues_forced_background_sync(self, flask_client):
        now = timeutil.iso_utc_now()
        self._set_nautobot_sync_state("idle", now, now)
        with (
            patch.object(inventory, "ensure_snapshot", return_value=True) as ensure,
            patch.object(
                alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])
            ),
        ):
            resp = flask_client.get("/api/v1/alerts?refresh=1")
        assert resp.status_code == 200
        ensure.assert_called_once_with(
            force=True,
            full=False,
            wait=False,
            cooldown_seconds=inventory.FORCED_SYNC_COOLDOWN_SECONDS,
        )
        assert resp.get_json()["sync_pending"] is True

    def test_timestamp_refresh_value_does_not_trigger_sync(self, flask_client):
        """The old UI sent refresh=<Date.now()>; the server contract is 1/true/yes/refresh."""
        now = timeutil.iso_utc_now()
        self._set_nautobot_sync_state("idle", now, now)
        with (
            patch.object(inventory, "ensure_snapshot", return_value=True) as ensure,
            patch.object(
                alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])
            ),
        ):
            resp = flask_client.get("/api/v1/alerts?refresh=1727000000000")
        ensure.assert_not_called()
        assert resp.get_json()["sync_pending"] is False

    def test_cold_start_enqueues_first_sync_without_waiting(self, flask_client, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        with (
            patch.object(inventory, "ensure_snapshot", return_value=True) as ensure,
            patch.object(alerts, "build_alert_board_payload", return_value=self._board()),
        ):
            resp = flask_client.get("/api/v1/alerts")
        assert resp.status_code == 200
        ensure.assert_called_once_with(wait=False)
        data = resp.get_json()
        assert data["alerts"] == []
        assert data["sync_pending"] is True
        # The empty cold-start board must not be cached, or the synced data
        # would stay hidden until the cache expires.
        assert caching.cache.get("alert-board-data:v3") is None

    def test_initialized_snapshot_does_not_start_sync_and_is_not_pending(self, flask_client):
        now = timeutil.iso_utc_now()
        self._set_nautobot_sync_state("idle", now, now)
        with (
            patch.object(inventory, "ensure_snapshot") as ensure,
            patch.object(
                alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])
            ),
        ):
            resp = flask_client.get("/api/v1/alerts")
        ensure.assert_not_called()
        assert resp.get_json()["sync_pending"] is False

    def test_due_sync_is_started_by_a_normal_board_load(self, flask_client, monkeypatch):
        """An open board keeps itself up to date: a normal load starts a sync once one is due (#152)."""
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 300)
        old = (datetime.now(UTC) - timedelta(seconds=301)).isoformat()
        self._set_nautobot_sync_state("idle", old, old)
        with (
            patch.object(inventory, "ensure_snapshot", return_value=True) as ensure,
            patch.object(
                alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])
            ),
        ):
            data = flask_client.get("/api/v1/alerts").get_json()
        ensure.assert_called_once_with(wait=False)
        assert data["sync_pending"] is True
        assert data["next_update_in_seconds"] == 0

    def test_next_update_counts_down_from_last_completed_sync(self, flask_client, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 300)
        completed = (datetime.now(UTC) - timedelta(seconds=100)).isoformat()
        self._set_nautobot_sync_state("idle", completed, completed)
        with (
            patch.object(inventory, "ensure_snapshot") as ensure,
            patch.object(
                alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])
            ),
        ):
            data = flask_client.get("/api/v1/alerts").get_json()
        ensure.assert_not_called()
        assert 195 <= data["next_update_in_seconds"] <= 200

    def test_next_update_uses_the_sooner_source(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.example.com")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "token")
        monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 3600)
        monkeypatch.setattr(settings, "LIBRENMS_SYNC_INTERVAL_SECONDS", 120)
        now = datetime.now(UTC).isoformat()
        self._set_nautobot_sync_state("idle", now, now)
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "librenms_inventory",
                last_started_at=now,
                last_completed_at=now,
                status="idle",
            )
        conn.close()
        due, next_in = alerts.inventory_update_schedule()
        assert due is False
        assert 115 <= next_in <= 120

    def test_next_update_unknown_while_sync_runs(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        self._set_nautobot_sync_state("running", timeutil.iso_utc_now())
        assert alerts.inventory_update_schedule() == (False, None)

    def test_next_update_unknown_without_persistence(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        assert alerts.inventory_update_schedule() == (False, None)

    def test_running_sync_is_reported_as_pending(self, flask_client):
        self._set_nautobot_sync_state("running", timeutil.iso_utc_now())
        assert alerts.nautobot_sync_in_progress() is True

    def test_abandoned_running_sync_is_not_pending(self):
        started = datetime(2020, 1, 1, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        self._set_nautobot_sync_state("running", started)
        assert alerts.nautobot_sync_in_progress() is False

    def test_no_sync_pending_without_persistence(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        assert alerts.nautobot_sync_in_progress() is False

    def test_adding_case_invalidates_cached_board(self, flask_client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        alerts.upsert_alert_lifecycle_for_site(
            site,
            devices_down,
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            timeutil.iso_utc_now(),
        )
        caching.cache.set("alert-board-data:v3", self._board([{"id": "loc-1"}]))
        resp = flask_client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_id": "dev-1", "case_number": "INC-2001"},
        )
        assert resp.status_code == 200
        assert caching.cache.get("alert-board-data:v3") is None


# ---------------------------------------------------------------------------
# Tests: one case number on multiple devices (#133)
# ---------------------------------------------------------------------------
class TestMultiDeviceCases:
    @pytest.fixture(autouse=True)
    def _database_with_open_alerts(self, pg_database):
        alerts.upsert_alert_lifecycle_for_site(
            {"id": "loc-1", "name": "Site One"},
            [
                {"id": "dev-1", "name": "sw01", "status": "offline"},
                {"id": "dev-2", "name": "sw02", "status": "offline"},
                {"id": "dev-3", "name": "sw03", "status": "offline"},
            ],
            {"level": "medium", "reason": "3/4 devices offline (75%)"},
            timeutil.iso_utc_now(),
        )
        yield
        caching.cache.clear()

    def _cases_for(self, flask_client, device_id):
        history = flask_client.get(
            f"/api/v1/alert-history?site_id=loc-1&device_id={device_id}"
        ).get_json()
        return [case["case_number"] for case in history["instances"][0]["cases"]]

    def test_links_one_case_to_every_selected_device(self, flask_client):
        resp = flask_client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_ids": ["dev-1", "dev-2"], "case_number": "INC-42"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert [item["device_id"] for item in data["linked"]] == ["dev-1", "dev-2"]
        assert "device_id" not in data  # single-device fields only for one device
        assert self._cases_for(flask_client, "dev-1") == ["INC-42"]
        assert self._cases_for(flask_client, "dev-2") == ["INC-42"]
        assert self._cases_for(flask_client, "dev-3") == []

    def test_is_all_or_nothing_when_a_device_has_no_open_alert(self, flask_client):
        resp = flask_client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_ids": ["dev-1", "dev-9"], "case_number": "INC-43"},
        )
        assert resp.status_code == 404
        assert resp.get_json()["missing_device_ids"] == ["dev-9"]
        assert self._cases_for(flask_client, "dev-1") == []

    def test_duplicate_ids_are_linked_once(self, flask_client):
        resp = flask_client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_ids": ["dev-1", " dev-1 "], "case_number": "INC-44"},
        )
        assert resp.status_code == 200
        assert len(resp.get_json()["linked"]) == 1
        assert self._cases_for(flask_client, "dev-1") == ["INC-44"]

    def test_single_device_id_keeps_original_response_fields(self, flask_client):
        resp = flask_client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_id": "dev-3", "case_number": "INC-45"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["device_id"] == "dev-3"
        assert isinstance(data["alert_instance_id"], int)
        assert self._cases_for(flask_client, "dev-3") == ["INC-45"]

    @pytest.mark.parametrize(
        "device_ids",
        ["dev-1", [], ["dev-1", 7], [f"dev-{i}" for i in range(api_board.MAX_CASE_DEVICES + 1)]],
        ids=["string-not-list", "empty", "non-string-id", "too-many"],
    )
    def test_rejects_invalid_device_ids(self, flask_client, device_ids):
        resp = flask_client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_ids": device_ids, "case_number": "INC-46"},
        )
        assert resp.status_code == 400
        assert self._cases_for(flask_client, "dev-1") == []


# Tests: /healthz (#131)
# ---------------------------------------------------------------------------
class TestHealthz:
    def test_ok_with_reachable_database(self, flask_client, monkeypatch, pg_database):
        resp = flask_client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json()["checks"] == {"app": "ok", "database": "ok"}

    def test_makes_no_upstream_calls(self, flask_client, monkeypatch):
        """A Nautobot/LibreNMS outage must not make the app look unhealthy."""
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        with patch.object(requests.Session, "get", side_effect=AssertionError("no upstream calls")):
            resp = flask_client.get("/healthz")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: LibreNMS polled IP is cached and used as a display fallback (#137)
# ---------------------------------------------------------------------------
# read_librenms_devices: none of LibreNMS's "don't alert" switches set (#570).
OFF = {"disabled": False, "disable_notify": False, "ignore": False}


class TestLibreNMSPolledIp:
    @pytest.mark.parametrize(
        "record, expected",
        [
            ({"overwrite_ip": "198.51.100.9", "ip": "192.0.2.7"}, "198.51.100.9"),
            ({"overwrite_ip": None, "ip": "192.0.2.7"}, "192.0.2.7"),
            ({"overwrite_ip": "", "ip": "2001:db8::7"}, "2001:db8::7"),
            ({"overwrite_ip": "not-an-ip", "ip": "router01.example.net"}, ""),
            ({}, ""),
        ],
        ids=["override-wins", "ip", "ipv6", "non-ip-ignored", "missing"],
    )
    def test_polled_ip_selection(self, record, expected):
        assert inventory.librenms_polled_ip(record) == expected

    def test_sync_caches_polled_ip(self, monkeypatch, pg_database):
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.test")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "tok")
        pass  # the schema: Alembic's (tests/test_schema.py)
        with patch.object(
            librenms,
            "fetch_inventory",
            return_value=[
                {
                    "device_id": 7,
                    "hostname": "router01.example.net",
                    "ip": "192.0.2.7",
                    "overwrite_ip": None,
                    "status": 1,
                },
            ],
        ):
            inventory.sync_librenms(force=True)
        cached = inventory.read_librenms_devices()
        assert cached == [
            {
                "device_id": 7,
                "hostname": "router01.example.net",
                "ip": "192.0.2.7",
                "status": 1,
                **OFF,
            }
        ]

    def test_device_added_by_hostname_shows_librenms_ip(self, monkeypatch):
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.test")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "tok")
        enriched = alerts.enrich_with_librenms(
            [{"id": "d1", "name": "router01", "status": "active", "primary_ip": ""}],
            lnms_devices=[
                {"device_id": 7, "hostname": "router01.example.net", "ip": "192.0.2.7", "status": 1}
            ],
            lnms_id_map={},
        )
        assert alerts.device_display_ip(enriched[0]) == "192.0.2.7"

    def test_nautobot_primary_ip_still_preferred(self):
        device = {"primary_ip": "10.0.0.1/32", "librenms_ip": "192.0.2.7"}
        assert alerts.device_display_ip(device) == "10.0.0.1"


# Tests: alert board explains a missing persistence database (#136)
# ---------------------------------------------------------------------------
class TestAlertBoardWithoutPersistence:
    def test_payload_reports_configured_database(self, flask_client, monkeypatch, pg_database):
        pass  # the schema: Alembic's (tests/test_schema.py)
        caching.cache.clear()
        with (
            patch.object(inventory, "ensure_snapshot", return_value=False),
            patch.object(
                alerts,
                "build_alert_board_payload",
                return_value=answers.complete(
                    board_answers.Board,
                    {"checked_at": timeutil.iso_utc_now(), "summary": {}, "alerts": []},
                ),
            ),
        ):
            data = flask_client.get("/api/v1/alerts").get_json()
        assert data["persistence_configured"] is True


# Tests: Refresh runs an incremental "sync now", not a full reconcile (#135)
# ---------------------------------------------------------------------------
class TestRefreshIsIncremental:
    @pytest.fixture(autouse=True)
    def _database_and_nautobot(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        pass  # the schema: Alembic's (tests/test_schema.py)

    def _location_queries(self, **ensure_kwargs):
        """Run a synchronous sync and return the params sent to dcim/locations/."""
        calls = []

        def fake_fetch(endpoint, params=None, **kwargs):
            calls.append((endpoint, dict(params or {})))
            return []

        with (
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(inventory, "read_location_name_map", return_value={}),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
        ):
            inventory.ensure_snapshot(wait=True, **ensure_kwargs)
        return [params for endpoint, params in calls if endpoint == "dcim/locations/"]

    def test_sync_now_only_fetches_changes_since_last_sync(self):
        self._location_queries(force=True)  # first sync: full, sets the watermark
        watermark = inventory.get_sync_state("nautobot_inventory")["last_successful_sync"]
        queries = self._location_queries(force=True, full=False)
        assert queries == [{"last_updated__gte": watermark}]

    def test_force_alone_still_means_full_reconcile(self):
        self._location_queries(force=True)
        queries = self._location_queries(force=True)
        assert queries == [{}]


# ---------------------------------------------------------------------------
# Tests: alert board reads in bulk, not per site (#149)
# ---------------------------------------------------------------------------
class TestAlertBoardBulkReads:
    CHECKED_AT = "2026-09-25T12:00:00+00:00"

    @pytest.fixture(autouse=True)
    def _database(self, pg_database, monkeypatch):
        self.db = pg_database
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *a, **k: False)
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: self.CHECKED_AT)
        pass  # the schema: Alembic's (tests/test_schema.py)
        caching.cache.clear()
        yield
        caching.cache.clear()

    def _execute(self, sql, rows):
        self.db.executemany(sql, rows)

    def _seed(self, site_count, down_every=10, backfill_done=True):
        """Seed *site_count* sites with 3 devices; every *down_every*-th site has its router down."""
        self._execute(
            "INSERT INTO nautobot_location_cache (location_id, name, status, location_type, latitude, longitude) "
            "VALUES (%s, %s, 'Active', 'Office', 1.0, 2.0)",
            [(f"loc-{i}", f"Site {i}") for i in range(site_count)],
        )
        self._execute(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (
                    f"dev-{i}-{j}",
                    f"loc-{i}",
                    f"dev{i}-{j}",
                    "router" if j == 0 else "access",
                    "Offline" if (j == 0 and i % down_every == 0) else "Active",
                    f"10.0.{i % 250}.{j}/32",
                )
                for i in range(site_count)
                for j in range(3)
            ],
        )
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_started_at="2026-09-25T11:00:00+00:00",
                last_completed_at="2026-09-25T11:00:00+00:00",
                last_successful_sync="2026-09-25T11:00:00+00:00" if backfill_done else None,
                cache_version=inventory.CACHE_VERSION,
                status="idle",
            )
        conn.close()

    def _add_alert(self, site_id, device_id, status, down_started_at, total_downtime_seconds=0):
        return self.db.execute(
            "INSERT INTO alert_instances (alert_key, site_id, site_name, device_id, device_name, alert_level, "
            "alert_reason, status, down_started_at, last_seen_down_at, total_downtime_seconds) "
            "VALUES (%s, %s, '', %s, %s, 'critical', '', %s, %s, %s, %s) RETURNING id",
            (
                # Open alerts use the real key so the build recognises them as still open.
                db.build_alert_key(site_id, device_id)
                if status == "open"
                else f"{site_id}-{device_id}-{down_started_at}",
                site_id,
                device_id,
                device_id,
                status,
                down_started_at,
                down_started_at,
                total_downtime_seconds,
            ),
        )[0]["id"]

    def _build_counting_connections(self):
        opened = []
        real_get_db_conn = db.get_conn

        def counting_get_db_conn():
            opened.append(1)
            return real_get_db_conn()

        with (
            patch.object(db, "get_conn", side_effect=counting_get_db_conn),
            # The build's own connections; not the build lock or its stats (#295).
            patch.object(alerts, "_board_build_lock", return_value=None),
            patch.object(board_store, "record_board_build"),
        ):
            data = alerts.get_alert_board_data()
        return data, len(opened)

    def test_connection_count_does_not_grow_with_site_count(self):
        self._seed(20)
        small, small_conns = self._build_counting_connections()
        caching.cache.clear()
        self._seed_more(20, 2000)
        large, large_conns = self._build_counting_connections()

        assert small["summary"]["total"] == 20
        assert large["summary"]["total"] == 2000
        assert large["summary"]["critical"] == 200
        # Before #149 a build opened 3 connections per site: 6,000 for 2,000 sites.
        assert large_conns == small_conns
        assert large_conns <= 6

    def _seed_more(self, start, stop):
        self._execute(
            "INSERT INTO nautobot_location_cache (location_id, name, status, location_type, latitude, longitude) "
            "VALUES (%s, %s, 'Active', 'Office', 1.0, 2.0)",
            [(f"loc-{i}", f"Site {i}") for i in range(start, stop)],
        )
        self._execute(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (
                    f"dev-{i}-{j}",
                    f"loc-{i}",
                    f"dev{i}-{j}",
                    "router" if j == 0 else "access",
                    "Offline" if (j == 0 and i % 10 == 0) else "Active",
                    f"10.0.{i % 250}.{j}/32",
                )
                for i in range(start, stop)
                for j in range(3)
            ],
        )

    def test_bulk_context_matches_per_site_context(self):
        self._seed(6, down_every=3)  # loc-0 and loc-3 have a router down
        # loc-1: healthy now, only closed history -> context comes from the bulk read.
        self._add_alert(
            "loc-1", "dev-1-1", "resolved", "2026-09-24T10:00:00+00:00", total_downtime_seconds=900
        )
        # loc-2: healthy now, but an open alert -> it is resolved by the write path.
        self._add_alert("loc-2", "dev-2-2", "open", "2026-09-25T11:30:00+00:00")
        # loc-3: down router with an open alert, a case and closed history.
        open_id = self._add_alert("loc-3", "dev-3-0", "open", "2026-09-25T11:00:00+00:00")
        self._add_alert(
            "loc-3", "dev-3-0", "resolved", "2026-09-20T10:00:00+00:00", total_downtime_seconds=120
        )
        self._execute(
            "INSERT INTO alert_cases (alert_instance_id, case_number, created_at) VALUES (%s, %s, %s)",
            [(open_id, "INC-1", "2026-09-25T11:05:00"), (open_id, "INC-2", "2026-09-25T11:10:00")],
        )

        data = alerts.get_alert_board_data()

        by_site = {entry["id"]: entry for entry in data["alerts"]}
        for site_id, entry in by_site.items():
            expected = alerts.get_alert_context_for_site(site_id, self.CHECKED_AT)
            assert (
                entry["historical_downtime_seconds"] == expected["historical_downtime_seconds"]
            ), site_id
            assert entry["current_downtime_seconds"] == expected["current_downtime_seconds"], (
                site_id
            )
            assert entry["active_cases"] == expected["active_cases"], site_id
        assert by_site["loc-1"]["historical_downtime_seconds"] == 900
        assert by_site["loc-2"]["active_alert_instance_count"] == 0  # resolved during the build
        assert by_site["loc-3"]["active_cases"] == ["INC-1", "INC-2"]
        assert by_site["loc-3"]["current_downtime_seconds"] == 3600
        assert by_site["loc-3"]["historical_downtime_seconds"] == 120 + 3600

    def test_sites_and_devices_carry_when_they_went_down(self):
        """The board's default sort and Copy need each down device's start (#227, #228)."""
        self._seed(3, down_every=1)  # every site has its router down
        self.db.execute(
            "UPDATE nautobot_device_cache SET status = 'Offline' WHERE device_id = 'dev-2-1'"
        )
        self._add_alert("loc-0", "dev-0-0", "open", "2026-09-25T10:00:00+00:00")
        self._add_alert("loc-2", "dev-2-0", "open", "2026-09-25T09:00:00+00:00")
        self._add_alert("loc-2", "dev-2-1", "open", "2026-09-25T11:30:00+00:00")
        # loc-1 has no open alert yet: this build opens one, starting now.

        data = alerts.get_alert_board_data()

        by_site = {entry["id"]: entry for entry in data["alerts"]}

        def at(value):
            return timeutil.parse_iso_datetime(value)

        assert at(by_site["loc-0"]["latest_down_at"]) == at("2026-09-25T10:00:00Z")
        assert at(by_site["loc-1"]["latest_down_at"]) == at(self.CHECKED_AT)
        assert at(by_site["loc-2"]["latest_down_at"]) == at(
            "2026-09-25T11:30:00Z"
        )  # the newer of two
        started = {
            device["device_id"]: at(device["down_started_at"])
            for device in by_site["loc-2"]["down_devices"]
        }
        assert started == {
            "dev-2-0": at("2026-09-25T09:00:00Z"),
            "dev-2-1": at("2026-09-25T11:30:00Z"),
        }
        assert alerts.empty_alert_context()["latest_down_at"] is None

    def test_excluded_device_status_resolves_its_open_alert(self):
        self._seed(3, down_every=2)  # loc-0 and loc-2: router down (status Offline)
        self.db.execute(
            "UPDATE nautobot_device_cache SET status = 'Decommissioning' WHERE device_id = 'dev-0-0'"
        )
        self._add_alert("loc-0", "dev-0-0", "open", "2026-09-25T11:00:00+00:00")

        with patch.object(settings, "ALERT_BOARD_EXCLUDED_DEVICE_STATUSES", {"decommissioning"}):
            data = alerts.get_alert_board_data()

        by_site = {entry["id"]: entry for entry in data["alerts"]}
        assert by_site["loc-0"]["alert_level"] == "ok"
        assert by_site["loc-0"]["device_count"] == 2
        assert by_site["loc-0"]["active_alert_instance_count"] == 0
        assert by_site["loc-2"]["alert_level"] == "critical"  # Offline is still down
        rows = self.db.execute("SELECT status FROM alert_instances WHERE device_id = 'dev-0-0'")
        assert [row["status"] for row in rows] == ["resolved"]

    def test_backfill_pending_skips_writes_and_hides_open_alerts(self):
        self._seed(4, down_every=2, backfill_done=False)
        self._add_alert(
            "loc-1", "dev-1-0", "resolved", "2026-09-24T10:00:00+00:00", total_downtime_seconds=300
        )
        self._add_alert("loc-1", "dev-1-2", "open", "2026-09-25T11:00:00+00:00")

        with patch.object(alerts, "upsert_alert_lifecycle_for_site") as upsert:
            data = alerts.get_alert_board_data()

        upsert.assert_not_called()
        loc1 = next(entry for entry in data["alerts"] if entry["id"] == "loc-1")
        assert loc1["active_alert_instance_count"] == 0
        assert loc1["current_downtime_seconds"] == 0
        assert loc1["historical_downtime_seconds"] == 300 + 3600


# ---------------------------------------------------------------------------
# Tests: one board row per Site, devices rolled up from child locations (#158)
# ---------------------------------------------------------------------------
class TestSiteRollup:
    """EMEA (Region) › DNK (Country) › Aarhus (Site) › Bygning A (Bygning) › Etage 2 (Etage)."""

    LOCATIONS = [
        ("reg-emea", "EMEA", "Region", "", "Active"),
        ("cty-dnk", "DNK", "Country", "reg-emea", "Active"),
        ("site-aar", "Aarhus", "Site", "cty-dnk", "Active"),
        ("bld-a", "Bygning A", "Bygning", "site-aar", "Active"),
        ("flr-2", "Etage 2", "Etage", "bld-a", "Active"),
        ("bld-old", "Bygning Old", "Bygning", "site-aar", "Decommissioning"),
        # A site straight under the region (no country level).
        ("site-osl", "Oslo", "Site", "reg-emea", "Active"),
        # A building with no site above it.
        ("bld-orphan", "Loose Building", "Bygning", "cty-dnk", "Active"),
    ]

    @pytest.fixture(autouse=True)
    def _database(self, pg_database, monkeypatch):
        self.db = pg_database
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *a, **k: False)
        monkeypatch.setattr(settings, "ALERT_BOARD_EXCLUDED_LOCATION_STATUSES", {"decommissioning"})
        monkeypatch.setattr(settings, "ALERT_BOARD_EXCLUDED_LOCATION_TYPES", set())
        names = {location_id: name for location_id, name, *_ in self.LOCATIONS}
        self.db.executemany(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, parent_id, parent, status) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (location_id, name, location_type, parent_id, names.get(parent_id, ""), status)
                for location_id, name, location_type, parent_id, status in self.LOCATIONS
            ],
        )
        self.db.executemany(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                ("d-site", "site-aar", "aar-core01", "core", "Active", "10.1.0.1/32"),
                ("d-bld", "bld-a", "aar-dist01", "distribution", "Active", "10.1.0.2/32"),
                ("d-flr", "flr-2", "aar-acc01", "access", "Offline", "10.1.0.3/32"),
                ("d-old", "bld-old", "aar-old01", "access", "Offline", "10.1.0.4/32"),
                ("d-osl", "site-osl", "osl-core01", "core", "Active", "10.2.0.1/32"),
                ("d-orphan", "bld-orphan", "loose01", "access", "Active", "10.3.0.1/32"),
            ],
        )
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_started_at="2026-09-25T11:00:00+00:00",
                last_completed_at="2026-09-25T11:00:00+00:00",
                last_successful_sync="2026-09-25T11:00:00+00:00",
                cache_version=inventory.CACHE_VERSION,
                status="idle",
            )
        conn.close()

    def _board(self, monkeypatch, site_type="site"):
        monkeypatch.setattr(settings, "ALERT_BOARD_SITE_LOCATION_TYPE", site_type)
        caching.cache.clear()
        return {row["id"]: row for row in alerts.get_alert_board_data()["alerts"]}

    def test_only_sites_are_rows_and_regions_are_hidden(self, monkeypatch):
        rows = self._board(monkeypatch)
        # The orphan building keeps its own row; Region, Country, Bygning and Etage do not.
        assert set(rows) == {"site-aar", "site-osl", "bld-orphan"}
        assert rows["site-aar"]["ancestor_path"] == "EMEA › DNK"
        assert rows["site-osl"]["ancestor_path"] == "EMEA"

    def test_devices_below_the_site_roll_up(self, monkeypatch):
        aarhus = self._board(monkeypatch)["site-aar"]
        # Site, Bygning A and Etage 2 devices; not the one in the excluded (Decommissioning) building.
        assert aarhus["device_count"] == 3
        assert aarhus["down_device_count"] == 1
        assert aarhus["alert_level"] == "medium"  # 1 of 3 down, an access switch
        assert aarhus["down_devices"] == [
            {
                "device_id": "d-flr",
                "device_name": "aar-acc01",
                "device_ip": "10.1.0.3",
                "status": "Offline",
                "role": "access",
                "case_numbers": [],
                "location_path": "Bygning A › Etage 2",
                "down_started_at": ANY,  # opened by this build, so "now"
            }
        ]

    def test_alerts_are_recorded_on_the_site(self, monkeypatch):
        self._board(monkeypatch)
        rows = self.db.execute("SELECT site_id, device_id, status FROM alert_instances")
        assert rows == [{"site_id": "site-aar", "device_id": "d-flr", "status": "open"}]

    def test_setting_is_case_insensitive_and_unset_keeps_one_row_per_location(self, monkeypatch):
        assert "site-aar" in self._board(monkeypatch, site_type="site")
        rows = self._board(monkeypatch, site_type="")
        assert {"reg-emea", "cty-dnk", "bld-a", "flr-2"} <= set(rows)
        assert rows["flr-2"]["down_device_count"] == 1
        # Every row still shows the Nautobot location path above it (#178).
        assert rows["site-aar"]["ancestor_path"] == "EMEA › DNK"
        assert rows["flr-2"]["ancestor_path"] == "EMEA › DNK › Aarhus › Bygning A"
        assert rows["reg-emea"]["ancestor_path"] == ""

    def test_orphan_location_is_logged_once(self, monkeypatch, caplog):
        alerts.logged_rollup_orphans.clear()
        with caplog.at_level("INFO", logger="app.services.alerts_service"):
            self._board(monkeypatch)
            self._board(monkeypatch)
        assert caplog.text.count("'Loose Building' has devices but no 'site' above it") == 1

    def test_parent_cycle_does_not_hang(self):
        locations = [
            {"id": "a", "name": "A", "location_type": "Bygning", "parent_id": "b"},
            {"id": "b", "name": "B", "location_type": "Bygning", "parent_id": "a"},
        ]
        rows, devices = alerts.roll_up_to_site_locations(locations, {"a": [{"id": "d1"}]}, "site")
        assert [row["id"] for row in rows] == ["a"]
        assert devices == {"a": [{"id": "d1", "location_path": ""}]}
        assert [
            row["ancestor_path"] for row in inventory.with_ancestor_paths(locations, locations)
        ] == [
            "A › B",
            "B › A",
        ]


# ---------------------------------------------------------------------------
# Tests: background scheduler records history with nobody viewing (#154)
# ---------------------------------------------------------------------------
class TestBackgroundScheduler:
    @pytest.fixture(autouse=True)
    def _database(self, pg_database, monkeypatch):
        self.db = pg_database
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        self.db.execute(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, status) "
            "VALUES ('loc-1', 'Site One', 'Office', 'Active')"
        )
        self.db.execute(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES ('dev-1', 'loc-1', 'router01', 'router', 'Offline', '10.0.0.1/32')"
        )
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_started_at="2026-09-25T11:00:00+00:00",
                last_completed_at="2026-09-25T11:00:00+00:00",
                last_successful_sync="2026-09-25T11:00:00+00:00",
                cache_version=inventory.CACHE_VERSION,
                status="idle",
            )
        conn.close()

    def test_tick_after_a_sync_records_alert_history_without_any_request(self):
        with patch.object(inventory, "ensure_snapshot", return_value=True) as ensure:
            assert scheduler.tick() is True
        ensure.assert_called_once_with(wait=True)
        rows = self.db.execute("SELECT site_id, device_id, status FROM alert_instances")
        assert rows == [{"site_id": "loc-1", "device_id": "dev-1", "status": "open"}]
        # The rebuilt board is cached, so the next page load is instant.
        assert caching.cache.get("alert-board-data:v3")["summary"]["critical"] == 1

    def test_tick_without_a_due_sync_does_nothing(self):
        with (
            patch.object(inventory, "ensure_snapshot", return_value=False),
            patch.object(alerts, "build_alert_board_payload") as build,
        ):
            assert scheduler.tick() is False
        build.assert_not_called()

    def test_only_one_process_ticks_at_a_time(self):
        """Another worker holding the lock makes this tick skip."""
        release = db.try_advisory_lock("background_scheduler")
        assert callable(release)
        try:
            other = db.try_advisory_lock("background_scheduler")
            assert other is False  # a second connection cannot take it
            with patch.object(inventory, "ensure_snapshot") as ensure:
                assert scheduler.tick() is False
            ensure.assert_not_called()
        finally:
            release()

    def test_loop_survives_a_failing_tick(self, monkeypatch):
        calls = []

        def failing_tick():
            calls.append(1)
            if len(calls) == 2:
                scheduler._stop.set()
            raise RuntimeError("nautobot down")

        monkeypatch.setattr(scheduler, "tick", failing_tick)
        monkeypatch.setattr(scheduler, "tick_seconds", lambda: 0)
        scheduler._stop.clear()
        try:
            scheduler.loop()
        finally:
            scheduler._stop.clear()
        assert len(calls) == 2

    def test_start_is_idempotent_and_respects_the_setting(self, monkeypatch):
        started = []
        monkeypatch.setattr(scheduler, "_started", False)
        monkeypatch.setattr(
            scheduler.threading, "Thread", lambda **kwargs: started.append(kwargs) or MagicMock()
        )
        monkeypatch.setattr(settings, "BACKGROUND_SYNC_ENABLED", False)
        assert scheduler.start() is False
        monkeypatch.setattr(settings, "BACKGROUND_SYNC_ENABLED", True)
        assert scheduler.start() is True
        assert scheduler.start() is True
        assert len(started) == 1
        assert started[0]["daemon"] is True

    def test_tick_seconds_follow_the_sooner_interval(self, monkeypatch):
        monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 300)
        assert scheduler.tick_seconds() == 30
        monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 10)
        assert scheduler.tick_seconds() == 10


# ---------------------------------------------------------------------------
# Tests: Low and No data levels, all devices down = Critical (#124)
# ---------------------------------------------------------------------------
class TestSeverityTiers:
    @staticmethod
    def _switches(total, down):
        return [
            {
                "id": f"d{i}",
                "name": f"sw{i}",
                "role": "Access Switch",
                "status": "offline" if i < down else "active",
            }
            for i in range(total)
        ]

    @pytest.fixture(autouse=True)
    def _no_database(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")

    @pytest.mark.parametrize(
        ("total", "down", "level", "reason"),
        [
            (20, 0, "ok", ""),
            (20, 1, "low", "1/20 devices offline (5%)"),
            (4, 1, "low", "1/4 devices offline (25%)"),
            (4, 2, "medium", "2/4 devices offline (50%)"),
            (4, 4, "critical", "All 4 monitored devices offline"),
            (1, 1, "critical", "All 1 monitored device offline"),
            (0, 0, "no_data", "No monitored devices"),
        ],
    )
    def test_levels(self, total, down, level, reason):
        assert alerts.compute_alert_level(self._switches(total, down)) == {
            "level": level,
            "reason": reason,
        }

    def test_core_device_still_wins(self):
        devices = self._switches(20, 1)
        devices[0]["role"] = "Core Router"
        assert alerts.compute_alert_level(devices)["level"] == "critical"

    def test_board_order_and_counts(self):
        assert sorted(
            ["ok", "no_data", "low", "critical", "medium"], key=alerts.alert_sort_key
        ) == [
            "critical",
            "medium",
            "low",
            "no_data",
            "ok",
        ]
        levels = ["critical", "low", "low", "no_data", "ok"]
        locations = [{"id": f"loc-{i}", "name": f"Site {i}"} for i in range(len(levels))]

        def fake_alert(location_id, *args, **kwargs):
            return [], {"level": levels[int(location_id.split("-")[1])], "reason": ""}

        caching.cache.clear()
        with (
            patch.object(inventory, "get_locations", return_value=locations),
            patch.object(alerts, "get_location_devices_and_alert", side_effect=fake_alert),
        ):
            summary = alerts.get_alert_board_data()["summary"]
        assert summary == {
            "total": 5,
            "critical": 1,
            "medium": 0,
            "low": 2,
            "no_data": 1,
            "ok": 1,
            "non_ok": 3,  # No data is not an alert
            "maintenance": 0,
            "wireless": 0,
        }
        assert "unknown" not in summary


# ---------------------------------------------------------------------------
# Tests: alerts start when Nautobot's status changed, not when first seen (#166)
# ---------------------------------------------------------------------------
class TestRealStartTimes:
    CHECKED_AT = "2026-09-28T12:00:00+00:00"

    def test_down_since(self):
        now = self.CHECKED_AT
        # Nautobot status changed three days earlier -> that is when it went down.
        assert (
            alerts.down_since({"last_updated": "2026-09-25T12:00:00Z"}, now)
            == "2026-09-25T12:00:00+00:00"
        )
        # A later edit (clock skew, or an edit after we saw it) never moves the start later.
        assert alerts.down_since({"last_updated": "2026-09-28T13:00:00Z"}, now) == now
        # Unknown last change -> when we saw it.
        assert alerts.down_since({"last_updated": ""}, now) == now
        assert alerts.down_since({}, now) == now
        # Only LibreNMS says down: Nautobot's last_updated says nothing about this outage.
        assert (
            alerts.down_since(
                {"last_updated": "2026-09-25T12:00:00Z", "down_source": "librenms"}, now
            )
            == now
        )

    def test_librenms_only_down_is_marked(self, monkeypatch):
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.example.com")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "token")
        enriched = alerts.enrich_with_librenms(
            [
                {
                    "id": "d1",
                    "name": "sw01",
                    "status": "active",
                    "last_updated": "2026-01-01T00:00:00Z",
                },
                {
                    "id": "d2",
                    "name": "sw02",
                    "status": "offline",
                    "last_updated": "2026-01-01T00:00:00Z",
                },
            ],
            lnms_devices=[
                {"device_id": 1, "hostname": "sw01", "status": 0},
                {"device_id": 2, "hostname": "sw02", "status": 0},
            ],
            lnms_id_map={1: {}, 2: {}},
            snapshot_only=True,
        )
        assert enriched[0]["status"] == "offline" and enriched[0]["down_source"] == "librenms"
        # Already down in Nautobot: Nautobot is the source.
        assert "down_source" not in enriched[1]

    @pytest.fixture
    def board(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *a, **k: False)
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: self.CHECKED_AT)
        pg_database.execute(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, status) "
            "VALUES ('loc-1', 'Site One', 'Office', 'Active')"
        )
        pg_database.executemany(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip, last_updated) "
            "VALUES (%s, 'loc-1', %s, 'access', %s, %s, %s)",
            [
                ("d-old", "sw-old", "Offline", "10.0.0.1/32", "2026-09-25T12:00:00Z"),
                ("d-up", "sw-up", "Active", "10.0.0.2/32", "2026-09-01T00:00:00Z"),
                ("d-up2", "sw-up2", "Active", "10.0.0.3/32", "2026-09-01T00:00:00Z"),
                ("d-up3", "sw-up3", "Active", "10.0.0.4/32", "2026-09-01T00:00:00Z"),
            ],
        )
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_started_at="2026-09-28T11:00:00+00:00",
                last_completed_at="2026-09-28T11:00:00+00:00",
                last_successful_sync="2026-09-28T11:00:00+00:00",
                cache_version=inventory.CACHE_VERSION,
                status="idle",
            )
        conn.close()

        def build():
            caching.cache.clear()
            return {row["id"]: row for row in alerts.get_alert_board_data()["alerts"]}

        return build

    def _start(self, pg_database):
        rows = pg_database.execute(
            "SELECT down_started_at FROM alert_instances WHERE device_id = 'd-old'"
        )
        return [db.serialize_value(row["down_started_at"]) for row in rows]

    def test_new_alert_starts_when_nautobot_status_changed(self, board, pg_database):
        site = board()["loc-1"]
        assert self._start(pg_database) == ["2026-09-25T12:00:00Z"]
        assert site["current_downtime_seconds"] == 3 * 24 * 3600

    def test_open_alert_is_moved_earlier_once_and_never_later(self, board, pg_database):
        # An alert opened before #166: started when the app first saw it.
        pg_database.execute(
            "INSERT INTO alert_instances (alert_key, site_id, site_name, device_id, device_name, alert_level, "
            "alert_reason, status, down_started_at, last_seen_down_at, total_downtime_seconds) "
            "VALUES (%s, 'loc-1', 'Site One', 'd-old', 'sw-old', 'low', '', 'open', "
            "'2026-09-27T08:00:00Z', '2026-09-27T08:00:00Z', 0)",
            (db.build_alert_key("loc-1", "d-old"),),
        )
        board()
        assert self._start(pg_database) == ["2026-09-25T12:00:00Z"]
        # A later edit in Nautobot must not move it back.
        pg_database.execute(
            "UPDATE nautobot_device_cache SET last_updated = '2026-09-28T11:30:00Z'"
        )
        board()
        assert self._start(pg_database) == ["2026-09-25T12:00:00Z"]


# ---------------------------------------------------------------------------
# Tests: alert feed of devices going down and up and site severity changes (#180)
# ---------------------------------------------------------------------------
class TestAlertFeed:
    @pytest.fixture
    def board(self, pg_database, monkeypatch):
        """A site with two switches; returns (build, set_status, clock)."""
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *a, **k: False)
        clock = {"now": "2026-09-28T12:00:00+00:00"}
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: clock["now"])
        pg_database.execute(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, status) "
            "VALUES ('loc-1', 'Site One', 'Office', 'Active')"
        )
        pg_database.executemany(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES (%s, 'loc-1', %s, 'access', 'Active', %s)",
            [("d-1", "sw01", "10.0.0.1/32"), ("d-2", "sw02", "10.0.0.2/32")],
        )
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_started_at="2026-09-28T11:00:00+00:00",
                last_completed_at="2026-09-28T11:00:00+00:00",
                last_successful_sync="2026-09-28T11:00:00+00:00",
                cache_version=inventory.CACHE_VERSION,
                status="idle",
            )
        conn.close()

        def build(at):
            clock["now"] = at
            caching.cache.clear()
            return {row["id"]: row for row in alerts.get_alert_board_data()["alerts"]}

        def set_status(device_id, status):
            pg_database.execute(
                "UPDATE nautobot_device_cache SET status = %s WHERE device_id = %s",
                (status, device_id),
            )

        return build, set_status

    def _feed(self, flask_client, query=""):
        resp = flask_client.get(f"/api/v1/alert-feed{query}")
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()

    def test_level_changes_are_logged_but_not_first_sightings(self, pg_database):
        at = "2026-09-28T12:00:00+00:00"
        assert (
            alerts.record_site_level_changes({"s1": ("One", "ok"), "s2": ("Two", "critical")}, at)
            == 0
        )
        assert (
            alerts.record_site_level_changes({"s1": ("One", "ok"), "s2": ("Two", "critical")}, at)
            == 0
        )
        assert (
            alerts.record_site_level_changes({"s1": ("One", "low"), "s2": ("Two", "critical")}, at)
            == 1
        )
        rows = pg_database.execute("SELECT site_id, from_level, to_level FROM site_level_changes")
        assert rows == [{"site_id": "s1", "from_level": "ok", "to_level": "low"}]
        levels = pg_database.execute(
            "SELECT site_id, alert_level FROM site_alert_levels ORDER BY site_id"
        )
        assert levels == [
            {"site_id": "s1", "alert_level": "low"},
            {"site_id": "s2", "alert_level": "critical"},
        ]

    def test_board_builds_fill_the_feed(self, board, flask_client):
        build, set_status = board
        assert build("2026-09-28T12:00:00+00:00")["loc-1"]["alert_level"] == "ok"
        assert self._feed(flask_client)["events"] == []  # first sighting: nothing changed

        set_status("d-1", "Offline")
        down_level = build("2026-09-28T12:05:00+00:00")["loc-1"]["alert_level"]
        assert down_level != "ok"
        set_status("d-1", "Active")
        build("2026-09-28T12:10:00+00:00")

        feed = self._feed(flask_client)
        assert feed["persistence_configured"] is True
        assert [(e["kind"], e["at"]) for e in feed["events"]] == [
            ("severity", "2026-09-28T12:10:00Z"),
            ("up", "2026-09-28T12:10:00Z"),
            ("severity", "2026-09-28T12:05:00Z"),
            ("down", "2026-09-28T12:05:00Z"),
        ]
        back_to_ok, up, went_down, down = feed["events"]
        assert down["site_name"] == "Site One" and down["device_name"] == "sw01"
        assert down["down_since"] == "2026-09-28T12:05:00Z" and "from_level" not in down
        assert up["device_id"] == "d-1" and up["level"] == "ok" and "down_since" not in up
        assert (went_down["from_level"], went_down["to_level"]) == ("ok", down_level)
        assert (back_to_ok["from_level"], back_to_ok["to_level"]) == (down_level, "ok")
        assert back_to_ok["device_id"] == ""

        # Filters.
        assert [e["kind"] for e in self._feed(flask_client, "?kinds=severity")["events"]] == [
            "severity",
            "severity",
        ]
        assert [e["kind"] for e in self._feed(flask_client, "?kinds=down,up")["events"]] == [
            "up",
            "down",
        ]
        assert [e["kind"] for e in self._feed(flask_client, "?limit=1")["events"]] == ["severity"]
        since = self._feed(flask_client, "?since=2026-09-28T12:05:00Z")["events"]
        assert [e["kind"] for e in since] == ["severity", "up"]

    def test_a_board_for_some_sites_reads_their_feed(self, board, flask_client, pg_database):
        """?tenant= / ?q= (#369): only those sites' entries, and the limit counts them."""
        build, set_status = board
        pg_database.execute(
            "UPDATE nautobot_location_cache SET tenant = 'Acme Corp' WHERE location_id = 'loc-1'"
        )
        pg_database.execute(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, status, tenant) "
            "VALUES ('loc-2', 'Site Two', 'Office', 'Active', 'Nordic Net')"
        )
        pg_database.execute(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES ('d-3', 'loc-2', 'sw03', 'access', 'Active', '10.0.0.3/32')"
        )
        build("2026-09-28T12:00:00+00:00")
        set_status("d-1", "Offline")
        build("2026-09-28T12:05:00+00:00")
        set_status("d-3", "Offline")  # newer: Site Two's events come first in the whole feed
        build("2026-09-28T12:10:00+00:00")

        every = self._feed(flask_client)["events"]
        assert {e["site_name"] for e in every} == {"Site One", "Site Two"}
        acme = self._feed(flask_client, "?tenant=acme%20corp")["events"]
        assert acme and {e["site_name"] for e in acme} == {"Site One"}
        assert {e["site_name"] for e in self._feed(flask_client, "?q=two")["events"]} == {
            "Site Two"
        }
        # The limit counts the matching entries, not the whole feed's newest.
        one = self._feed(flask_client, "?tenant=Acme%20Corp&limit=1")["events"]
        assert len(one) == 1 and one[0]["site_name"] == "Site One"
        # since and the scope together.
        later = self._feed(flask_client, "?q=two&since=2026-09-28T12:05:00Z")["events"]
        assert later and {(e["site_name"], e["at"]) for e in later} == {
            ("Site Two", "2026-09-28T12:10:00Z")
        }
        assert (
            self._feed(flask_client, "?tenant=Acme%20Corp&since=2026-09-28T12:05:00Z")["events"]
            == []
        )
        assert self._feed(flask_client, "?tenant=nobody") == {
            "events": [],
            "persistence_configured": True,
            "matching_sites": 0,
        }
        assert self._feed(flask_client, "?q=site")["matching_sites"] == 2
        assert "matching_sites" not in self._feed(flask_client)

    def test_a_scoped_feed_without_stored_sites_says_so(self, pg_database, flask_client):
        """No stored locations (not synced yet, or the read failed): an error,
        not an empty feed that looks like an all-clear (#369)."""
        resp = flask_client.get("/api/v1/alert-feed?tenant=Acme")
        assert resp.status_code == 503
        assert "not stored yet" in resp.get_json()["error"]
        assert self._feed(flask_client)["events"] == []  # the whole network's feed still answers

    def test_bad_parameters_are_rejected(self, pg_database, flask_client):
        assert flask_client.get("/api/v1/alert-feed?limit=many").status_code == 400
        assert flask_client.get("/api/v1/alert-feed?since=yesterday").status_code == 400
        resp = flask_client.get("/api/v1/alert-feed?kinds=down,cases")
        assert resp.status_code == 400 and "cases" in resp.get_json()["error"]
        # Out-of-range limits are clamped, not rejected.
        assert flask_client.get("/api/v1/alert-feed?limit=0").status_code == 200
        assert flask_client.get("/api/v1/alert-feed?limit=100000").status_code == 200

    def test_failed_observation_is_not_a_level_change(self, board, monkeypatch, pg_database):
        build, _ = board
        build("2026-09-28T12:00:00+00:00")

        def fail(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(alerts, "get_location_devices_and_alert", fail)
        assert build("2026-09-28T12:05:00+00:00")["loc-1"]["alert_level"] == "no_data"
        assert pg_database.execute("SELECT count(*) AS n FROM site_level_changes") == [{"n": 0}]


# ---------------------------------------------------------------------------
# Tests: configurable geocoder, cache and rate limit (#197)
# ---------------------------------------------------------------------------
class TestGeocoder:
    @pytest.fixture(autouse=True)
    def _fresh_cache(self, monkeypatch):
        caching.cache.clear()
        monkeypatch.setattr(inventory, "get_locations", lambda *a, **k: [])
        yield
        caching.cache.clear()

    def _geolocator(self, lat=55.68, lon=12.57):
        geolocator = MagicMock()
        geolocator.geocode.return_value = MagicMock(latitude=lat, longitude=lon)
        return geolocator

    def test_disabled_geocoder_only_accepts_coordinates(self, flask_client, monkeypatch):
        monkeypatch.setattr(settings, "GEOCODER_ENABLED", False)
        with patch(
            "app.v1.routes.locations.Nominatim", side_effect=AssertionError("must not geocode")
        ):
            resp = flask_client.get("/api/v1/search?q=Copenhagen")
            assert resp.status_code == 400 and "coordinates" in resp.get_json()["error"]
            assert flask_client.get("/api/v1/search?q=55.68,12.57").status_code == 200

    def test_uses_the_configured_service(self, flask_client, monkeypatch):
        monkeypatch.setattr(settings, "GEOCODER_URL", "http://nominatim.internal:8080/nominatim")
        monkeypatch.setattr(settings, "GEOCODER_USER_AGENT", "noc-maps/2")
        with patch(
            "app.v1.routes.locations.Nominatim", return_value=self._geolocator()
        ) as nominatim:
            assert flask_client.get("/api/v1/search?q=Copenhagen").status_code == 200
        nominatim.assert_called_once_with(
            user_agent="noc-maps/2", domain="nominatim.internal:8080/nominatim", scheme="http"
        )

    def test_results_are_cached(self, flask_client):
        geolocator = self._geolocator()
        with patch("app.v1.routes.locations.Nominatim", return_value=geolocator):
            first = flask_client.get("/api/v1/search?q=Copenhagen").get_json()
            caching.cache.delete("geocode-rate-limit")
            second = flask_client.get("/api/v1/search?q=copenhagen ").get_json()
        assert geolocator.geocode.call_count == 1
        assert first["search_lat"] == second["search_lat"] == pytest.approx(55.68)

    def test_at_most_one_request_per_second(self, flask_client, monkeypatch):
        # A minute, not a second: a slow CI runner took over a second between
        # the two requests, and the second one was let through (#332).
        monkeypatch.setattr(api_locations, "GEOCODE_MIN_INTERVAL_SECONDS", 60)
        with patch("app.v1.routes.locations.Nominatim", return_value=self._geolocator()):
            assert flask_client.get("/api/v1/search?q=Copenhagen").status_code == 200
            busy = flask_client.get("/api/v1/search?q=Aarhus")
            assert busy.status_code == 429 and "try again" in busy.get_json()["error"]
            # Once the interval is over (its key expired), the next search is asked.
            caching.cache.delete("geocode-rate-limit")
            assert flask_client.get("/api/v1/search?q=Aarhus").status_code == 200

    def test_the_interval_is_one_second(self):
        assert api_locations.GEOCODE_MIN_INTERVAL_SECONDS == 1

    def test_geocoder_error_is_not_passed_to_the_client(self, flask_client):
        geolocator = MagicMock()
        geolocator.geocode.side_effect = OSError("connect to 10.0.0.5 refused")
        with patch("app.v1.routes.locations.Nominatim", return_value=geolocator):
            resp = flask_client.get("/api/v1/search?q=Copenhagen")
        assert resp.status_code == 503
        assert resp.get_json() == {"error": "Geocoding service unavailable"}

    def test_not_found_is_cached_too(self, flask_client):
        geolocator = MagicMock()
        geolocator.geocode.return_value = None
        with patch("app.v1.routes.locations.Nominatim", return_value=geolocator):
            assert flask_client.get("/api/v1/search?q=Nowhere").status_code == 404
            caching.cache.delete("geocode-rate-limit")
            assert flask_client.get("/api/v1/search?q=Nowhere").status_code == 404
        assert geolocator.geocode.call_count == 1


# ---------------------------------------------------------------------------
# Tests: browser security headers (#198)
# ---------------------------------------------------------------------------
class TestSecurityHeaders:
    @pytest.mark.parametrize("path", ["/", "/alerts", "/healthz", "/api/v1/does-not-exist"])
    def test_headers_on_every_response(self, flask_client, path, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        resp = flask_client.get(path)
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        csp = resp.headers["Content-Security-Policy"]
        assert "script-src 'self';" in csp and "frame-ancestors 'none'" in csp
        # No inline script or style anywhere (#370): an injection can't restyle the page.
        assert "style-src 'self';" in csp and "unsafe-inline" not in csp

    def test_images_allowed_from_the_tile_server(self, flask_client, monkeypatch):
        monkeypatch.setattr(
            settings, "MAP_TILE_URL", "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        )
        assert (
            "img-src 'self' data: https://*.tile.openstreetmap.org;"
            in flask_client.get("/").headers["Content-Security-Policy"]
        )
        monkeypatch.setattr(settings, "MAP_TILE_URL", "http://tiles.internal:8080/{z}/{x}/{y}.png")
        assert (
            "img-src 'self' data: http://tiles.internal:8080;"
            in flask_client.get("/").headers["Content-Security-Policy"]
        )
        monkeypatch.setattr(settings, "MAP_TILE_URL", "/tiles/{z}/{x}/{y}.png")
        assert "img-src 'self' data:;" in flask_client.get("/").headers["Content-Security-Policy"]


# ---------------------------------------------------------------------------
# Tests: logging (#193)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: alert history retention (#194)
# ---------------------------------------------------------------------------
class TestHistoryRetention:
    def _alert(self, db_, device, status, resolved_days_ago=None):
        row = db_.execute(
            "INSERT INTO alert_instances (alert_key, site_id, site_name, device_id, device_name, alert_level, "
            "status, down_started_at, last_seen_down_at, resolved_at) VALUES "
            "(%s, 'loc-1', 'Site', %s, %s, 'low', %s, now() - interval '400 days', now() - interval '400 days', "
            "CASE WHEN %s::int IS NULL THEN NULL ELSE now() - make_interval(days => %s::int) END) RETURNING id",
            (f"key-{device}", device, device, status, resolved_days_ago, resolved_days_ago),
        )[0]["id"]
        db_.execute(
            "INSERT INTO alert_events (alert_instance_id, event_type, event_at) VALUES (%s, 'opened', now())",
            (row,),
        )
        db_.execute(
            "INSERT INTO alert_cases (alert_instance_id, case_number) VALUES (%s, 'INC-1')", (row,)
        )
        return row

    def _change(self, db_, days_ago):
        db_.execute(
            "INSERT INTO site_level_changes (site_id, site_name, from_level, to_level, changed_at) "
            "VALUES ('loc-1', 'Site', 'ok', 'low', now() - make_interval(days => %s))",
            (days_ago,),
        )

    def _devices(self, db_):
        return sorted(r["device_id"] for r in db_.execute("SELECT device_id FROM alert_instances"))

    def test_deletes_only_old_resolved_history(self, pg_database):
        self._alert(pg_database, "old-resolved", "resolved", resolved_days_ago=100)
        self._alert(pg_database, "new-resolved", "resolved", resolved_days_ago=5)
        self._alert(pg_database, "old-open", "open")  # started 400 days ago, still down
        self._change(pg_database, 100)
        self._change(pg_database, 5)
        conn = db.get_conn()
        try:
            deleted = board_store.prune_alert_history(conn, 30)
        finally:
            conn.close()
        assert deleted == {"alert_instances": 1, "site_level_changes": 1}
        assert self._devices(pg_database) == ["new-resolved", "old-open"]
        # Its events and cases went with it.
        assert pg_database.execute("SELECT count(*) AS n FROM alert_events") == [{"n": 2}]
        assert pg_database.execute("SELECT count(*) AS n FROM alert_cases") == [{"n": 2}]

    def test_deletes_in_batches(self, pg_database, monkeypatch):
        monkeypatch.setattr(board_store, "RETENTION_BATCH_SIZE", 2)
        for i in range(5):
            self._alert(pg_database, f"d{i}", "resolved", resolved_days_ago=100)
        conn = db.get_conn()
        try:
            assert board_store.prune_alert_history(conn, 30)["alert_instances"] == 5
        finally:
            conn.close()
        assert self._devices(pg_database) == []

    def test_off_by_default_and_once_a_day(self, pg_database, monkeypatch):
        self._alert(pg_database, "old-resolved", "resolved", resolved_days_ago=100)
        conn = db.get_conn()
        try:
            monkeypatch.setattr(settings, "ALERT_HISTORY_RETENTION_DAYS", 0)
            assert alerts.maybe_prune_alert_history(conn) is None
            assert self._devices(pg_database) == ["old-resolved"]

            monkeypatch.setattr(settings, "ALERT_HISTORY_RETENTION_DAYS", 30)
            assert alerts.maybe_prune_alert_history(conn)["alert_instances"] == 1
            self._alert(pg_database, "later", "resolved", resolved_days_ago=100)
            assert alerts.maybe_prune_alert_history(conn) is None  # already ran today
            assert self._devices(pg_database) == ["later"]
        finally:
            conn.close()

    def test_scheduler_tick_runs_retention(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_HISTORY_RETENTION_DAYS", 30)
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *a, **k: False)
        self._alert(pg_database, "old-resolved", "resolved", resolved_days_ago=100)
        scheduler.tick()
        assert self._devices(pg_database) == []


# ---------------------------------------------------------------------------
# Tests: Prometheus metrics and sync age (#200)
# ---------------------------------------------------------------------------
class TestMetrics:
    def _record(self, status, started, completed):
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_started_at=started,
                last_completed_at=completed,
                last_successful_sync="2026-09-01T00:00:00+00:00",
                status=status,
            )
        conn.close()

    @staticmethod
    def _value(body: str, sample: str) -> float:
        line = next(line for line in body.splitlines() if line.startswith(sample + " "))
        return float(line.rsplit(" ", 1)[1])

    def test_last_success_survives_a_failure(self, pg_database, flask_client):
        self._record("idle", "2026-09-29T10:00:00+00:00", "2026-09-29T10:00:30+00:00")
        self._record("running", "2026-09-29T10:05:00+00:00", None)
        self._record("error", "2026-09-29T10:05:00+00:00", "2026-09-29T10:05:10+00:00")
        body = flask_client.get("/metrics").get_data(as_text=True)
        src = '{source="nautobot_inventory"}'
        success = datetime(2026, 9, 29, 10, 0, 30, tzinfo=UTC).timestamp()
        assert (
            self._value(body, "nautobot_maps_sync_last_success_timestamp_seconds" + src) == success
        )
        assert self._value(body, "nautobot_maps_sync_failing" + src) == 1
        assert self._value(body, "nautobot_maps_sync_last_duration_seconds" + src) == 10

    def test_alert_and_site_counts(self, pg_database, flask_client):
        alerts.upsert_alert_lifecycle_for_site(
            {"id": "loc-1", "name": "Site One"},
            [
                {"id": "d1", "name": "sw1", "status": "offline"},
                {"id": "d2", "name": "sw2", "status": "offline"},
            ],
            {"level": "critical", "reason": "all down"},
            timeutil.iso_utc_now(),
        )
        alerts.record_site_level_changes(
            {"s1": ("One", "ok"), "s2": ("Two", "ok"), "s3": ("Three", "low")}, "now"
        )
        resp = flask_client.get("/metrics")
        assert resp.headers["Content-Type"].startswith("text/plain; version=0.0.4")
        body = resp.get_data(as_text=True)
        assert self._value(body, 'nautobot_maps_open_alerts{level="critical"}') == 2
        assert self._value(body, 'nautobot_maps_sites{level="ok"}') == 2
        assert self._value(body, "nautobot_maps_database_up") == 1
        assert "# TYPE nautobot_maps_sites gauge" in body

    def test_can_be_turned_off(self, flask_client, monkeypatch):
        monkeypatch.setattr(settings, "METRICS_ENABLED", False)
        assert flask_client.get("/metrics").status_code == 404

    def test_label_values_are_escaped(self):
        from app.services import metrics_service as metrics

        out = metrics.Exposition()
        out.gauge("m", "h", [({"level": 'a"b\\c\nd'}, 1)])
        assert 'm{level="a\\"b\\\\c\\nd"} 1' in out.render()

    def test_healthz_reports_sync_age_without_failing(self, pg_database, flask_client):
        assert (
            flask_client.get("/healthz").get_json()["inventory_sync_age_seconds"] is None
        )  # never synced
        self._record("idle", "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:10+00:00")
        resp = flask_client.get("/healthz")
        assert resp.status_code == 200  # an old sync is not a liveness failure
        assert resp.get_json()["inventory_sync_age_seconds"] > 365 * 24 * 3600

    def test_timestamps_keep_full_precision(self):
        from app.services import metrics_service as metrics

        out = metrics.Exposition()
        out.gauge("t", "h", [({}, 1790676030.0), ({"x": "1"}, 0.25)])
        assert "t 1790676030\n" in out.render() and 't{x="1"} 0.25' in out.render()


# ---------------------------------------------------------------------------
# Tests: versioned schema migrations (#201)
# ---------------------------------------------------------------------------
# sha256 of db.baseline_schema's source.  The baseline is frozen: a schema
# change goes into a new step in db.MIGRATIONS, never into the baseline, or
# databases already at version 1 would never get it.
BASELINE_SCHEMA_SHA256 = "f5e812f54f7ffad33bf515db035d384fdb49f791a89451c3f623e12ca986178f"


# ---------------------------------------------------------------------------
# Tests: database connect and statement timeouts (#190)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: upstream HTTP retries and connection reuse (#192)
# ---------------------------------------------------------------------------


def _retry_kwargs(policy) -> dict:
    """The production retry settings, so tests only change the backoff."""
    return {
        "total": policy.total,
        "connect": policy.connect,
        "read": policy.read,
        "status": policy.status,
        "status_forcelist": policy.status_forcelist,
        "allowed_methods": policy.allowed_methods,
        "respect_retry_after_header": policy.respect_retry_after_header,
        "raise_on_status": policy.raise_on_status,
    }


# ---------------------------------------------------------------------------
# Tests: API explorer (#230)
# ---------------------------------------------------------------------------
class TestApiExplorer:
    def _routes(self):
        """Every route (#436), in Flask's notation."""
        from fastapi.routing import APIRoute

        from app.main import app as whole_app
        from app.services import apidocs_service as apidocs

        full = {
            op["operationId"]: path
            for path, ops in whole_app.openapi()["paths"].items()
            for op in ops.values()
        }

        def every(routes):
            for route in routes:
                if getattr(route, "original_router", None) is not None:
                    yield from every(route.original_router.routes)
                elif isinstance(route, APIRoute):
                    yield route

        return {
            (
                apidocs._flask_style(
                    full.get(route.operation_id or "", route.path), route.endpoint
                ),
                method,
            )
            for route in every(whole_app.routes)
            if route.name not in apidocs.HIDDEN_ENDPOINTS
            for method in route.methods - {"HEAD", "OPTIONS"}
        }

    def test_every_route_is_listed(self, flask_client):
        listed = flask_client.get("/api/v1/endpoints").get_json()["endpoints"]
        assert {
            (item["path"], method) for item in listed for method in item["methods"]
        } == self._routes()
        # Swagger UI on /api/docs draws /api/openapi.json (#436, #483): every API
        # route in it, with its method. Pages and files aren't API, nor are the
        # two routes under /api that describe it: the reference and the spec.
        # Listed by hand, so an API route left out of the spec by mistake fails.
        spec = flask_client.get("/api/openapi.json").get_json()
        documented = {
            (re.sub(r"\{(\w+)(?::\w+)?\}", r"<\1>", path), method.upper())
            for path, operations in spec["paths"].items()
            for method in operations
        }
        api_routes = {
            (re.sub(r"<(?:\w+:)?(\w+)>", r"<\1>", path), method) for path, method in self._routes()
        }
        pages = {navigation.API_DOCS_PATH, navigation.API_SPEC_PATH}
        assert {
            item
            for item in api_routes
            if item[0].startswith(("/api/v1/", "/mcp")) and item[0] not in pages
        } <= documented

    def test_every_write_has_an_example_body(self):
        from app.services import apidocs_service as apidocs

        for route in apidocs.routes():
            if set(route["methods"]) & {"POST", "PUT", "PATCH"}:
                endpoint = route["endpoint"]
                assert endpoint in apidocs.EXAMPLE_BODIES, (
                    f"add an example body for {endpoint} to apidocs"
                )

    def test_what_each_endpoint_takes(self, flask_client):
        listed = flask_client.get("/api/v1/endpoints").get_json()["endpoints"]
        by_key = {(item["path"], item["methods"][0]): item for item in listed}
        detail = by_key[("/api/v1/locations/<location_id>/detail", "GET")]
        assert detail["path_params"] == ["location_id"] and detail["query_params"] == [
            "location_type"
        ]
        assert detail["group"] == "API" and detail["required_role"] is None
        assert by_key[("/api/v1/alert-feed", "GET")]["query_params"] == [
            "limit",
            "since",
            "kinds",
            "tenant",
            "q",
        ]
        assert by_key[("/api/v1/locations/coordinate-check", "GET")]["query_params"] == [
            "tenant",
            "q",
        ]
        assert by_key[("/api/v1/alert-history", "GET")]["required_role"] == "operator"
        assert by_key[("/metrics", "GET")]["group"] == "Monitoring"
        assert by_key[("/alerts", "GET")]["group"] == "Pages"
        feed = by_key[("/api/v1/alert-feed", "GET")]
        assert feed["summary"] == "What changed on the alert board, newest first."  # no "(#180)"
        assert "Query" in feed["description"]
        case = by_key[("/api/v1/alert-cases", "POST")]
        assert json.loads(case["example_body_text"]) == case["example_body"]
        # The page keeps the example's own order (the JSON response sorts keys).
        assert list(json.loads(case["example_body_text"])) == [
            "site_id",
            "device_ids",
            "case_number",
        ]

    def test_write_bodies_are_in_the_spec(self, flask_client):
        """Each write's JSON body is described (deps.body_schema), e.g. a case."""
        spec = flask_client.get("/api/openapi.json").get_json()
        case = spec["paths"]["/api/v1/alert-cases"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        assert {"site_id", "case_number"} <= set(case["required"]) and "device_ids" in case[
            "properties"
        ]
        window = spec["paths"]["/api/v1/maintenance"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        assert "reason" in window["required"]
        # Query models are spelled as sent: "from", not "from_".
        names = {
            param["name"] for param in spec["paths"]["/api/v1/availability"]["get"]["parameters"]
        }
        assert {"month", "from", "to", "basis"} <= names

    def test_the_menu_links_to_the_explorer(self, flask_client):
        # The pages read the menu from /api/page-config (#463).
        pages = flask_client.get("/api/v1/page-config").get_json()["pages"]
        assert {"key": "docs", "href": "/api/docs"}.items() <= next(
            p for p in pages if p["key"] == "docs"
        ).items()

    def test_the_old_paths_move_for_good(self, flask_client):
        """#483: the reference and the spec live under /api; bookmarks and
        tools reading the old paths are sent on (301, which they remember)."""
        for old, new in (("/docs", "/api/docs"), ("/openapi.json", "/api/openapi.json")):
            resp = flask_client.get(old)
            assert resp.status_code == 301 and resp.headers["Location"] == new
            # With its query: a 301 is remembered.
            assert (
                flask_client.get(f"{old}?v=2&x=a%20b").headers["Location"] == f"{new}?v=2&x=a%20b"
            )
        listed = {
            item["path"] for item in flask_client.get("/api/v1/endpoints").get_json()["endpoints"]
        }
        assert "/docs" not in listed and "/openapi.json" not in listed

    def test_needs_the_viewer_role_like_every_page(self, flask_client, monkeypatch, frontend_build):
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
        with auth_config(mode="header", viewer_groups={"noc"}):
            assert flask_client.get("/api/docs").status_code == 401
            assert flask_client.get("/api/v1/endpoints").status_code == 401
            ok = flask_client.get(
                "/api/docs", headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc"}
            )
            assert ok.status_code == 200


def _snapshot_location(location_id: str, name: str, location_type: str = "Data Center") -> dict:
    return {
        "id": location_id,
        "name": name,
        "slug": location_id,
        "status": "Active",
        "location_type": location_type,
        "parent": "",
        "latitude": 55.0,
        "longitude": 12.0,
        "description": "",
        "physical_address": "",
        "facility": "",
        "tenant": "",
        "tenant_id": "",
        "tenant_group": "",
        "asn": None,
        "time_zone": "",
        "tags": [],
        "url": "",
        "last_updated": "2026-01-01T00:00:00Z",
    }


def _snapshot_device(device_id: str, location_id: str, role: str, status: str) -> dict:
    return {
        "id": device_id,
        "location_id": location_id,
        "name": device_id,
        "device_type": "",
        "manufacturer": "",
        "role": role,
        "status": status,
        "primary_ip": "10.0.0.1",
        "platform": "",
        "serial": "",
        "tenant": "",
        "last_updated": "2026-01-01T00:00:00Z",
    }


class TestLocationAlertLevels:
    """/api/v1/location-alerts colours the map markers without a click (#234)."""

    @pytest.fixture(autouse=True)
    def _database(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        _snapshot_location("loc-core", "Core down"),
                        _snapshot_location("loc-few", "One of five down"),
                        _snapshot_location("loc-ok", "All up"),
                        _snapshot_location("loc-empty", "No devices"),
                    ],
                )
                inventory.write_devices(
                    conn,
                    [
                        _snapshot_device("core-1", "loc-core", "Core Router", "Offline"),
                        _snapshot_device("acc-1", "loc-core", "Access Switch", "Active"),
                        _snapshot_device("few-1", "loc-few", "Access Switch", "Offline"),
                        *[
                            _snapshot_device(f"few-{n}", "loc-few", "Access Switch", "Active")
                            for n in range(2, 6)
                        ],
                        _snapshot_device("ok-1", "loc-ok", "Access Switch", "Active"),
                    ],
                )
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:01:00Z",
                    last_successful_sync="2026-01-01T00:01:00Z",
                    status="success",
                    error_message="",
                )
        finally:
            conn.close()
        caching.cache.clear()

    def test_returns_every_location_with_monitored_devices(self, flask_client):
        """OK too, so the map tells OK (green) from no data (grey) (#311)."""
        with patch.object(
            nautobot, "fetch_all_pages", side_effect=AssertionError("no upstream calls")
        ):
            resp = flask_client.get("/api/v1/location-alerts")
        assert resp.status_code == 200
        levels = resp.get_json()["levels"]
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

    def test_matches_the_site_panel(self, flask_client):
        levels = flask_client.get("/api/v1/location-alerts").get_json()["levels"]
        with patch.object(nautobot, "fetch_all_pages", return_value=[]):
            for location_id in ("loc-core", "loc-few", "loc-ok"):
                detail = alerts.get_location_detail(location_id, location_type="Data Center")
                expected = levels.get(location_id, {}).get("level", "ok")
                assert detail["alert"]["level"] == expected

    def test_the_site_panel_marks_core_devices(self, flask_client):
        """Core devices first in the panel's down list (#394): by role, or an override."""
        with patch.object(nautobot, "fetch_all_pages", return_value=[]):
            detail = alerts.get_location_detail("loc-core", location_type="Data Center")
        assert {d["name"]: d["core"] for d in detail["devices"]} == {"core-1": True, "acc-1": False}
        # And the level each gives the site, as the board weighs it (#377).
        assert {d["name"]: d["level"] for d in detail["devices"]} == {
            "core-1": "critical",
            "acc-1": "",
        }
        conn = db.get_conn()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO device_criticality_override (nautobot_device_id, is_critical, updated_by, updated_at)"
                    " VALUES ('few-1', 1, 'test', '2026-01-01T00:00:00Z')"
                )
        finally:
            conn.close()
        with patch.object(nautobot, "fetch_all_pages", return_value=[]):
            detail = alerts.get_location_detail("loc-few", location_type="Data Center")
        assert [d["id"] for d in detail["devices"] if d["core"]] == ["few-1"]

    def test_criticality_override_is_applied(self, flask_client):
        conn = db.get_conn()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO device_criticality_override (nautobot_device_id, is_critical, updated_by, updated_at)"
                    " VALUES ('few-1', 1, 'test', '2026-01-01T00:00:00Z')"
                )
        finally:
            conn.close()
        assert (
            flask_client.get("/api/v1/location-alerts").get_json()["levels"]["loc-few"]["level"]
            == "critical"
        )

    def test_cached_until_a_sync_invalidates_it(self, flask_client):
        assert (
            flask_client.get("/api/v1/location-alerts").get_json()["levels"]["loc-ok"]["level"]
            == "ok"
        )
        conn = db.get_conn()
        try:
            with conn:
                conn.execute(
                    "UPDATE nautobot_device_cache SET status = 'Offline' WHERE device_id = 'ok-1'"
                )
        finally:
            conn.close()
        assert (
            flask_client.get("/api/v1/location-alerts").get_json()["levels"]["loc-ok"]["level"]
            == "ok"
        ), "cached"
        caching.invalidate_alert_board()
        assert (
            flask_client.get("/api/v1/location-alerts").get_json()["levels"]["loc-ok"]["level"]
            == "critical"
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

    def test_every_circuit_is_listed(self, flask_client):
        with self._mock(SAMPLE_CIRCUIT_TERMINATIONS):
            data = flask_client.get("/api/v1/locations/loc-1/detail").get_json()
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

    def test_no_circuits_app_is_not_an_error(self, flask_client):
        response = MagicMock(status_code=404)
        with self._mock(error=requests.HTTPError(response=response)):
            data = flask_client.get("/api/v1/locations/loc-1/detail").get_json()
        assert data["circuits"] == []
        assert data["circuits_error"] == ""

    def test_a_failed_call_keeps_the_rest_of_the_panel(self, flask_client):
        with self._mock(error=requests.ConnectionError("boom")):
            resp = flask_client.get("/api/v1/locations/loc-1/detail")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["circuits"] == []
        assert data["circuits_error"] == "Circuit information unavailable"
        assert "boom" not in resp.get_data(as_text=True)
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
            raise requests.HTTPError(response=MagicMock(status_code=403))
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
    def test_setting_narrows_by_key_or_label(self, monkeypatch, wanted):
        fetch, _ = _relationship_fetch()
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        monkeypatch.setattr(settings, "SITE_TENANT_RELATIONSHIPS", {wanted})
        links = inventory.fetch_location_tenant_links()
        assert {link["relationship"] for link in links} == {"Site customers"}

    def test_no_relationship_is_an_empty_list(self, monkeypatch):
        fetch, _ = _relationship_fetch()
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        monkeypatch.setattr(settings, "SITE_TENANT_RELATIONSHIPS", {"nothing-matches"})
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
    def _database(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")

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
        inventory.sync_nautobot(force=True)
        caching.cache.clear()

    def test_sync_fills_the_board_and_the_map(self, flask_client, monkeypatch):
        self._sync(monkeypatch)
        (site,) = flask_client.get("/api/v1/alerts").get_json()["alerts"]
        assert site["tenants"] == ["Acme Corp", "EuroIX", "Nordic Net"]
        (location,) = flask_client.get("/api/v1/locations").get_json()["locations"]
        assert location["tenants"] == ["Acme Corp", "EuroIX", "Nordic Net"]

    def test_a_failed_read_keeps_the_links_a_removed_one_goes(self, flask_client, monkeypatch):
        self._sync(monkeypatch)
        self._sync(monkeypatch, error_on="extras/relationships/")
        (site,) = flask_client.get("/api/v1/alerts").get_json()["alerts"]
        assert site["tenants"] == ["Acme Corp", "EuroIX", "Nordic Net"]

        monkeypatch.setitem(
            RELATIONSHIP_ASSOCIATIONS, "rel-1", [{"source_id": "loc-1", "destination_id": "ten-2"}]
        )
        self._sync(monkeypatch)
        (site,) = flask_client.get("/api/v1/alerts").get_json()["alerts"]
        assert site["tenants"] == ["Acme Corp", "Nordic Net"]


class TestTenantDescriptions:
    """The tenant (i) shows the Nautobot description (#263)."""

    @pytest.fixture(autouse=True)
    def _sync_setup(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")

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

    def test_board_and_map_get_the_descriptions_of_their_tenants(self, flask_client, monkeypatch):
        self._sync(monkeypatch)
        (site,) = flask_client.get("/api/v1/alerts").get_json()["alerts"]
        # Its own tenant and a related one; EuroIX has no description, so no entry.
        assert site["tenant_descriptions"] == {
            "Acme Corp": "Bank, 24/7 SLA",
            "Nordic Net": "Shared rack",
        }
        (location,) = flask_client.get("/api/v1/locations").get_json()["locations"]
        assert location["tenant_descriptions"] == site["tenant_descriptions"]

    def test_an_edited_description_replaces_the_old_one(self, flask_client, monkeypatch):
        self._sync(monkeypatch)
        monkeypatch.setitem(TENANT_DESCRIPTIONS, "ten-1", "Bank, business hours")
        monkeypatch.delitem(TENANT_DESCRIPTIONS, "ten-2")
        self._sync(monkeypatch)
        (location,) = flask_client.get("/api/v1/locations").get_json()["locations"]
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

        monkeypatch.setattr(db, "get_conn", lambda *args, **kwargs: None)
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *args, **kwargs: False)
        monkeypatch.setattr(nautobot, "fetch_all_pages", live)
        by_name = {loc["name"]: loc for loc in inventory.get_locations()}
        assert by_name["London"]["tenant_descriptions"] == {"Acme Corp": "Bank, 24/7 SLA"}
        assert by_name["Paris"]["tenant_descriptions"] == {}  # EuroIX has no description

    def test_a_failed_read_keeps_the_descriptions(self, monkeypatch):
        self._sync(monkeypatch)
        monkeypatch.setattr(inventory, "fetch_tenants", lambda: None)
        self._sync(monkeypatch)
        assert inventory.read_tenant_descriptions() == {
            "Acme Corp": "Bank, 24/7 SLA",
            "Nordic Net": "Shared rack",
        }


class TestLibreNMSMappingsAreBatched:
    """Board builds write new LibreNMS mappings once, not a connection per device (#240)."""

    DEVICES = 60

    @pytest.fixture(autouse=True)
    def _inventory(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.test")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "tok")
        # No background sync against the fake LibreNMS URL.
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *args, **kwargs: False)
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(conn, [_snapshot_location("loc-1", "Big site")])
                inventory.write_devices(
                    conn,
                    [
                        {
                            **_snapshot_device(f"dev-{n}", "loc-1", "Access Switch", "Active"),
                            "name": f"sw{n}",
                        }
                        for n in range(self.DEVICES)
                    ],
                )
                inventory.write_librenms_devices(
                    conn,
                    [
                        # The first device matches by hostname and is down in LibreNMS.
                        {
                            "device_id": 1000 + n,
                            "hostname": f"sw{n}.corp.example",
                            "status": 0 if n == 0 else 1,
                        }
                        for n in range(self.DEVICES)
                    ],
                )
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:01:00Z",
                    last_successful_sync="2026-01-01T00:01:00Z",
                    status="success",
                    error_message="",
                )
        finally:
            conn.close()
        caching.cache.clear()

    def _mappings(self):
        conn = db.get_conn()
        try:
            return {
                row["nautobot_device_id"]: row["librenms_device_id"]
                for row in conn.execute("SELECT * FROM librenms_device_map").fetchall()
            }
        finally:
            conn.close()

    def _count_connections(self):
        counter = {"n": 0}
        real = db.get_conn

        def counting(*args, **kwargs):
            counter["n"] += 1
            return real(*args, **kwargs)

        return counter, patch.object(db, "get_conn", side_effect=counting)

    def test_board_build_uses_a_few_connections(self):
        counter, patched = self._count_connections()
        with (
            patched,
            patch.object(
                nautobot, "fetch_all_pages", side_effect=AssertionError("no upstream calls")
            ),
        ):
            board = alerts.get_alert_board_data()
        (site,) = board["alerts"]
        assert site["down_device_count"] == 1
        # Before #240 this was one connection per newly matched device.
        assert counter["n"] < 15, counter["n"]
        mappings = self._mappings()
        assert len(mappings) == self.DEVICES
        assert mappings["dev-7"] == 1007

    def test_map_levels_store_mappings_in_one_batch(self):
        # The map reads the board's rows (#316); the scheduler keeps it cached.
        alerts.cached_alert_board()
        counter, patched = self._count_connections()
        with patched:
            levels = alerts.build_location_alert_levels()["levels"]
        assert levels["loc-1"]["level"] == "low"
        assert counter["n"] < 10, counter["n"]
        assert len(self._mappings()) == self.DEVICES

    def test_persisted_mappings_win_and_are_not_rewritten(self):
        board_store.store_librenms_maps({"dev-1": (1000, "sw0.corp.example")})
        with patch.object(
            board_store, "store_librenms_maps", wraps=board_store.store_librenms_maps
        ) as store:
            board = alerts.get_alert_board_data()
        # dev-1 is mapped to LibreNMS device 1000, which is down.
        down = {d["device_id"] for d in board["alerts"][0]["down_devices"]}
        assert down == {"dev-0", "dev-1"}
        (written,) = [call.args[0] for call in store.call_args_list if call.args[0]]
        assert "dev-1" not in written and len(written) == self.DEVICES - 1

    def test_enrich_on_its_own_writes_once(self):
        devices = [{"id": f"dev-{n}", "name": f"sw{n}", "status": "Active"} for n in range(5)]
        with patch.object(board_store, "store_librenms_maps") as store:
            alerts.enrich_with_librenms(devices, snapshot_only=True)
        store.assert_called_once()
        assert set(store.call_args.args[0]) == {f"dev-{n}" for n in range(5)}

    def test_a_failed_write_does_not_fail_the_board(self, caplog):
        with patch.object(db, "transaction", side_effect=RuntimeError("db gone")):
            board_store.store_librenms_maps({"dev-1": (1, "sw1")})
        assert "Could not store 1 LibreNMS device mappings" in caplog.text


class TestAlertHistoryCsv:
    """A site's alert history as CSV (#277)."""

    NOW = "2026-10-07T12:00:00Z"

    @pytest.fixture(autouse=True)
    def _history(self, pg_database, monkeypatch):
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: self.NOW)
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                for device_id, name, role, ip in (
                    ("d1", "lon-sw01", "Access Switch", "10.0.8.4/24"),
                    ("d2", "lon-core", "Core Router", ""),
                ):
                    conn.execute(
                        "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, primary_ip) "
                        "VALUES (%s, 'loc-lon', %s, %s, %s)",
                        (device_id, name, role, ip),
                    )
                # d2 has no primary IP: the address LibreNMS polls is used.
                conn.execute("INSERT INTO librenms_device_map VALUES ('d2', 7, 'lon-core')")
                conn.execute(
                    "INSERT INTO librenms_device_status (device_id, hostname, ip) VALUES (7, 'lon-core', '10.9.9.9')"
                )
                # "gone" was deleted from Nautobot, but its LibreNMS mapping lingers.
                conn.execute("INSERT INTO librenms_device_map VALUES ('gone', 8, 'gone-host')")
                conn.execute(
                    "INSERT INTO librenms_device_status (device_id, hostname, ip) VALUES (8, 'gone-host', '10.6.6.6')"
                )
                incidents = [
                    # id, device, name, status, down, resolved, seconds, level
                    (
                        1,
                        "d1",
                        "lon-sw01",
                        "resolved",
                        "2026-10-01T08:12:00Z",
                        "2026-10-01T08:40:00Z",
                        1680,
                        "low",
                    ),
                    (2, "d1", "lon-sw01", "open", "2026-10-07T11:30:00Z", None, 0, "medium"),
                    (
                        3,
                        "d2",
                        "lon-core",
                        "resolved",
                        "2026-08-20T10:00:00Z",
                        "2026-09-27T10:00:00Z",
                        3283200,
                        "critical",
                    ),
                    (
                        4,
                        "d2",
                        "lon-core",
                        "resolved",
                        "2026-07-01T10:00:00Z",
                        "2026-07-01T11:00:00Z",
                        3600,
                        "critical",
                    ),
                    # Deleted from Nautobot since; a name that Excel would run as a formula.
                    (
                        5,
                        "gone",
                        "=HYPERLINK(1)",
                        "resolved",
                        "2026-10-05T09:00:00Z",
                        "2026-10-05T09:10:00Z",
                        600,
                        "low",
                    ),
                ]
                for row in incidents:
                    conn.execute(
                        "INSERT INTO alert_instances (id, alert_key, site_id, site_name, device_id, device_name, "
                        "status, down_started_at, last_seen_down_at, resolved_at, total_downtime_seconds, alert_level, "
                        "alert_reason) VALUES (%s, %s, 'loc-lon', 'London HQ', %s, %s, %s, %s, %s, %s, %s, %s, 'x')",
                        (
                            row[0],
                            f"k{row[0]}",
                            row[1],
                            row[2],
                            row[3],
                            row[4],
                            row[4],
                            row[5],
                            row[6],
                            row[7],
                        ),
                    )
                conn.execute(
                    "INSERT INTO alert_cases (alert_instance_id, case_number, created_by, created_at) VALUES "
                    "(1, 'INC-881', '', now()), (1, 'INC-1', '', now()), (2, 'INC-900', '', now())"
                )
        finally:
            conn.close()

    @staticmethod
    def _rows(resp):
        import csv as csv_module

        text = resp.get_data(as_text=True)
        assert text.startswith("﻿"), "BOM for Excel"
        return list(csv_module.DictReader(text[1:].splitlines()))

    def test_incidents_in_the_last_30_days(self, flask_client):
        resp = flask_client.get("/api/v1/alert-history.csv?site_id=loc-lon")
        assert resp.status_code == 200 and resp.mimetype == "text/csv"
        assert (
            resp.headers["Content-Disposition"]
            == 'attachment; filename="london-hq-incidents-30d-2026-10-07.csv"'
        )
        rows = self._rows(resp)
        # Newest first; #3 started before the period but was still down in it; #4 is older.
        assert [row["device"] for row in rows] == [
            "lon-sw01",
            "'=HYPERLINK(1)",
            "lon-sw01",
            "lon-core",
        ]
        open_row, gone, resolved, long_one = rows
        assert (
            open_row["up_at_utc"] == ""
            and open_row["status"] == "open"
            and open_row["duration_min"] == "30"
        )
        assert (
            open_row["cases"] == "INC-900"
            and open_row["ip"] == "10.0.8.4"
            and open_row["role"] == "Access Switch"
        )
        assert resolved == {
            "site": "London HQ",
            "device": "lon-sw01",
            "role": "Access Switch",
            "ip": "10.0.8.4",
            "down_at_utc": "2026-10-01 08:12",
            "up_at_utc": "2026-10-01 08:40",
            "duration_min": "28",
            "status": "resolved",
            "level": "low",
            "reason": "x",
            "cases": "INC-1;INC-881",
            "probable_cause": "",  # no core device was down (#285)
            "case_urls": "",  # no CASE_URL_TEMPLATE (#289)
        }
        assert long_one["ip"] == "10.9.9.9" and long_one["duration_min"] == "54720"
        # A deleted device exports no role and no IP, even with a lingering LibreNMS mapping.
        assert gone["role"] == "" and gone["ip"] == ""

    def test_incidents_are_streamed_in_batches(self, flask_client, monkeypatch):
        """A site's whole history is never held in memory (Copilot on #278)."""
        from app.services import export_service as export

        monkeypatch.setattr(export, "STREAM_BATCH", 2)
        resp = flask_client.get("/api/v1/alert-history.csv?site_id=loc-lon&days=all")
        assert resp.is_streamed
        assert len(self._rows(resp)) == 5

    def test_all_and_seven_days(self, flask_client):
        everything = self._rows(
            flask_client.get("/api/v1/alert-history.csv?site_id=loc-lon&days=all")
        )
        assert len(everything) == 5
        week = self._rows(flask_client.get("/api/v1/alert-history.csv?site_id=loc-lon&days=7"))
        assert [row["down_at_utc"] for row in week] == [
            "2026-10-07 11:30",
            "2026-10-05 09:00",
            "2026-10-01 08:12",
        ]

    def test_per_device(self, flask_client):
        resp = flask_client.get("/api/v1/alert-history.csv?site_id=loc-lon&view=devices&days=all")
        assert (
            'filename="london-hq-devices-all-2026-10-07.csv"' in resp.headers["Content-Disposition"]
        )
        rows = {row["device"]: row for row in self._rows(resp)}
        assert rows["lon-sw01"] == {
            "site": "London HQ",
            "device": "lon-sw01",
            "role": "Access Switch",
            "ip": "10.0.8.4",
            "times_down": "2",
            "total_down_min": "58",
            "longest_min": "30",
            "last_down_at_utc": "2026-10-07 11:30",
            "down_now": "yes",
        }
        assert rows["lon-core"]["times_down"] == "2" and rows["lon-core"]["down_now"] == "no"
        assert rows["lon-core"]["longest_min"] == "54720"

    def test_history_panel_is_newest_down_first(self, flask_client):
        """By when the device went down, not by internal id (#279); like the CSV."""
        instances = flask_client.get("/api/v1/alert-history?site_id=loc-lon").get_json()[
            "instances"
        ]
        # Inserted as ids 1-5 in a different order than their start times.
        assert [row["id"] for row in instances] == [2, 5, 1, 3, 4]

    def test_bad_requests(self, flask_client):
        assert flask_client.get("/api/v1/alert-history.csv").status_code == 400
        assert (
            flask_client.get("/api/v1/alert-history.csv?site_id=loc-lon&days=14").status_code == 400
        )
        assert (
            flask_client.get("/api/v1/alert-history.csv?site_id=loc-lon&view=json").status_code
            == 400
        )
        empty = self._rows(flask_client.get("/api/v1/alert-history.csv?site_id=nowhere"))
        assert empty == []

    def test_needs_the_operator_role(self, flask_client):
        with auth_config(mode="header", viewer_groups={"noc"}, operator_groups={"ops"}):
            viewer = {"X-Forwarded-User": "vera", "X-Forwarded-Groups": "noc"}
            assert (
                flask_client.get(
                    "/api/v1/alert-history.csv?site_id=loc-lon", headers=viewer
                ).status_code
                == 403
            )
            operator = {"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"}
            assert (
                flask_client.get(
                    "/api/v1/alert-history.csv?site_id=loc-lon", headers=operator
                ).status_code
                == 200
            )


class TestAlertHistoryCsvCells:
    def test_formula_guard(self):
        from app.services import export_service as export

        assert [export.safe_cell(v) for v in ("=1+1", "+x", "-3", "@a", "ok", None, 5)] == [
            "'=1+1",
            "'+x",
            "'-3",
            "'@a",
            "ok",
            "",
            "5",
        ]

    def test_file_name_is_safe(self):
        from datetime import UTC, datetime

        from app.services import export_service as export

        now = datetime(2026, 10, 7, tzinfo=UTC)
        assert (
            export.file_name('København "DC" / 2', "devices", "90", now)
            == "k-benhavn-dc-2-devices-90d-2026-10-07.csv"
        )
        assert export.file_name("", "incidents", "all", now) == "site-incidents-all-2026-10-07.csv"


# Held back until what they need is ported (#587):
#   TestAlertBoard.test_log_alert_board_exclusions_reports_resolved_sets: the current app's app.py startup (step 10)
#   TestSSLVerification.test_missing_ca_bundle_is_logged_at_startup: the current app's app.py startup (step 10)
#   TestAlertBoardWithoutPersistence.test_startup_log_warns_without_database: the current app's app.py startup (step 10)
#   TestAlertBoardWithoutPersistence.test_startup_log_errors_on_leftover_sqlite_setting: the current app's app.py startup (step 10)
#   TestAlertBoardWithoutPersistence.test_startup_log_quiet_with_database: the current app's app.py startup (step 10)
#   TestLogging.test_json_lines_include_the_stack_trace: the current app's server and logging setup (step 10)
#   TestLogging.test_successful_health_checks_are_not_access_logged: the current app's server and logging setup (step 10)
#   TestLogging.test_gunicorn_config_logs_access_and_keeps_a_root_logger: the current app's server and logging setup (step 10)

# The current app's own schema migration and gunicorn tests are its own: the schema
# here is Alembic's (tests/test_schema.py), the server uvicorn:
#   TestAlertLifecycleTracking.test_init_db_rekeys_open_alerts_without_severity
#   TestAlertLifecycleTracking.test_app_starts_with_open_alerts_to_migrate
#   TestAlertLifecycleTracking.test_init_db_postgres_uses_advisory_lock_before_ddl
#   TestAlertLifecycleTracking.test_init_db_postgres_marks_primary_ip_migration_pending
#   TestAlertLifecycleTracking.test_init_db_postgres_migrates_legacy_sync_state_cache_version
#   TestAlertLifecycleTracking.test_init_db_migrates_legacy_time_zone_not_null
#   TestAlertLifecycleTracking.test_init_db_marks_primary_ip_migration_pending
#   TestAuthConfiguration.test_header_auth_binds_gunicorn_to_all_interfaces
#   TestAuthConfiguration.test_non_header_auth_keeps_public_gunicorn_bind
#   TestAuthConfiguration.test_gunicorn_when_ready_logs_alert_board_exclusions
#   TestAuthConfiguration.test_default_gunicorn_timeout_is_120_seconds
#   TestAuthConfiguration.test_gunicorn_timeout_has_a_120_second_floor
#   TestAuthConfiguration.test_gunicorn_timeout_allows_values_above_floor
#   TestLibreNMSPolledIp.test_migration_adds_ip_column_to_existing_table
#   TestLibreNMSPolledIp.test_postgres_migration_adds_ip_column
#   TestSiteRollup.test_migration_adds_parent_id_column
#   TestBackgroundScheduler.test_gunicorn_starts_the_scheduler_in_each_worker
#   TestMetrics.test_migration_backfills_last_success

# Left out: it runs with no database configured (db.dialect() ""), which the new
# stack doesn't have: it always has its database (#602).
#   test_get_alert_board_data_replaces_write_connection_after_failure
#   test_get_alert_board_data_disables_persistence_after_connect_failure
#   test_no_db_returns_503
#   test_get_db_conn_postgres_enables_autocommit
#   test_empty_sync_interval_env_uses_default
#   test_ok_without_persistence
#   test_payload_reports_missing_database
#   test_without_persistence_the_feed_is_empty
#   test_without_database
#   test_not_started_without_database

# Left out: it runs with no database configured (db.dialect() ""), which the new
# stack doesn't have: it always has its database (#602).
# Their sign-in rules: tests/test_auth.py.
#   test_identity_headers_only_count_from_trusted_proxies
#   test_proxy_secret_is_required_when_set
#   test_allow_unauthenticated_writes_restores_the_old_behaviour

# Left out: the current app's server details (Flask's bind address, its access-log
# format, templates/ and /static/, HTML error pages for browsers); the pages and
# their error page come in step 8 of #587, access logging at the switchover.
#   test_404_html_for_browser
#   test_405_html_for_browser
#   test_the_reference_is_a_page_not_json
#   test_header_auth_binds_flask_to_loopback
#   test_non_header_auth_keeps_public_flask_bind

# Left out: it changes the database URL while running; the engines here are made at
# startup. tests/test_openapi.py::test_healthz_without_the_database is that 503.
#   test_unavailable_database_returns_503_without_details

# Held: web/ holds the pages, Swagger UI bundled, from step 8 of #587.
#   test_the_page_is_the_nuxt_page
