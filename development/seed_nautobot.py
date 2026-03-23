#!/usr/bin/env python3
"""
Seed a real Nautobot instance with the same data used by demo/mock_nautobot.py.

This script talks to the Nautobot REST API, creating location types, tenants,
locations (with GPS coordinates and ASN), manufacturers, device types, roles,
and devices so that the nautobot-maps integration tests can validate against a
genuine Nautobot backend.

Note: In Nautobot 3.x core the ``/api/ipam/asns/`` endpoint does not exist
(it is provided by the optional BGP Models plugin).  ASN numbers are stored
directly as the ``asn`` integer field on each Location.

Usage:
    NAUTOBOT_URL=http://localhost:8080 \
    NAUTOBOT_TOKEN=aaaa-bbbb-cccc-dddd-eeee \
    python development/seed_nautobot.py

The script is idempotent – running it twice will not create duplicates.
"""

import os
import sys
import time

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NAUTOBOT_URL = os.environ.get("NAUTOBOT_URL", "http://localhost:8080").rstrip("/")
NAUTOBOT_TOKEN = os.environ.get("NAUTOBOT_TOKEN", "aaaa-bbbb-cccc-dddd-eeee")

session = requests.Session()
session.headers.update(
    {
        "Authorization": f"Token {NAUTOBOT_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
)
_verify_env = os.environ.get("NAUTOBOT_VERIFY_SSL", "false").strip().lower()
if _verify_env == "true":
    session.verify = True
elif _verify_env == "false":
    session.verify = False
else:
    session.verify = _verify_env  # treat as CA bundle path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def wait_for_nautobot(timeout: int = 300) -> None:
    """Block until the Nautobot API is reachable."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = session.get(f"{NAUTOBOT_URL}/api/", timeout=5)
            if resp.status_code < 500:
                print(f"  Nautobot API reachable ({resp.status_code})")
                return
        except requests.ConnectionError:
            pass
        elapsed = int(time.time() - start)
        print(f"  Waiting for Nautobot … ({elapsed}s / {timeout}s)")
        time.sleep(5)
    print("ERROR: Nautobot did not become ready in time", file=sys.stderr)
    sys.exit(1)


def _get(endpoint: str, params: dict | None = None) -> dict:
    """GET helper with error handling."""
    resp = session.get(f"{NAUTOBOT_URL}/api/{endpoint}", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _post(endpoint: str, data: dict) -> dict:
    """POST helper with error handling."""
    resp = session.post(f"{NAUTOBOT_URL}/api/{endpoint}", json=data, timeout=30)
    if not resp.ok:
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:500]
        print(f"  ERROR {resp.status_code} POST {endpoint}: {body}", file=sys.stderr)
    resp.raise_for_status()
    return resp.json()


def get_or_create(endpoint: str, data: dict, lookup: dict | None = None) -> dict:
    """
    Return an existing object or create a new one.

    *lookup* defaults to ``{"name": data["name"]}`` if not given.
    """
    lookup = lookup or {"name": data.get("name", data.get("model", ""))}
    results = _get(endpoint, params=lookup).get("results", [])
    if results:
        print(f"  ↳ found  {endpoint}  {lookup}")
        return results[0]

    obj = _post(endpoint, data)
    print(f"  ↳ created {endpoint}  {lookup}")
    return obj


def lookup_status(name: str) -> dict:
    """Retrieve a built-in status by name."""
    results = _get("extras/statuses/", {"name": name}).get("results", [])
    if not results:
        raise RuntimeError(f"Status '{name}' not found in Nautobot")
    return results[0]


# ---------------------------------------------------------------------------
# Seed data (mirrors demo/mock_nautobot.py)
# ---------------------------------------------------------------------------


def seed() -> None:  # noqa: C901 – sequential-but-simple setup script
    print("\n=== Seeding Nautobot ===\n")
    wait_for_nautobot()

    # -- Statuses --------------------------------------------------------
    print("[1/8] Looking up statuses …")
    active = lookup_status("Active")
    planned = lookup_status("Planned")
    print(f"  Active  → {active['id']}")
    print(f"  Planned → {planned['id']}")

    # -- Location types --------------------------------------------------
    print("[2/8] Creating location types …")
    lt = {}
    for name in ("Data Center", "PoP", "Office", "Internet Exchange"):
        lt[name] = get_or_create(
            "dcim/location-types/",
            {"name": name, "nestable": True, "content_types": ["dcim.device"]},
        )

    # -- Tenants ---------------------------------------------------------
    print("[3/8] Creating tenants …")
    tenants = {}
    for name in ("Acme Corp", "Nordic Net", "EuroIX", "DataCenter GmbH"):
        tenants[name] = get_or_create("tenancy/tenants/", {"name": name})

    # -- Locations -------------------------------------------------------
    # ASN is a plain integer field on the Location object in Nautobot 3.x;
    # /api/ipam/asns/ is a BGP-plugin endpoint and is not available in core.
    print("[4/8] Creating locations …")
    location_defs = [
        {
            "name": "Copenhagen DC",
            "location_type": lt["Data Center"]["id"],
            "status": active["id"],
            "tenant": tenants["Acme Corp"]["id"],
            "latitude": 55.6761,
            "longitude": 12.5683,
            "asn": 65001,
            "physical_address": "Vermlandsgade 51, 2300 Copenhagen, Denmark",
            "time_zone": "Europe/Copenhagen",
            "description": "Primary Scandinavian data centre",
        },
        {
            "name": "Copenhagen Colocation",
            "location_type": lt["Data Center"]["id"],
            "status": active["id"],
            "tenant": tenants["Nordic Net"]["id"],
            "latitude": 55.6830,
            "longitude": 12.5750,
            "asn": 65002,
            "physical_address": "Borgergade 10, 1300 Copenhagen, Denmark",
            "time_zone": "Europe/Copenhagen",
            "description": "Secondary colocation facility",
        },
        {
            "name": "Stockholm PoP",
            "location_type": lt["PoP"]["id"],
            "status": active["id"],
            "tenant": tenants["Acme Corp"]["id"],
            "latitude": 59.3293,
            "longitude": 18.0686,
            "asn": 65010,
            "physical_address": "Stureplan 4, 114 35 Stockholm, Sweden",
            "time_zone": "Europe/Stockholm",
            "description": "Stockholm internet exchange point",
        },
        {
            "name": "Oslo Office",
            "location_type": lt["Office"]["id"],
            "status": planned["id"],
            "tenant": tenants["Nordic Net"]["id"],
            "latitude": 59.9139,
            "longitude": 10.7522,
            "physical_address": "Karl Johans gate 14, 0154 Oslo, Norway",
            "time_zone": "Europe/Oslo",
            "description": "Future Oslo regional office",
        },
        {
            "name": "Amsterdam Internet Exchange",
            "location_type": lt["Internet Exchange"]["id"],
            "status": active["id"],
            "tenant": tenants["EuroIX"]["id"],
            "latitude": 52.3676,
            "longitude": 4.9041,
            "asn": 65020,
            "physical_address": "Frederiksplein 42, 1017 XN Amsterdam, Netherlands",
            "time_zone": "Europe/Amsterdam",
            "description": "AMS-IX peering facility",
        },
        {
            "name": "Frankfurt DC",
            "location_type": lt["Data Center"]["id"],
            "status": active["id"],
            "tenant": tenants["DataCenter GmbH"]["id"],
            "latitude": 50.1109,
            "longitude": 8.6821,
            "asn": 65030,
            "physical_address": "Hanauer Landstrasse 298, 60314 Frankfurt, Germany",
            "time_zone": "Europe/Berlin",
            "description": "DE-CIX Frankfurt data centre",
        },
        {
            "name": "Paris PoP",
            "location_type": lt["PoP"]["id"],
            "status": active["id"],
            "tenant": tenants["Acme Corp"]["id"],
            "latitude": 48.8566,
            "longitude": 2.3522,
            "asn": 65040,
            "physical_address": "Rue de la Paix 10, 75002 Paris, France",
            "time_zone": "Europe/Paris",
            "description": "Paris Telecom point of presence",
        },
        {
            "name": "London HQ",
            "location_type": lt["Data Center"]["id"],
            "status": active["id"],
            "tenant": tenants["Acme Corp"]["id"],
            "latitude": 51.5074,
            "longitude": -0.1278,
            "asn": 65050,
            "physical_address": "1 Canada Square, Canary Wharf, London E14 5AB, UK",
            "time_zone": "Europe/London",
            "description": "Corporate headquarters and primary UK facility",
        },
    ]

    locations = {}
    for loc_def in location_defs:
        locations[loc_def["name"]] = get_or_create("dcim/locations/", loc_def)

    # -- Manufacturers ---------------------------------------------------
    print("[5/8] Creating manufacturers …")
    manufacturers = {}
    for name in (
        "Cisco",
        "Juniper Networks",
        "Palo Alto Networks",
        "Nokia",
        "F5",
    ):
        manufacturers[name] = get_or_create("dcim/manufacturers/", {"name": name})

    # -- Device types ----------------------------------------------------
    print("[6/8] Creating device types …")
    device_type_defs = [
        ("ASR1001-X", "Cisco"),
        ("Catalyst 9300", "Cisco"),
        ("PA-3260", "Palo Alto Networks"),
        ("MX204", "Juniper Networks"),
        ("ASR9001", "Cisco"),
        ("PTX5000", "Juniper Networks"),
        ("QFX10002-72Q", "Juniper Networks"),
        ("7750 SR-12", "Nokia"),
        ("Nexus 9336C-FX2", "Cisco"),
        ("ASR9006", "Cisco"),
        ("PA-5250", "Palo Alto Networks"),
        ("BIG-IP i5800", "F5"),
    ]
    device_types = {}
    for model, mfr_name in device_type_defs:
        device_types[model] = get_or_create(
            "dcim/device-types/",
            {
                "model": model,
                "manufacturer": manufacturers[mfr_name]["id"],
            },
            lookup={"model": model},
        )

    # -- Roles -----------------------------------------------------------
    print("[7/8] Creating roles …")
    role_names = (
        "Core Router",
        "Distribution Switch",
        "Firewall",
        "Edge Router",
        "Peering Switch",
        "ToR Switch",
        "Load Balancer",
    )
    roles = {}
    for name in role_names:
        roles[name] = get_or_create(
            "extras/roles/",
            {
                "name": name,
                "content_types": ["dcim.device"],
                "color": "4caf50",
            },
        )

    # -- Devices ---------------------------------------------------------
    print("[8/8] Creating devices …")
    device_defs = [
        # Copenhagen DC
        ("cph-core-rt01", "ASR1001-X", "Core Router", "Copenhagen DC", "Acme Corp", "IOS-XE", "FCZ2227B0M1"),
        ("cph-dist-sw01", "Catalyst 9300", "Distribution Switch", "Copenhagen DC", "Acme Corp", "IOS-XE", "FCW2101L01D"),
        ("cph-fw01", "PA-3260", "Firewall", "Copenhagen DC", "Acme Corp", "PAN-OS", "013201006938"),
        # Copenhagen Colocation
        ("cph2-edge-rt01", "MX204", "Edge Router", "Copenhagen Colocation", "Nordic Net", "Junos", "BT0217480120"),
        # Stockholm PoP
        ("sto-core-rt01", "ASR9001", "Core Router", "Stockholm PoP", "Acme Corp", "IOS-XR", "FOX1826GXXX"),
        # Amsterdam Internet Exchange
        ("ams-rt01", "PTX5000", "Core Router", "Amsterdam Internet Exchange", "EuroIX", "Junos", "BUILTIN"),
        ("ams-rt02", "PTX5000", "Core Router", "Amsterdam Internet Exchange", "EuroIX", "Junos", "BUILTIN2"),
        ("ams-sw01", "QFX10002-72Q", "Peering Switch", "Amsterdam Internet Exchange", "EuroIX", "Junos", "VN2022000099"),
        # Frankfurt DC
        ("fra-core-rt01", "7750 SR-12", "Core Router", "Frankfurt DC", "DataCenter GmbH", "SR OS", "NS1234567890"),
        ("fra-sw01", "Nexus 9336C-FX2", "ToR Switch", "Frankfurt DC", "DataCenter GmbH", "NX-OS", "FDO2220000X"),
        # Paris PoP
        ("par-rt01", "ASR1001-X", "Edge Router", "Paris PoP", "Acme Corp", "IOS-XE", "FCZ1955C5K5"),
        # London HQ
        ("lon-core-rt01", "ASR9006", "Core Router", "London HQ", "Acme Corp", "IOS-XR", "FOX2143G0XX"),
        ("lon-fw01", "PA-5250", "Firewall", "London HQ", "Acme Corp", "PAN-OS", "015351000000"),
        ("lon-lb01", "BIG-IP i5800", "Load Balancer", "London HQ", "Acme Corp", None, "f5-abcd-1234"),
    ]

    # Look up or create platforms
    platforms = {}
    for _, _, _, _, _, platform_name, _ in device_defs:
        if platform_name and platform_name not in platforms:
            platforms[platform_name] = get_or_create(
                "dcim/platforms/", {"name": platform_name}
            )

    for dev_name, dt_model, role_name, loc_name, tenant_name, platform_name, serial in device_defs:
        dev_data: dict = {
            "name": dev_name,
            "device_type": device_types[dt_model]["id"],
            "role": roles[role_name]["id"],
            "location": locations[loc_name]["id"],
            "status": active["id"],
            "tenant": tenants[tenant_name]["id"],
            "serial": serial,
        }
        if platform_name:
            dev_data["platform"] = platforms[platform_name]["id"]
        get_or_create("dcim/devices/", dev_data)

    print("\n=== Seeding complete ===\n")


if __name__ == "__main__":
    seed()
