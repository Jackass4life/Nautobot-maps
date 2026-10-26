"""
mock_nautobot.py – Lightweight mock of the Nautobot REST API.

Serves realistic seed data (European data-centre locations, network devices,
ASNs and tenants) so the nautobot-maps application can be demoed and
integration-tested without a live Nautobot instance.

Endpoints implemented:
  GET /api/dcim/locations/
  GET /api/dcim/devices/        (filterable by ?location_id=)
  GET /api/ipam/asns/           (filterable by ?location_id=)
  GET /api/circuits/circuit-terminations/  (filterable by ?location=)
  GET /api/circuits/circuits/
  GET /api/extras/relationships/, /api/extras/relationship-associations/

Token auth: any request must carry  Authorization: Token <any non-empty value>
"""

import os
import random
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException, Request

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

TENANT_GROUPS = {
    "tg-corp": {"id": "tg-corp", "name": "Corporate", "slug": "corporate"},
    "tg-infra": {"id": "tg-infra", "name": "Infrastructure", "slug": "infrastructure"},
}

# Descriptions show as the tenant (i) on the alert board and the map (#263);
# EuroIX has none, so it shows how a tenant without one looks.
TENANTS = {
    "ten-acme": {
        "id": "ten-acme",
        "name": "Acme Corp",
        "slug": "acme-corp",
        "description": "Retail bank. 24/7 SLA, 4-hour fix; escalate to the Acme NOC on +45 70 00 00 00",
        "tenant_group": TENANT_GROUPS["tg-corp"],
    },
    "ten-nordnet": {
        "id": "ten-nordnet",
        "name": "Nordic Net",
        "slug": "nordic-net",
        "description": "Regional ISP. Business hours support, next-business-day fix",
        "tenant_group": TENANT_GROUPS["tg-infra"],
    },
    "ten-euroix": {
        "id": "ten-euroix",
        "name": "EuroIX",
        "slug": "euroix",
        "description": "",
        "tenant_group": TENANT_GROUPS["tg-infra"],
    },
    "ten-dcgmbh": {
        "id": "ten-dcgmbh",
        "name": "DataCenter GmbH",
        "slug": "datacenter-gmbh",
        "description": "Colocation provider (Frankfurt). Remote hands via their ticket portal",
        "tenant_group": None,
    },
}

LOCATION_TYPES = {
    "lt-dc": {"id": "lt-dc", "name": "Data Center"},
    "lt-pop": {"id": "lt-pop", "name": "PoP"},
    "lt-office": {"id": "lt-office", "name": "Office"},
    "lt-ix": {"id": "lt-ix", "name": "Internet Exchange"},
    # Hierarchy used with ALERT_BOARD_SITE_LOCATION_TYPE=Site (#158).
    "lt-region": {"id": "lt-region", "name": "Region"},
    "lt-country": {"id": "lt-country", "name": "Country"},
    "lt-site": {"id": "lt-site", "name": "Site"},
    "lt-building": {"id": "lt-building", "name": "Bygning"},
    "lt-floor": {"id": "lt-floor", "name": "Etage"},
}

STATUSES = {
    "status-active": {"id": "status-active", "name": "Active", "label": "Active"},
    "status-planned": {"id": "status-planned", "name": "Planned", "label": "Planned"},
}

LOCATIONS = [
    {
        "id": "loc-cph",
        "name": "Copenhagen DC",
        "slug": "copenhagen-dc",
        "status": {"label": "Active", "value": "active"},
        "location_type": LOCATION_TYPES["lt-dc"],
        "parent": None,
        "latitude": "55.6761",
        "longitude": "12.5683",
        "description": "Primary Scandinavian data centre",
        "physical_address": "Vermlandsgade 51, 2300 Copenhagen, Denmark",
        "tenant": TENANTS["ten-acme"],
        "asn": 65001,
        "time_zone": "Europe/Copenhagen",
        "url": "http://mock-nautobot:8080/api/dcim/locations/loc-cph/",
    },
    {
        "id": "loc-cph2",
        "name": "Copenhagen Colocation",
        "slug": "cph-colo",
        "status": {"label": "Active", "value": "active"},
        "location_type": LOCATION_TYPES["lt-dc"],
        "parent": None,
        # ~1.2 km from Copenhagen DC – will show up in 5 km proximity search
        "latitude": "55.6830",
        "longitude": "12.5750",
        "description": "Secondary colocation facility",
        "physical_address": "Borgergade 10, 1300 Copenhagen, Denmark",
        "tenant": TENANTS["ten-nordnet"],
        "asn": 65002,
        "time_zone": "Europe/Copenhagen",
        "url": "http://mock-nautobot:8080/api/dcim/locations/loc-cph2/",
    },
    {
        "id": "loc-sto",
        "name": "Stockholm PoP",
        "slug": "stockholm-pop",
        "status": {"label": "Active", "value": "active"},
        "location_type": LOCATION_TYPES["lt-pop"],
        "parent": None,
        "latitude": "59.3293",
        "longitude": "18.0686",
        "description": "Stockholm internet exchange point",
        "physical_address": "Stureplan 4, 114 35 Stockholm, Sweden",
        "tenant": TENANTS["ten-acme"],
        "asn": 65010,
        "time_zone": "Europe/Stockholm",
        "url": "http://mock-nautobot:8080/api/dcim/locations/loc-sto/",
    },
    {
        "id": "loc-osl",
        "name": "Oslo Office",
        "slug": "oslo-office",
        "status": {"label": "Planned", "value": "planned"},
        "location_type": LOCATION_TYPES["lt-office"],
        "parent": None,
        "latitude": "59.9139",
        "longitude": "10.7522",
        "description": "Future Oslo regional office",
        "physical_address": "Karl Johans gate 14, 0154 Oslo, Norway",
        "tenant": TENANTS["ten-nordnet"],
        "asn": None,
        "time_zone": "Europe/Oslo",
        "url": "http://mock-nautobot:8080/api/dcim/locations/loc-osl/",
    },
    {
        "id": "loc-ams",
        "name": "Amsterdam Internet Exchange",
        "slug": "ams-ix",
        "status": {"label": "Active", "value": "active"},
        "location_type": LOCATION_TYPES["lt-ix"],
        "parent": None,
        "latitude": "52.3676",
        "longitude": "4.9041",
        "description": "AMS-IX peering facility",
        "physical_address": "Frederiksplein 42, 1017 XN Amsterdam, Netherlands",
        "tenant": TENANTS["ten-euroix"],
        "asn": 65020,
        "time_zone": "Europe/Amsterdam",
        "url": "http://mock-nautobot:8080/api/dcim/locations/loc-ams/",
    },
    {
        "id": "loc-fra",
        "name": "Frankfurt DC",
        "slug": "frankfurt-dc",
        "status": {"label": "Active", "value": "active"},
        "location_type": LOCATION_TYPES["lt-dc"],
        "parent": None,
        "latitude": "50.1109",
        "longitude": "8.6821",
        "description": "DE-CIX Frankfurt data centre",
        "physical_address": "Hanauer Landstrasse 298, 60314 Frankfurt, Germany",
        "tenant": TENANTS["ten-dcgmbh"],
        "asn": 65030,
        "time_zone": "Europe/Berlin",
        "url": "http://mock-nautobot:8080/api/dcim/locations/loc-fra/",
    },
    {
        "id": "loc-par",
        "name": "Paris PoP",
        "slug": "paris-pop",
        "status": {"label": "Active", "value": "active"},
        "location_type": LOCATION_TYPES["lt-pop"],
        "parent": None,
        "latitude": "48.8566",
        "longitude": "2.3522",
        "description": "Paris Telecom point of presence",
        "physical_address": "Rue de la Paix 10, 75002 Paris, France",
        "tenant": TENANTS["ten-acme"],
        "asn": 65040,
        "time_zone": "Europe/Paris",
        "url": "http://mock-nautobot:8080/api/dcim/locations/loc-par/",
        "custom_fields": {"tier": "silver"},
    },
    {
        "id": "loc-lon",
        "name": "London HQ",
        "slug": "london-hq",
        "status": {"label": "Active", "value": "active"},
        "location_type": LOCATION_TYPES["lt-dc"],
        "parent": None,
        "latitude": "51.5074",
        "longitude": "-0.1278",
        "description": "Corporate headquarters and primary UK facility",
        "physical_address": "1 Canada Square, Canary Wharf, London E14 5AB, UK",
        "tenant": TENANTS["ten-acme"],
        "asn": 65050,
        "time_zone": "Europe/London",
        "url": "http://mock-nautobot:8080/api/dcim/locations/loc-lon/",
        "custom_fields": {"tier": "gold"},
    },
    {
        "id": "loc-lon2",
        "name": "London Colo",
        "slug": "london-colo",
        "status": {"label": "Active", "value": "active"},
        "location_type": LOCATION_TYPES["lt-dc"],
        "parent": None,
        # Same building as London HQ – tests co-located site handling
        "latitude": "51.5074",
        "longitude": "-0.1278",
        "description": "Co-located facility in London HQ building",
        "physical_address": "1 Canada Square, Canary Wharf, London E14 5AB, UK",
        "tenant": TENANTS["ten-nordnet"],
        "asn": 65052,
        "time_zone": "Europe/London",
        "url": "http://mock-nautobot:8080/api/dcim/locations/loc-lon2/",
    },
]


def _location(location_id: str, name: str, location_type: str, parent: tuple[str, str] | None = None, **fields) -> dict:
    """A generated location; without coordinates unless given (*fields* override)."""
    return {
        "id": location_id,
        "name": name,
        "slug": location_id,
        "status": {"label": "Active", "value": "active"},
        "location_type": LOCATION_TYPES[location_type],
        "parent": {"id": parent[0], "name": parent[1]} if parent else None,
        "latitude": None,
        "longitude": None,
        "description": "",
        "physical_address": "",
        "tenant": TENANTS["ten-acme"],
        "asn": None,
        "time_zone": "Europe/Copenhagen",
        "url": f"http://mock-nautobot:8080/api/dcim/locations/{location_id}/",
        **fields,
    }


# EMEA › DNK › Aarhus Campus (Site) › Bygning A › Etage 2 (#158).
LOCATIONS += [
    _location("loc-emea", "EMEA", "lt-region", None),
    _location("loc-dnk", "DNK", "lt-country", ("loc-emea", "EMEA")),
    _location("loc-aar", "Aarhus Campus", "lt-site", ("loc-dnk", "DNK")),
    _location("loc-aar-bld-a", "Bygning A", "lt-building", ("loc-aar", "Aarhus Campus")),
    _location("loc-aar-bld-a-f2", "Etage 2", "lt-floor", ("loc-aar-bld-a", "Bygning A")),
]

# Devices keyed by location_id
DEVICES = {
    "loc-cph": [
        {
            "id": "dev-cph-1",
            "name": "cph-core-rt01",
            "device_type": {"model": "ASR1001-X", "manufacturer": {"name": "Cisco"}},
            "role": {"name": "Core Router"},
            "status": {"label": "Active"},
            "platform": {"name": "IOS-XE"},
            "serial": "FCZ2227B0M1",
            "tenant": TENANTS["ten-acme"],
        },
        {
            "id": "dev-cph-2",
            "name": "cph-dist-sw01",
            "device_type": {"model": "Catalyst 9300", "manufacturer": {"name": "Cisco"}},
            "role": {"name": "Distribution Switch"},
            "status": {"label": "Active"},
            "platform": {"name": "IOS-XE"},
            "serial": "FCW2101L01D",
            "tenant": TENANTS["ten-acme"],
        },
        {
            "id": "dev-cph-3",
            "name": "cph-fw01",
            "device_type": {"model": "PA-3260", "manufacturer": {"name": "Palo Alto Networks"}},
            "role": {"name": "Firewall"},
            "status": {"label": "Active"},
            "platform": {"name": "PAN-OS"},
            "serial": "013201006938",
            "tenant": TENANTS["ten-acme"],
            # The device criticality custom field (#377), read with DEVICE_CRITICALITY_FIELD=criticality.
            "custom_fields": {"criticality": "critical"},
        },
    ],
    "loc-cph2": [
        {
            "id": "dev-cph2-1",
            "name": "cph2-edge-rt01",
            "device_type": {"model": "MX204", "manufacturer": {"name": "Juniper Networks"}},
            "role": {"name": "Edge Router"},
            "status": {"label": "Active"},
            "platform": {"name": "Junos"},
            "serial": "BT0217480120",
            "tenant": TENANTS["ten-nordnet"],
        },
    ],
    "loc-sto": [
        {
            "id": "dev-sto-1",
            "name": "sto-core-rt01",
            "device_type": {"model": "ASR9001", "manufacturer": {"name": "Cisco"}},
            "role": {"name": "Core Router"},
            "status": {"label": "Active"},
            "platform": {"name": "IOS-XR"},
            "serial": "FOX1826GXXX",
            "tenant": TENANTS["ten-acme"],
        },
        # Access points (#499): Availability shows "1 network · 3 APs".
        *(
            {
                "id": f"dev-sto-ap-{n}",
                "name": f"sto-ap0{n}",
                "device_type": {"model": "MR46", "manufacturer": {"name": "Cisco Meraki"}},
                "role": {"name": "Access Point"},
                "status": {"label": "Active"},
                "platform": {"name": "Meraki"},
                "serial": f"Q2KD-AP0{n}",
                "tenant": TENANTS["ten-acme"],
            }
            for n in range(1, 4)
        ),
    ],
    "loc-ams": [
        {
            "id": "dev-ams-1",
            "name": "ams-rt01",
            "device_type": {"model": "PTX5000", "manufacturer": {"name": "Juniper Networks"}},
            "role": {"name": "Core Router"},
            "status": {"label": "Active"},
            "platform": {"name": "Junos"},
            "serial": "BUILTIN",
            "tenant": TENANTS["ten-euroix"],
        },
        {
            "id": "dev-ams-2",
            "name": "ams-rt02",
            "device_type": {"model": "PTX5000", "manufacturer": {"name": "Juniper Networks"}},
            "role": {"name": "Core Router"},
            "status": {"label": "Active"},
            "platform": {"name": "Junos"},
            "serial": "BUILTIN2",
            "tenant": TENANTS["ten-euroix"],
        },
        {
            "id": "dev-ams-3",
            "name": "ams-sw01",
            "device_type": {"model": "QFX10002-72Q", "manufacturer": {"name": "Juniper Networks"}},
            "role": {"name": "Peering Switch"},
            "status": {"label": "Active"},
            "platform": {"name": "Junos"},
            "serial": "VN2022000099",
            "tenant": TENANTS["ten-euroix"],
        },
    ],
    "loc-fra": [
        {
            "id": "dev-fra-1",
            "name": "fra-core-rt01",
            "device_type": {"model": "7750 SR-12", "manufacturer": {"name": "Nokia"}},
            "role": {"name": "Core Router"},
            "status": {"label": "Active"},
            "platform": {"name": "SR OS"},
            "serial": "NS1234567890",
            "tenant": TENANTS["ten-dcgmbh"],
        },
        {
            "id": "dev-fra-2",
            "name": "fra-sw01",
            "device_type": {"model": "Nexus 9336C-FX2", "manufacturer": {"name": "Cisco"}},
            "role": {"name": "ToR Switch"},
            "status": {"label": "Active"},
            "platform": {"name": "NX-OS"},
            "serial": "FDO2220000X",
            "tenant": TENANTS["ten-dcgmbh"],
        },
    ],
    "loc-par": [
        {
            "id": "dev-par-1",
            "name": "par-rt01",
            "device_type": {"model": "ASR1001-X", "manufacturer": {"name": "Cisco"}},
            "role": {"name": "Edge Router"},
            "status": {"label": "Active"},
            "platform": {"name": "IOS-XE"},
            "serial": "FCZ1955C5K5",
            "tenant": TENANTS["ten-acme"],
        },
        # A stack of four (#400): only the master has a name and a primary IP.
        *(
            {
                "id": f"dev-par-stk-{position}",
                "name": "par-stack01" if position == 1 else None,
                # As Nautobot shows a nameless member: its chassis, place and UUID.
                "display": "par-stack01" if position == 1 else f"par-stack01:{position} (dev-par-stk-{position})",
                "device_type": {"model": "C9300-48P", "manufacturer": {"name": "Cisco"}},
                "role": {"name": "Access Switch"},
                "status": {"label": "Active"},
                "platform": {"name": "IOS-XE"},
                "serial": f"FOC2401STK{position}",
                "tenant": TENANTS["ten-acme"],
                "virtual_chassis": {
                    "id": "vc-par-1",
                    "name": "par-stack01",
                    "display": "par-stack01",
                    "master": {"id": "dev-par-stk-1"},
                },
                "vc_position": position,
            }
            for position in range(1, 5)
        ),
    ],
    "loc-lon2": [
        {
            "id": "dev-lon2-1",
            "name": "lon2-edge-rt01",
            "device_type": {"model": "MX204", "manufacturer": {"name": "Juniper Networks"}},
            "role": {"name": "Edge Router"},
            "status": {"label": "Active"},
            "platform": {"name": "Junos"},
            "serial": "BT0217480999",
            "tenant": TENANTS["ten-nordnet"],
        },
    ],
    "loc-lon": [
        {
            "id": "dev-lon-1",
            "name": "lon-core-rt01",
            "device_type": {"model": "ASR9006", "manufacturer": {"name": "Cisco"}},
            "role": {"name": "Core Router"},
            "status": {"label": "Active"},
            "platform": {"name": "IOS-XR"},
            "serial": "FOX2143G0XX",
            "tenant": TENANTS["ten-acme"],
        },
        {
            "id": "dev-lon-2",
            "name": "lon-fw01",
            "device_type": {"model": "PA-5250", "manufacturer": {"name": "Palo Alto Networks"}},
            "role": {"name": "Firewall"},
            "status": {"label": "Active"},
            "platform": {"name": "PAN-OS"},
            "serial": "015351000000",
            "tenant": TENANTS["ten-acme"],
        },
        {
            "id": "dev-lon-3",
            "name": "lon-lb01",
            "device_type": {"model": "BIG-IP i5800", "manufacturer": {"name": "F5"}},
            "role": {"name": "Load Balancer"},
            "status": {"label": "Active"},
            "platform": None,
            "serial": "f5-abcd-1234",
            "tenant": TENANTS["ten-acme"],
        },
        {
            "id": "dev-lon-4",
            "name": "lon-acc-sw01",
            "device_type": {"model": "Catalyst 9200", "manufacturer": {"name": "Cisco"}},
            "role": {"name": "Access Switch"},
            "status": {"label": "Offline"},
            "platform": {"name": "IOS-XE"},
            "serial": "FCW2301A00Z",
            "tenant": TENANTS["ten-acme"],
            # The device criticality custom field (#377), read with DEVICE_CRITICALITY_FIELD=criticality.
            "custom_fields": {"criticality": "low"},
        },
        {
            "id": "dev-lon-5",
            "name": "lon-acc-sw02",
            "device_type": {"model": "Catalyst 9200", "manufacturer": {"name": "Cisco"}},
            "role": {"name": "Access Switch"},
            "status": {"label": "Offline"},
            "platform": {"name": "IOS-XE"},
            "serial": "FCW2301A00Y",
            "tenant": TENANTS["ten-acme"],
            # The device criticality custom field (#377), read with DEVICE_CRITICALITY_FIELD=criticality.
            "custom_fields": {"criticality": "low"},
        },
        {
            "id": "dev-lon-6",
            "name": "lon-vpn-gw01",
            "device_type": {"model": "ISR4431", "manufacturer": {"name": "Cisco"}},
            "role": {"name": "VPN Gateway"},
            "status": {"label": "Active"},
            "platform": {"name": "IOS-XE"},
            "serial": "FGL2228ABCD",
            "tenant": TENANTS["ten-acme"],
        },
        {
            "id": "dev-lon-7",
            "name": "lon-oob-sw01",
            "device_type": {"model": "SG350-28", "manufacturer": {"name": "Cisco"}},
            "role": {"name": "Out-of-Band Switch"},
            "status": {"label": "Active"},
            "platform": {"name": "IOS"},
            "serial": "PSZ2315A0ZZ",
            "tenant": TENANTS["ten-acme"],
        },
        {
            "id": "dev-lon-8",
            "name": "lon-mon01",
            "device_type": {"model": "UCS C220 M5", "manufacturer": {"name": "Cisco"}},
            "role": {"name": "Monitoring Server"},
            "status": {"label": "Active"},
            "platform": {"name": "Linux"},
            "serial": "FCH2219V0A1",
            "tenant": TENANTS["ten-acme"],
        },
    ],
}

# Real Nautobot device responses carry a location reference and a primary IP;
# the inventory sync and alert board rely on both.  Attach them here so the
# seed data above stays readable.  lon-oob-sw01 is deliberately left without a
# primary IP to demonstrate that the alert board skips such devices.
# Aarhus Campus: one device on the site itself, one on a floor two levels down (down).
DEVICES["loc-aar"] = [
    {
        "id": "dev-aar-1",
        "name": "aar-core-rt01",
        "device_type": {"model": "ASR1001-X", "manufacturer": {"name": "Cisco"}},
        "role": {"name": "Core Router"},
        "status": {"label": "Active"},
        "platform": {"name": "IOS-XE"},
        "serial": "FCZ2227AAR1",
        "tenant": TENANTS["ten-acme"],
    }
]
DEVICES["loc-aar-bld-a-f2"] = [
    {
        "id": "dev-aar-2",
        "name": "aar-acc-sw01",
        "device_type": {"model": "Catalyst 9200", "manufacturer": {"name": "Cisco"}},
        "role": {"name": "Access Switch"},
        "status": {"label": "Offline"},
        "platform": {"name": "IOS-XE"},
        "serial": "JAE2227AAR2",
        "tenant": TENANTS["ten-acme"],
    }
]

DEVICES_WITHOUT_PRIMARY_IP = {"dev-lon-7", "dev-par-stk-2", "dev-par-stk-3", "dev-par-stk-4"}


# Nautobot returns last_updated on every object; the incremental sync and the
# data contract (docs/data-contract.md) rely on it.
MOCK_LAST_UPDATED = "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Scale mode: MOCK_SCALE=<sites> adds that many generated sites, for screenshots
# and checks at a NOC's real load (1,200 sites, ~25 in alarm, one with 30+
# devices down). The same sites and devices every start (the outages begin when
# the mock starts); off by default.
# ---------------------------------------------------------------------------
SCALE_CITIES = [
    ("Copenhagen", "DK", 55.68, 12.57),
    ("Aarhus", "DK", 56.16, 10.20),
    ("Odense", "DK", 55.40, 10.39),
    ("Stockholm", "SE", 59.33, 18.07),
    ("Gothenburg", "SE", 57.71, 11.97),
    ("Malmö", "SE", 55.60, 13.00),
    ("Oslo", "NO", 59.91, 10.75),
    ("Bergen", "NO", 60.39, 5.32),
    ("Helsinki", "FI", 60.17, 24.94),
    ("Hamburg", "DE", 53.55, 9.99),
    ("Berlin", "DE", 52.52, 13.40),
    ("Munich", "DE", 48.14, 11.58),
    ("Frankfurt", "DE", 50.11, 8.68),
    ("Cologne", "DE", 50.94, 6.96),
    ("Amsterdam", "NL", 52.37, 4.90),
    ("Rotterdam", "NL", 51.92, 4.48),
    ("Brussels", "BE", 50.85, 4.35),
    ("Paris", "FR", 48.86, 2.35),
    ("Lyon", "FR", 45.76, 4.84),
    ("Marseille", "FR", 43.30, 5.37),
    ("London", "GB", 51.51, -0.13),
    ("Manchester", "GB", 53.48, -2.24),
    ("Dublin", "IE", 53.35, -6.26),
    ("Madrid", "ES", 40.42, -3.70),
    ("Barcelona", "ES", 41.39, 2.17),
    ("Lisbon", "PT", 38.72, -9.14),
    ("Milan", "IT", 45.46, 9.19),
    ("Rome", "IT", 41.90, 12.50),
    ("Zurich", "CH", 47.38, 8.54),
    ("Vienna", "AT", 48.21, 16.37),
    ("Prague", "CZ", 50.08, 14.44),
    ("Warsaw", "PL", 52.23, 21.01),
    ("Budapest", "HU", 47.50, 19.04),
]
SCALE_CUSTOMERS = [
    "Northwind Bank",
    "Bluefin Retail",
    "Helix Health",
    "Polar Logistics",
    "Fjord Energy",
    "Kestrel Air",
    "Atlas Insurance",
    "Granite Telecom",
    "Lumen Pharma",
    "Orbit Media",
    "Pioneer Foods",
    "Quartz Labs",
    "Redwood Hotels",
    "Summit Schools",
    "Tidal Ports",
    "Vantage Cars",
]
# (sites, down devices of 8, a core device down, access points down): Critical,
# Medium, Low.  A site with access points down has 6 of them (#503), whatever
# its kind, so a Medium one stays above 25 % down; the day's devices down stay
# within 50-100.
SCALE_ALARMS = [(6, 1, True, 0), (8, 3, False, 1), (12, 1, False, 0)]


def _scale_device(site_id, index, name, role, model, status, tenant, down_minutes=None):
    device = {
        "id": f"dev-{site_id}-{index}",
        "name": name,
        "device_type": {"model": model[1], "manufacturer": {"name": model[0]}},
        "role": {"name": role},
        "status": {"label": status},
        "platform": {"name": "IOS-XE" if model[0] == "Cisco" else "Junos"},
        "serial": f"SC{site_id[-4:]}{index:02d}",
        "tenant": tenant,
    }
    if down_minutes is not None:
        # The outage began down_minutes ago: Nautobot's last change.
        changed = datetime.now(UTC) - timedelta(minutes=down_minutes)
        device["last_updated"] = changed.strftime("%Y-%m-%dT%H:%M:%SZ")
    return device


def _scale_site_devices(site_id, prefix, switches, down, core, core_model, since, tenant_of, aps=0, aps_down=0):
    """A core router, *switches* access switches and *aps* access points (#503); the core (when
    *core*), the first *down* switches and the first *aps_down* access points down."""
    devices = [
        _scale_device(
            site_id,
            0,
            f"{prefix}-core-rt01",
            "Core Router",
            core_model,
            "Offline" if core else "Active",
            tenant_of(0),
            since if core else None,
        )
    ]
    for i in range(1, switches + 1):
        is_down = i <= down
        devices.append(
            _scale_device(
                site_id,
                i,
                f"{prefix}-acc-sw{i:02d}",
                "Access Switch",
                ("Cisco", "Catalyst 9200"),
                "Offline" if is_down else "Active",
                tenant_of(i),
                since if is_down else None,
            )
        )
    for i in range(1, aps + 1):
        is_down = i <= aps_down
        devices.append(
            _scale_device(
                site_id,
                switches + i,
                f"{prefix}-ap{i:02d}",
                "Access Point",
                ("Cisco", "Catalyst 9136"),
                "Offline" if is_down else "Active",
                tenant_of(switches + i),
                since if is_down else None,
            )
        )
    return devices


# What Nautobot's tenant description says (#497: found by it, shown on the status page).
SCALE_DESCRIPTIONS = {
    "Northwind Bank": "Retail banking, 24/7 SLA",
    "Helix Health": "Hospitals and clinics, critical care",
    "Lumen Pharma": "Pharmaceutical production",
    "Tidal Ports": "Container terminals",
}


def _add_scale_sites(count: int) -> None:
    rnd = random.Random(1200)
    # Its own generator: the access points leave the sites and alarms above as they were.
    ap_rnd = random.Random(503)
    customers = []
    for name in SCALE_CUSTOMERS:
        slug = name.lower().replace(" ", "-")
        description = SCALE_DESCRIPTIONS.get(name, "")
        tenant = {"id": f"ten-{slug}", "name": name, "slug": slug, "description": description, "tenant_group": None}
        TENANTS[tenant["id"]] = tenant
        customers.append(tenant)
    alarms = [(down, core, aps_down) for sites, down, core, aps_down in SCALE_ALARMS for _ in range(sites)][: count - 1]
    alarm_at = dict(zip(sorted(rnd.sample(range(1, count), len(alarms))), alarms, strict=True))
    for n in range(count):
        city, country, lat, lon = SCALE_CITIES[n % len(SCALE_CITIES)]
        kind = rnd.choices(["lt-office", "lt-pop", "lt-dc"], weights=[70, 25, 5])[0]
        site_id = f"loc-sc{n:04d}"
        tenant = customers[n % len(customers)]
        label = LOCATION_TYPES[kind]["name"].replace("Data Center", "DC")
        LOCATIONS.append(
            _location(
                site_id,
                f"{city} {label} {n // len(SCALE_CITIES) + 1:02d}",
                kind,
                latitude=f"{lat + rnd.uniform(-0.6, 0.6):.4f}",
                longitude=f"{lon + rnd.uniform(-0.9, 0.9):.4f}",
                physical_address=f"{rnd.randint(1, 120)} Harbour Street, {city}, {country}",
                tenant=tenant,
            )
        )
        prefix = f"{city[:3].lower()}{n:04d}"
        if n == 0:
            # A core outage's burst: the core and 33 of 39 switches down, devices of eight customers.
            DEVICES[site_id] = _scale_site_devices(
                site_id,
                prefix,
                39,
                33,
                True,
                ("Cisco", "ASR 9006"),
                37,
                lambda i, t=tenant: t if i == 0 else customers[i % 8],
            )
            continue
        down, core, aps_down = alarm_at.get(n, (0, False, 0))
        since = rnd.choice([4, 12, 35, 80, 190, 420, 1500])
        # Access points (#503): offices 6-16, PoPs 2, DCs none; 6 with some down
        # (SCALE_ALARMS).  One draw for every site, used or not, so the counts
        # are the same on every start whatever the kinds.
        drawn = ap_rnd.randint(6, 16)
        aps = 6 if aps_down else {"lt-office": drawn, "lt-pop": 2}.get(kind, 0)
        DEVICES[site_id] = _scale_site_devices(
            site_id, prefix, 7, down, core, ("Juniper", "MX204"), since, lambda i, t=tenant: t, aps, aps_down
        )


_scale = os.environ.get("MOCK_SCALE", "").replace(",", "").replace("_", "").strip() or "0"
if not _scale.isdigit():
    raise SystemExit(f"MOCK_SCALE must be a number of sites, e.g. 1200 (got {_scale!r})")
SCALE_SITES = int(_scale)
if SCALE_SITES > 0:
    _add_scale_sites(SCALE_SITES)


def _attach_location_and_primary_ip() -> None:
    for location in LOCATIONS:
        location.setdefault("last_updated", MOCK_LAST_UPDATED)
    for loc_index, (location_id, devices) in enumerate(DEVICES.items(), start=1):
        for dev_index, device in enumerate(devices, start=1):
            device.setdefault("last_updated", MOCK_LAST_UPDATED)
            device["location"] = {
                "id": location_id,
                "url": f"http://mock-nautobot:8080/api/dcim/locations/{location_id}/",
            }
            if device["id"] in DEVICES_WITHOUT_PRIMARY_IP:
                device["primary_ip4"] = None
                continue
            # 10.<site/256>.<site%256>.<device>: valid past 255 sites (scale mode).
            host = f"10.{loc_index // 256}.{loc_index % 256}.{dev_index}"
            device["primary_ip4"] = {
                "id": f"ip-{device['id']}",
                "address": f"{host}/32",
                "host": host,
            }


_attach_location_and_primary_ip()
ALL_DEVICES = [d for devs in DEVICES.values() for d in devs]

# ASNs keyed by location_id
ASNS = {
    "loc-cph": [{"asn": 65001, "description": "Acme Corp primary ASN", "tenant": TENANTS["ten-acme"]}],
    "loc-cph2": [{"asn": 65002, "description": "Nordic Net Denmark ASN", "tenant": TENANTS["ten-nordnet"]}],
    "loc-sto": [{"asn": 65010, "description": "Acme Corp Sweden ASN", "tenant": TENANTS["ten-acme"]}],
    "loc-ams": [
        {"asn": 65020, "description": "EuroIX AMS-IX ASN", "tenant": TENANTS["ten-euroix"]},
        {"asn": 65021, "description": "EuroIX transit ASN", "tenant": TENANTS["ten-euroix"]},
    ],
    "loc-fra": [{"asn": 65030, "description": "DataCenter GmbH primary ASN", "tenant": TENANTS["ten-dcgmbh"]}],
    "loc-par": [{"asn": 65040, "description": "Acme Corp France ASN", "tenant": TENANTS["ten-acme"]}],
    "loc-lon": [
        {"asn": 65050, "description": "Acme Corp UK primary ASN", "tenant": TENANTS["ten-acme"]},
        {"asn": 65051, "description": "Acme Corp UK backup ASN", "tenant": TENANTS["ten-acme"]},
    ],
    "loc-lon2": [{"asn": 65052, "description": "Nordic Net UK ASN", "tenant": TENANTS["ten-nordnet"]}],
}


# Circuit terminations keyed by location_id (#235).  Copenhagen has two.
CIRCUIT_PROVIDERS = {
    "prov-telia": {"id": "prov-telia", "name": "Telia Carrier"},
    "prov-gtt": {"id": "prov-gtt", "name": "GTT"},
}
CIRCUIT_TYPES = {
    "ct-transit": {"id": "ct-transit", "name": "Internet Transit"},
    "ct-mpls": {"id": "ct-mpls", "name": "MPLS"},
}


def _circuit(circuit_id, cid, provider, circuit_type, status, commit_rate):
    return {
        "id": circuit_id,
        "cid": cid,
        "display": cid,
        "provider": CIRCUIT_PROVIDERS[provider],
        "circuit_type": CIRCUIT_TYPES[circuit_type],
        "status": {"name": status},
        "commit_rate": commit_rate,
        "tenant": TENANTS["ten-acme"],
        "description": "",
    }


CIRCUIT_TERMINATIONS = {
    "loc-cph": [
        {
            "id": "ct-cph-1",
            "circuit": _circuit("cir-cph-1", "TEL-CPH-0001", "prov-telia", "ct-transit", "Active", 10_000_000),
            "term_side": "A",
            "port_speed": 10_000_000,
            "upstream_speed": None,
            "xconnect_id": "XC-4411",
            "pp_info": "PP-02 port 7",
            "description": "Primary transit",
        },
        {
            "id": "ct-cph-2",
            "circuit": _circuit("cir-cph-2", "GTT-MPLS-7781", "prov-gtt", "ct-mpls", "Offline", 1_000_000),
            "term_side": "Z",
            "port_speed": 1_000_000,
            "upstream_speed": 500_000,
            "xconnect_id": "",
            "pp_info": "",
            "description": "",
        },
    ],
    "loc-ams": [
        {
            "id": "ct-ams-1",
            "circuit": _circuit("cir-ams-1", "TEL-AMS-0042", "prov-telia", "ct-transit", "Active", 100_000_000),
            "term_side": "A",
            "port_speed": 100_000_000,
            "upstream_speed": None,
            "xconnect_id": "",
            "pp_info": "",
            "description": "",
        },
    ],
}


# Circuits whose description is the site's address, without a termination
# there (#383): the site panel lists them "By address".
ADDRESS_CIRCUITS = [
    # Same street, number and postcode; the town written differently.
    _circuit("cir-cph-addr", "TDC-FIB-1001", "prov-telia", "ct-mpls", "Active", 1_000_000),
    _circuit("cir-sto-addr", "TEL-STO-2001", "prov-telia", "ct-transit", "Active", 500_000),
    _circuit("cir-fra-addr", "GTT-FRA-3001", "prov-gtt", "ct-mpls", "Planned", None),
    # One address, two sites (London HQ and London Colo).
    _circuit("cir-lon-addr", "GTT-LON-4001", "prov-gtt", "ct-transit", "Active", 10_000_000),
    # Not just the address: not matched.
    _circuit("cir-par-addr", "TEL-PAR-5001", "prov-telia", "ct-transit", "Active", 1_000_000),
]
for _circ, _description in zip(
    ADDRESS_CIRCUITS,
    (
        "Vermlandsgade 51, 2300 København S",
        "Stureplan 4, 11435 Stockholm",
        "Hanauer Landstrasse 298, 60314 Frankfurt am Main",
        "1 Canada Square, Canary Wharf, London E14 5AB, UK",
        "Fibre 1G – Rue de la Paix 10, 75002 Paris",
    ),
    strict=True,
):
    _circ["description"] = _description
# Terminated at Copenhagen DC and its address too: listed once, as terminated.
CIRCUIT_TERMINATIONS["loc-cph"][0]["circuit"]["description"] = "Vermlandsgade 51, 2300 Copenhagen"
# Scale mode: every third generated site has a circuit by address (#383);
# every 30th says more than the address, so it matches no site (#385).
ADDRESS_CIRCUITS += [
    {
        **_circuit(f"cir-{loc['id']}", f"SC-{index:05d}", "prov-gtt", "ct-mpls", "Active", 1_000_000),
        # The site's customer's (#511): it sees it on its status page.
        "tenant": loc["tenant"],
        "description": f"Fibre – {loc['physical_address']}" if index % 30 == 0 else loc["physical_address"],
    }
    for index, loc in enumerate(loc for loc in LOCATIONS if loc["id"].startswith("loc-sc"))
    if index % 3 == 0
]


# Location <-> Tenant relationship: London HQ hosts three customers (#238).
RELATIONSHIPS = [
    {
        "id": "rel-site-customers",
        "key": "site_customers",
        "label": "Site customers",
        "type": "many-to-many",
        "source_type": "dcim.location",
        "destination_type": "tenancy.tenant",
    },
    {
        "id": "rel-other",
        "key": "device_backup",
        "label": "Backup device",
        "type": "one-to-one",
        "source_type": "dcim.device",
        "destination_type": "dcim.device",
    },
]
RELATIONSHIP_ASSOCIATIONS = [
    {
        "id": f"ra-{index}",
        "relationship": {"id": "rel-site-customers"},
        "source_type": "dcim.location",
        "source_id": location_id,
        "destination_type": "tenancy.tenant",
        "destination_id": tenant_id,
    }
    for index, (location_id, tenant_id) in enumerate(
        [("loc-lon", "ten-nordnet"), ("loc-lon", "ten-euroix"), ("loc-lon", "ten-dcgmbh"), ("loc-ams", "ten-acme")]
    )
]


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------
def _check_auth(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Token ") or len(auth) <= 6:
        raise HTTPException(status_code=403, detail="Invalid or missing API token")


# ---------------------------------------------------------------------------
# Paginate helper
# ---------------------------------------------------------------------------
def _paginate(request: Request, items: list) -> dict:
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    limit = int(request.query_params.get("limit", 200))
    offset = int(request.query_params.get("offset", 0))
    page = items[offset : offset + limit]
    next_url = None
    if offset + limit < len(items):
        parsed = urlparse(str(request.url))
        params = parse_qs(parsed.query, keep_blank_values=True)
        params["offset"] = [str(offset + limit)]
        next_url = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))
    return {
        "count": len(items),
        "next": next_url,
        "previous": None,
        "results": page,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/dcim/locations/")
def locations(request: Request):
    _check_auth(request)
    return _paginate(request, LOCATIONS)


@app.get("/api/dcim/location-types/")
def location_types(request: Request):
    _check_auth(request)
    return _paginate(request, list(LOCATION_TYPES.values()))


@app.get("/api/tenancy/tenants/")
def tenants(request: Request):
    _check_auth(request)
    return _paginate(request, list(TENANTS.values()))


@app.get("/api/tenancy/tenant-groups/")
def tenant_groups(request: Request):
    _check_auth(request)
    return _paginate(request, list(TENANT_GROUPS.values()))


@app.get("/api/extras/statuses/")
def statuses(request: Request):
    _check_auth(request)
    return _paginate(request, list(STATUSES.values()))


@app.get("/api/dcim/virtual-chassis/")
def virtual_chassis(request: Request):
    """The stacks (#400); none changes while the mock runs."""
    _check_auth(request)
    if request.query_params.get("last_updated__gte"):
        return _paginate(request, [])
    return _paginate(request, [{"id": "vc-par-1", "name": "par-stack01", "master": {"id": "dev-par-stk-1"}}])


@app.get("/api/dcim/devices/")
def devices(request: Request):
    _check_auth(request)
    # Accept both "location" (Nautobot 3.x) and "location_id" (legacy) filter params.
    location_id = request.query_params.get("location") or request.query_params.get("location_id")
    if location_id:
        items = DEVICES.get(location_id, [])
    else:
        items = ALL_DEVICES
    return _paginate(request, items)


@app.get("/api/ipam/asns/")
def asns(request: Request):
    _check_auth(request)
    location_id = request.query_params.get("location_id")
    if location_id:
        items = ASNS.get(location_id, [])
    else:
        items = [a for asn_list in ASNS.values() for a in asn_list]
    return _paginate(request, items)


@app.get("/api/circuits/circuit-terminations/")
def circuit_terminations(request: Request):
    _check_auth(request)
    location_id = request.query_params.get("location")
    if location_id:
        items = CIRCUIT_TERMINATIONS.get(location_id, [])
    else:
        items = [t for terms in CIRCUIT_TERMINATIONS.values() for t in terms]
    return _paginate(request, items)


@app.get("/api/circuits/circuits/")
def circuits(request: Request):
    _check_auth(request)
    terminated = {}
    for location_id, terms in CIRCUIT_TERMINATIONS.items():
        for term in terms:
            side = f"circuit_termination_{term['term_side'].lower()}"
            circuit = terminated.setdefault(term["circuit"]["id"], {**term["circuit"]})
            circuit[side] = {"id": term["id"], "term_side": term["term_side"], "location": {"id": location_id}}
    return _paginate(request, [*terminated.values(), *ADDRESS_CIRCUITS])


@app.get("/api/extras/relationships/")
def relationships(request: Request):
    _check_auth(request)
    return _paginate(request, RELATIONSHIPS)


@app.get("/api/extras/relationship-associations/")
def relationship_associations(request: Request):
    _check_auth(request)
    relationship = request.query_params.get("relationship")
    items = [a for a in RELATIONSHIP_ASSOCIATIONS if not relationship or a["relationship"]["id"] == relationship]
    return _paginate(request, items)


# ---------------------------------------------------------------------------
# Cisco Catalyst Center (#508): the demo's access points as one instance
# lists them, for WIRELESS_CONTROLLERS_FILE (demo/wireless.yml).  sto-ap02 and
# one in 200 of the scale demo's are unreachable; one AP isn't in Nautobot.
# ---------------------------------------------------------------------------
CATALYST_TOKEN = "mock-catalyst-center-token"


def _access_points(controller: str) -> list[dict]:
    """The demo's access points: the nine sites' and the even scale sites' in
    Catalyst Center, the odd scale sites' in Aruba New Central (#508)."""
    aps = [d for d in ALL_DEVICES if (d.get("role") or {}).get("name") == "Access Point"]

    def odd_scale_site(device: dict) -> bool:
        return device["id"].startswith("dev-loc-sc") and int(device["id"][len("dev-loc-sc") :].split("-")[0]) % 2 == 1

    return [d for d in aps if odd_scale_site(d) == (controller == "aruba_central")]


def _catalyst_aps() -> list[dict]:
    aps = _access_points("catalyst_center")
    listed = []
    for index, device in enumerate(aps):
        down = device["name"] == "sto-ap02" or (device["id"].startswith("dev-loc-sc") and index % 200 == 7)
        listed.append(
            {
                "id": f"cc-{device['id']}",
                "hostname": device["name"],
                "serialNumber": device.get("serial") or "",
                "macAddress": f"00:11:22:{index // 65536 % 256:02x}:{index // 256 % 256:02x}:{index % 256:02x}",
                "family": "Unified AP",
                "reachabilityStatus": "Unreachable" if down else "Reachable",
            }
        )
    listed.append(
        {
            "id": "cc-unknown",
            "hostname": "lab-ap99",
            "serialNumber": "FGL-NOT-IN-NAUTOBOT",
            "macAddress": "00:11:22:ff:ff:ff",
            "family": "Unified AP",
            "reachabilityStatus": "Reachable",
        }
    )
    return listed


# HPE Aruba Networking New Central (#508): the odd scale sites' access points,
# one in 200 offline, in pages with a "next" cursor.  Its sign-in is HPE's
# OAuth client credentials (token_url in demo/wireless.yml points here).
CENTRAL_TOKEN = "mock-aruba-central-token"


@app.post("/as/token.oauth2")
async def central_token(request: Request):
    form = await request.form()
    if form.get("grant_type") != "client_credentials" or not form.get("client_id") or not form.get("client_secret"):
        raise HTTPException(status_code=401, detail="invalid_client")
    return {"access_token": CENTRAL_TOKEN, "token_type": "Bearer", "expires_in": 7200}


@app.get("/network-monitoring/v1/aps")
def central_aps(request: Request):
    if request.headers.get("Authorization") != f"Bearer {CENTRAL_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    limit = min(1000, int(request.query_params.get("limit", 20)))
    page = int(request.query_params.get("next") or 1)
    aps = _access_points("aruba_central")
    items = [
        {
            "id": f"ac-{device['id']}",
            "type": "ACCESS_POINT",
            "serialNumber": device.get("serial") or "",
            "macAddress": f"20:4c:03:{index // 65536 % 256:02x}:{index // 256 % 256:02x}:{index % 256:02x}",
            "deviceName": device["name"],
            "siteId": device["location"]["id"],
            "status": "OFFLINE" if index % 200 == 11 else "ONLINE",
        }
        for index, device in enumerate(aps)
    ][(page - 1) * limit : page * limit]
    more = page * limit < len(aps)
    return {"items": items, "count": len(items), "total": len(aps), "next": str(page + 1) if more else None}


@app.post("/dna/system/api/v1/auth/token")
def catalyst_token(request: Request):
    if not request.headers.get("Authorization", "").startswith("Basic "):
        raise HTTPException(status_code=401, detail="Basic auth required")
    return {"Token": CATALYST_TOKEN}


@app.get("/dna/intent/api/v1/network-device")
def catalyst_devices(request: Request):
    if request.headers.get("X-Auth-Token") != CATALYST_TOKEN:
        raise HTTPException(status_code=401, detail="Token expired or invalid")
    if request.query_params.get("family") != "Unified AP":
        return {"response": [], "version": "1.0"}
    offset = max(1, int(request.query_params.get("offset", 1)))
    limit = min(500, int(request.query_params.get("limit", 500)))
    return {"response": _catalyst_aps()[offset - 1 : offset - 1 + limit], "version": "1.0"}


@app.get("/api/")
def api_root(request: Request):
    return {"message": "Mock Nautobot API"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")
