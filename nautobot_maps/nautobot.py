"""Nautobot REST API client and lookup maps (#165).

Other modules call these as ``nautobot.function()`` so tests can replace them
with ``monkeypatch.setattr(nautobot, "fetch_all_pages", ...)``.
"""

import logging

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

from nautobot_maps import caching, settings

logger = logging.getLogger(__name__)


def configure_ssl_warnings() -> None:
    if settings.NAUTOBOT_VERIFY_SSL is False:
        urllib3.disable_warnings(InsecureRequestWarning)


def nested_str(obj: dict | None, *keys: str) -> str:
    """Return the first non-empty value found in *obj* for the given keys.

    Nautobot 2.x uses ``name`` / ``label`` for nested objects; Nautobot 3.x
    returns a full model representation that uses ``display``.  Trying all
    three keys keeps the code compatible with both versions and with the
    mock fixtures used in unit/integration tests.
    """
    if not obj:
        return ""
    if isinstance(obj, str):
        return obj
    if not isinstance(obj, dict):
        return str(obj)
    for key in keys:
        val = obj.get(key)
        if val is not None and val != "":
            return str(val)
    return ""


def id_name_map(endpoint: str) -> dict:
    """Fetch all objects from *endpoint* and return a ``{id: display_name}`` map.

    Used as a fallback when nested objects in Nautobot's response don't
    include a human-readable field (e.g. some Nautobot 3.x builds return
    brief nested objects with only ``id`` and ``url``).
    """
    try:
        items = fetch_all_pages(endpoint)
        result = {}
        for item in items:
            uid = item.get("id")
            if not uid:
                continue
            name = nested_str(item, "name", "display", "label", "slug")
            if name:
                result[uid] = name
        return result
    except Exception as exc:
        logger.debug("Could not build name lookup for %s: %s", endpoint, exc)
        return {}


def device_type_maps() -> tuple:
    """Return ``({device_type_id: manufacturer_name}, {device_type_id: model_name})``.

    In Nautobot 3.x the brief nested ``device_type`` object returned inside
    device list responses does **not** include ``manufacturer`` or ``model``
    fields — only ``id`` and ``url``.  Fetching all device types once lets us
    resolve both fields for any device without extra per-device API calls.

    The manufacturer sub-object inside a device-type listing may itself be a
    brief object (id+url only in Nautobot 3.0.x), so we also build a
    manufacturer UUID→name map and fall back to it when the inline name is
    missing.
    """
    try:
        mfr_map = id_name_map("dcim/manufacturers/")
        items = fetch_all_pages("dcim/device-types/")
        dt_mfr: dict = {}
        dt_model: dict = {}
        for item in items:
            uid = item.get("id")
            if not uid:
                continue
            # model name
            model = item.get("model") or nested_str(item, "display") or ""
            if model:
                dt_model[uid] = model
            # manufacturer name
            mfr_obj = item.get("manufacturer") or {}
            mfr_id = mfr_obj.get("id", "") if isinstance(mfr_obj, dict) else ""
            mfr_name = nested_str(mfr_obj, "name", "display") or mfr_map.get(mfr_id, "")
            if mfr_name:
                dt_mfr[uid] = mfr_name
        return dt_mfr, dt_model
    except Exception as exc:
        logger.debug("Could not build device-type maps: %s", exc)
        return {}, {}


def tenant_group_map() -> dict:
    """Return ``{tenant_id: tenant_group_name}``.

    Fetches all tenants and resolves each tenant's ``tenant_group`` field so
    that locations can expose the tenant group without extra per-location API
    calls.  A fallback name-map for tenant groups is built from the
    ``tenancy/tenant-groups/`` endpoint for Nautobot builds where the nested
    object is brief (id + url only).
    """
    try:
        tg_name_map = id_name_map("tenancy/tenant-groups/")
        tenants = fetch_all_pages("tenancy/tenants/")
        tenant_group_map: dict = {}
        for tenant in tenants:
            tid = tenant.get("id")
            if not tid:
                continue
            tg_obj = tenant.get("tenant_group") or {}
            tg_id = tg_obj.get("id", "") if isinstance(tg_obj, dict) else ""
            tg_name = nested_str(tg_obj, "name", "display") or tg_name_map.get(tg_id, "")
            if tg_name:
                tenant_group_map[tid] = tg_name
        return tenant_group_map
    except Exception as exc:
        logger.debug("Could not build tenant group map: %s", exc)
        return {}


def get(endpoint: str, params: dict | None = None) -> dict:
    """Perform a GET request against the Nautobot REST API."""
    if not settings.NAUTOBOT_URL or not settings.NAUTOBOT_TOKEN:
        raise RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set in environment variables.")
    cache_key = f"{endpoint}:{params}"
    cached = caching.get(cache_key)
    if cached is not None:
        return cached

    accept = "application/json"
    if settings.NAUTOBOT_API_VERSION:
        accept += f"; version={settings.NAUTOBOT_API_VERSION}"
    headers = {
        "Authorization": f"Token {settings.NAUTOBOT_TOKEN}",
        "Content-Type": "application/json",
        "Accept": accept,
    }
    url = f"{settings.NAUTOBOT_URL}/api/{endpoint.lstrip('/')}"
    response = requests.get(url, headers=headers, params=params, timeout=(5, 30), verify=settings.NAUTOBOT_VERIFY_SSL)
    response.raise_for_status()
    data = response.json()
    caching.set(cache_key, data)
    return data


def post(endpoint: str, payload: dict) -> dict:
    """Perform a POST request against the Nautobot REST API."""
    if not settings.NAUTOBOT_URL or not settings.NAUTOBOT_TOKEN:
        raise RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set in environment variables.")
    accept = "application/json"
    if settings.NAUTOBOT_API_VERSION:
        accept += f"; version={settings.NAUTOBOT_API_VERSION}"
    headers = {
        "Authorization": f"Token {settings.NAUTOBOT_TOKEN}",
        "Content-Type": "application/json",
        "Accept": accept,
    }
    url = f"{settings.NAUTOBOT_URL}/api/{endpoint.lstrip('/')}"
    response = requests.post(url, headers=headers, json=payload, timeout=15, verify=settings.NAUTOBOT_VERIFY_SSL)
    response.raise_for_status()
    return response.json()


def delete(endpoint: str) -> None:
    """Perform a DELETE request against the Nautobot REST API."""
    if not settings.NAUTOBOT_URL or not settings.NAUTOBOT_TOKEN:
        raise RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set in environment variables.")
    accept = "application/json"
    if settings.NAUTOBOT_API_VERSION:
        accept += f"; version={settings.NAUTOBOT_API_VERSION}"
    headers = {
        "Authorization": f"Token {settings.NAUTOBOT_TOKEN}",
        "Content-Type": "application/json",
        "Accept": accept,
    }
    url = f"{settings.NAUTOBOT_URL}/api/{endpoint.lstrip('/')}"
    response = requests.delete(url, headers=headers, timeout=15, verify=settings.NAUTOBOT_VERIFY_SSL)
    response.raise_for_status()


def fetch_all_pages(endpoint: str, params: dict | None = None) -> list:
    """Fetch all paginated results from a Nautobot API endpoint."""
    params = dict(params or {})
    params.setdefault("limit", 1000)
    params.setdefault("depth", 0)
    results = []
    offset = 0
    while True:
        params["offset"] = offset
        data = get(endpoint, params)
        results.extend(data.get("results", []))
        if not data.get("next"):
            break
        offset += params["limit"]
    return results


def device_lookup_maps() -> dict:
    dt_mfr_map, dt_model_map = device_type_maps()
    return {
        "dt_mfr_map": dt_mfr_map,
        "dt_model_map": dt_model_map,
        "mfr_map": id_name_map("dcim/manufacturers/"),
        "role_map": id_name_map("extras/roles/"),
        "tenant_map": id_name_map("tenancy/tenants/"),
        "status_map": id_name_map("extras/statuses/"),
    }
