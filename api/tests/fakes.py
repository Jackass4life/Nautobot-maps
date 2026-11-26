"""Nautobot stand-ins and helpers several test files share (#786: from test_app.py)."""

from contextlib import contextmanager

from app.v1.map import dtos as location_answers
from tests import answers
from tests.overrides import overridden


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
    role = (default_role or "").strip().lower()
    with overridden(
        AUTH_MODE=mode,
        AUTH_HEADER_USER=user_header,
        AUTH_HEADER_GROUPS=groups_header,
        AUTH_VIEWER_GROUPS=set(viewer_groups or set()),
        AUTH_OPERATOR_GROUPS=set(operator_groups or set()),
        AUTH_ADMIN_GROUPS=set(admin_groups or set()),
        AUTH_DEFAULT_ROLE=role if role in ("viewer", "operator", "admin") else "",
    ):
        yield


def snapshot_location(location_id: str, name: str, location_type: str = "Data Center") -> dict:
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


def snapshot_device(device_id: str, location_id: str, role: str, status: str) -> dict:
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
