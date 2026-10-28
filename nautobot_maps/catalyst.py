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

import requests

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
    return http.request(
        method,
        f"{controller['url']}{path}",
        verify=controller.get("verify_ssl", True),
        timeout=TIMEOUT_SECONDS,
        **kwargs,
    )


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
    """What the app keeps of one AP: its serial, MAC, name and address (#521), and whether it is up."""
    return {
        "serial": str(device.get("serialNumber") or "").strip(),
        "mac": str(device.get("macAddress") or "").strip().lower(),
        "name": str(device.get("hostname") or "").strip(),
        "ip": str(device.get("managementIpAddress") or "").strip(),
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


# Plug and Play (#534): an AP being onboarded isn't in the device inventory
# until it has joined its controller.  A few dozen at a time, hundreds in a
# large rollout: pages of 500, at most 10,000.
PNP_PAGE_SIZE = 500
PNP_MAX_PAGES = 20


def _error_text(value) -> str:
    """``errorDetails`` as text: a string, or an object's message and details."""
    if isinstance(value, dict):
        parts = [str(value.get(key) or "").strip() for key in ("message", "details")]
        return " ".join(dict.fromkeys(part for part in parts if part))
    return str(value or "").strip()


def pnp_record(device: dict) -> dict:
    """What the app keeps of one Plug and Play device: its serial (the match),
    name, model, where it is in onboarding and why it failed."""
    info = device.get("deviceInfo") if isinstance(device.get("deviceInfo"), dict) else {}
    last_contact = info.get("lastContact")
    return {
        "serial": str(info.get("serialNumber") or "").strip(),
        "hostname": str(info.get("hostname") or "").strip(),
        "pid": str(info.get("pid") or "").strip(),
        "state": str(info.get("state") or "").strip(),
        "onb_state": str(info.get("onbState") or "").strip(),
        "error": _error_text(info.get("errorDetails")),
        # Milliseconds since the epoch.
        "last_contact": last_contact / 1000 if isinstance(last_contact, int | float) and last_contact > 0 else None,
    }


def fetch_pnp_devices(controller: dict) -> list[dict]:
    """Every device Plug and Play lists on this instance (#534), in pages."""
    devices = []
    for page in range(PNP_MAX_PAGES):
        data = get(
            controller,
            "/dna/intent/api/v1/onboarding/pnp-device",
            {"offset": page * PNP_PAGE_SIZE, "limit": PNP_PAGE_SIZE},
        )
        # The list itself; some releases wrap it in "response".
        listed = data if isinstance(data, list) else (data.get("response") or [] if isinstance(data, dict) else [])
        devices.extend(device for device in listed if isinstance(device, dict))
        if len(listed) < PNP_PAGE_SIZE:
            return devices
    raise RuntimeError(f"Catalyst Center's Plug and Play listed more than {PNP_MAX_PAGES * PNP_PAGE_SIZE} devices")
