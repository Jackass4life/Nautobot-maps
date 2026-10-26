"""HPE Aruba Networking New Central REST API client, for access point status (#508).

One controller is a dict from ``settings.WIRELESS_CONTROLLERS``.  A
Central-scoped API client (client id and secret from the GreenLake
workspace) gets a token from HPE's sign-in service with the OAuth client
credentials grant: no refresh token to store, a new one when it is about to
expire (``expires_in``, two hours) or is refused.  The access points are read
in pages of 1,000 from ``/network-monitoring/v1/aps``, following ``next``:
a few requests for every AP, never one per site or per AP.

Called as ``arubacentral.function()`` so tests can replace it on this module.
Neither the client secret nor the token is ever logged.
"""

import threading
import time

import requests

from nautobot_maps import http, settings

PAGE_SIZE = 1000
# Safety net against a server whose cursor never ends: 200,000 APs.
MAX_PAGES = 200
# (connect, read): an instance that doesn't answer is given up on soon.
TIMEOUT_SECONDS = (10, 30)
# A new token this long before the old one expires.
TOKEN_MARGIN_SECONDS = 5 * 60

_tokens: dict[tuple[str, str], tuple[str, float]] = {}  # (token URL, client id): (token, when to renew)
_tokens_lock = threading.Lock()


def _request(controller: dict, method: str, url: str, **kwargs) -> requests.Response:
    # HPE's own sign-in service is always verified: verify_ssl is for a
    # Central behind a private CA or in a lab, never for sending the secret.
    verify = True if url == settings.ARUBA_CENTRAL_TOKEN_URL else controller.get("verify_ssl", True)
    return http.request(method, url, verify=verify, timeout=TIMEOUT_SECONDS, **kwargs)


def token(controller: dict, renew: bool = False) -> str:
    """This API client's token, asking for a new one when it is about to expire or *renew*."""
    key = (controller["token_url"], controller["client_id"])
    with _tokens_lock:
        cached = _tokens.get(key)
        if cached and not renew and time.monotonic() < cached[1]:
            return cached[0]
    response = _request(
        controller,
        "POST",
        controller["token_url"],
        data={
            "grant_type": "client_credentials",
            "client_id": controller["client_id"],
            "client_secret": controller["client_secret"],
        },
        headers={"Accept": "application/json"},
    )
    body = response.json()
    value = body.get("access_token") if isinstance(body, dict) else None
    if not value or not isinstance(value, str):
        raise RuntimeError("Aruba Central's sign-in answered without an access token")
    try:
        lifetime = int(body.get("expires_in") or 7200)
    except (TypeError, ValueError):
        lifetime = 7200
    with _tokens_lock:
        _tokens[key] = (value, time.monotonic() + max(60, lifetime - TOKEN_MARGIN_SECONDS))
    return value


def get(controller: dict, path: str, params: dict) -> dict:
    """GET with the client's token; asks for a new one once if it is refused."""
    for attempt in (1, 2):
        headers = {"Authorization": f"Bearer {token(controller, renew=attempt == 2)}", "Accept": "application/json"}
        try:
            return (
                _request(controller, "GET", f"{controller['url']}{path}", headers=headers, params=params).json() or {}
            )
        except requests.HTTPError as exc:
            if attempt == 1 and exc.response is not None and exc.response.status_code == 401:
                continue
            raise
    return {}


def ap_record(item: dict) -> dict:
    """What the app keeps of one AP: its serial, MAC and name, and whether it is up."""
    return {
        "serial": str(item.get("serialNumber") or "").strip(),
        "mac": str(item.get("macAddress") or "").strip().lower(),
        "name": str(item.get("deviceName") or "").strip(),
        "up": str(item.get("status") or "").strip().upper() == "ONLINE",
    }


def fetch_aps(controller: dict) -> list[dict]:
    """Every access point this instance knows, as New Central lists them."""
    aps = []
    cursor = None
    seen = set()
    for _ in range(MAX_PAGES):
        params = {"limit": PAGE_SIZE}
        if cursor:
            params["next"] = str(cursor)
        data = get(controller, "/network-monitoring/v1/aps", params)
        if not isinstance(data, dict):
            raise RuntimeError("Aruba Central answered the access point list with something other than an object")
        aps.extend(item for item in data.get("items") or [] if isinstance(item, dict))
        cursor = data.get("next")
        if not cursor:
            return aps
        if str(cursor) in seen:
            raise RuntimeError(f"Aruba Central's next page cursor repeated ({cursor}); stopped")
        seen.add(str(cursor))
    raise RuntimeError(f"Aruba Central listed more than {MAX_PAGES * PAGE_SIZE} access points; stopped")
