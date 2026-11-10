"""Nautobot REST API client and lookup maps (#165, #596): READ-ONLY, never writes.
The current app's nautobot_maps/nautobot.py.

Other modules call these as ``nautobot.function()`` so tests can replace them
with ``monkeypatch.setattr(nautobot, "fetch_all_pages", ...)``.
"""

import urllib3
from loguru import logger
from urllib3.exceptions import InsecureRequestWarning

from app.clients import caching, http
from app.core.settings import get_settings


def configure_ssl_warnings() -> None:
    if get_settings().nautobot_verify_ssl is False:
        urllib3.disable_warnings(InsecureRequestWarning)


def nested_str(obj: object, *keys: str) -> str:
    """The first non-empty value found in *obj* for the given keys.

    Nautobot 2.x uses ``name`` / ``label`` for nested objects; Nautobot 3.x returns
    a full model representation that uses ``display``. Trying all three keys keeps
    the code compatible with both versions and with the mock fixtures.
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
    """All objects of *endpoint* as ``{id: display_name}``: the fallback when nested
    objects carry only ``id`` and ``url`` (some Nautobot 3.x builds)."""
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
    except Exception as exc:  # noqa: BLE001 - a missing map only loses names
        logger.debug("Could not build name lookup for {}: {}", endpoint, exc)
        return {}


def device_type_maps() -> tuple[dict, dict]:
    """``({device_type_id: manufacturer_name}, {device_type_id: model_name})``.

    In Nautobot 3.x the brief nested ``device_type`` inside device lists has only
    ``id`` and ``url``; one fetch of every device type resolves both for any device.
    Its manufacturer may itself be brief, so a manufacturer map is the fallback.
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
            model = item.get("model") or nested_str(item, "display") or ""
            if model:
                dt_model[uid] = model
            mfr_obj = item.get("manufacturer") or {}
            mfr_id = mfr_obj.get("id", "") if isinstance(mfr_obj, dict) else ""
            mfr_name = nested_str(mfr_obj, "name", "display") or mfr_map.get(mfr_id, "")
            if mfr_name:
                dt_mfr[uid] = mfr_name
        return dt_mfr, dt_model
    except Exception as exc:  # noqa: BLE001 - a missing map only loses names
        logger.debug("Could not build device-type maps: {}", exc)
        return {}, {}


def tenant_group_map() -> dict:
    """``{tenant_id: tenant_group_name}``, with a tenant-group name map as the
    fallback for brief nested objects."""
    try:
        tg_name_map = id_name_map("tenancy/tenant-groups/")
        tenants = fetch_all_pages("tenancy/tenants/")
        groups: dict = {}
        for tenant in tenants:
            tid = tenant.get("id")
            if not tid:
                continue
            tg_obj = tenant.get("tenant_group") or {}
            tg_id = tg_obj.get("id", "") if isinstance(tg_obj, dict) else ""
            tg_name = nested_str(tg_obj, "name", "display") or tg_name_map.get(tg_id, "")
            if tg_name:
                groups[tid] = tg_name
        return groups
    except Exception as exc:  # noqa: BLE001 - a missing map only loses names
        logger.debug("Could not build tenant group map: {}", exc)
        return {}


def get(endpoint: str, params: dict | None = None, *, use_cache: bool = True) -> dict:
    """GET against the Nautobot REST API.

    Cached for ``CACHE_TTL`` unless *use_cache* is false. The inventory sync must
    not use the cache: it would be served a previous sync's pages and miss changes
    (#185).
    """
    settings = get_settings()
    if not settings.nautobot_url or not settings.nautobot_token:
        raise RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set in environment variables.")
    cache_key = f"{endpoint}:{params}"
    if use_cache:
        cached = caching.get(cache_key)
        if cached is not None:
            return cached

    accept = "application/json"
    if settings.nautobot_api_version:
        accept += f"; version={settings.nautobot_api_version}"
    headers = {
        "Authorization": f"Token {settings.nautobot_token}",
        "Content-Type": "application/json",
        "Accept": accept,
    }
    url = f"{settings.nautobot_url}/api/{endpoint.lstrip('/')}"
    response = http.session().get(
        url, headers=headers, params=params, timeout=(5, 30), verify=settings.nautobot_verify_ssl
    )
    response.raise_for_status()
    data = response.json()
    if use_cache:
        caching.set(cache_key, data)
    return data


def fetch_all_pages(endpoint: str, params: dict | None = None, *, use_cache: bool = True) -> list:
    """Every page of a Nautobot list endpoint (see ``get`` for *use_cache*)."""
    params = dict(params or {})
    params.setdefault("limit", 1000)
    params.setdefault("depth", 0)
    results = []
    offset = 0
    while True:
        params["offset"] = offset
        data = get(endpoint, params, use_cache=use_cache)
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
