"""Cisco Catalyst Center REST API client, for access point status (#508).

One controller is a dict from ``settings.WIRELESS_CONTROLLERS``.  A token
from ``POST /dna/system/api/v1/auth/token`` (basic auth, a read-only user) is
kept per instance until it is about to expire or is refused.  The access
points are read in pages of 500 from ``/dna/intent/api/v1/network-device``,
filtered to the ``Unified AP`` family: a few requests for every AP, never
one per site or per AP.

Called as ``catalyst.function()`` so tests can replace it on this module.
Neither the password nor the token is ever logged.
"""

import threading
import time
import warnings

import requests
from urllib3.exceptions import InsecureRequestWarning

from nautobot_maps import http

PAGE_SIZE = 500
# Safety net against a server that keeps answering full pages: 100,000 APs.
MAX_PAGES = 200
# (connect, read): an instance that doesn't answer is given up on soon.
TIMEOUT_SECONDS = (10, 30)
# Catalyst Center tokens last an hour; a new one well before that.
TOKEN_SECONDS = 45 * 60

_tokens: dict[str, tuple[str, float]] = {}  # instance URL: (token, when it was issued)
_tokens_lock = threading.Lock()


def _request(controller: dict, method: str, path: str, **kwargs) -> requests.Response:
    verify = controller.get("verify_ssl", True)
    url = f"{controller['url']}{path}"
    with warnings.catch_warnings():
        if verify is False:
            warnings.simplefilter("ignore", InsecureRequestWarning)
        response = http.session().request(method, url, timeout=TIMEOUT_SECONDS, verify=verify, **kwargs)
    response.raise_for_status()
    return response


def token(controller: dict, renew: bool = False) -> str:
    """This instance's API token, signing in again when it is old or *renew*."""
    key = controller["url"]
    with _tokens_lock:
        cached = _tokens.get(key)
        if cached and not renew and time.monotonic() - cached[1] < TOKEN_SECONDS:
            return cached[0]
    response = _request(
        controller,
        "POST",
        "/dna/system/api/v1/auth/token",
        auth=(controller["username"], controller["password"]),
    )
    value = (response.json() or {}).get("Token") or ""
    if not value:
        raise RuntimeError("Catalyst Center answered the sign-in without a token")
    with _tokens_lock:
        _tokens[key] = (value, time.monotonic())
    return value


def get(controller: dict, path: str, params: dict) -> dict:
    """GET with the instance's token; signs in again once if it is refused."""
    for attempt in (1, 2):
        headers = {"X-Auth-Token": token(controller, renew=attempt == 2), "Accept": "application/json"}
        try:
            return _request(controller, "GET", path, headers=headers, params=params).json() or {}
        except requests.HTTPError as exc:
            if attempt == 1 and exc.response is not None and exc.response.status_code == 401:
                continue
            raise
    return {}


def ap_record(device: dict) -> dict:
    """What the app keeps of one AP: its serial, MAC and name, and whether it is up."""
    return {
        "serial": str(device.get("serialNumber") or "").strip(),
        "mac": str(device.get("macAddress") or "").strip().lower(),
        "name": str(device.get("hostname") or "").strip(),
        "up": str(device.get("reachabilityStatus") or "").strip().lower() == "reachable",
    }


def fetch_devices(controller: dict) -> list[dict]:
    """Every access point this instance knows, as Catalyst Center lists them."""
    aps = []
    for page in range(MAX_PAGES):
        # Catalyst Center's offset starts at 1.
        data = get(
            controller,
            "/dna/intent/api/v1/network-device",
            {"family": "Unified AP", "offset": page * PAGE_SIZE + 1, "limit": PAGE_SIZE},
        )
        devices = data.get("response") or []
        aps.extend(device for device in devices if isinstance(device, dict))
        if len(devices) < PAGE_SIZE:
            return aps
    raise RuntimeError(f"Catalyst Center listed more than {MAX_PAGES * PAGE_SIZE} access points; stopped")
