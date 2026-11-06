"""LibreNMS REST API client (#165, #596): the current app's nautobot_maps/librenms.py.

Called as ``librenms.function()`` so tests can replace it on this module.
"""

import warnings
from urllib.parse import quote

import requests
from urllib3.exceptions import InsecureRequestWarning

from app.clients import http
from app.utils.config import get_settings


def get(path: str, params: dict | None = None) -> dict:
    """Perform a GET request against the LibreNMS REST API."""
    settings = get_settings()
    base_url = (settings.librenms_url or "").strip().rstrip("/")
    api_token = (settings.librenms_api_token or "").strip()
    if not base_url or not api_token:
        return {}

    headers = {"X-Auth-Token": api_token}
    url = f"{base_url}/api/v0/{path.lstrip('/')}"
    if settings.librenms_verify_ssl is False:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            response = http.session().get(
                url, headers=headers, params=params, timeout=15, verify=False
            )
    else:
        response = http.session().get(
            url, headers=headers, params=params, timeout=15, verify=settings.librenms_verify_ssl
        )
    response.raise_for_status()
    return response.json()


def fetch_device(device: str) -> dict | None:
    """One device by LibreNMS id or hostname (#284); None when LibreNMS doesn't know it."""
    try:
        data = get(f"devices/{quote(device, safe='')}")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
    devices = data.get("devices") or []
    return devices[0] if devices else None


def configured() -> bool:
    settings = get_settings()
    return bool(
        (settings.librenms_url or "").strip() and (settings.librenms_api_token or "").strip()
    )


def fetch_inventory() -> list:
    """Fetch full LibreNMS inventory once."""
    if not configured():
        return []
    data = get("devices", {"type": "all"})
    return data.get("devices", [])
