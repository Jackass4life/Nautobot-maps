"""
mock_nautobot.py – Lightweight mock of the Nautobot REST API.

Serves realistic seed data (European data-centre locations, network devices,
ASNs and tenants) so the nautobot-maps application can be demoed and
integration-tested without a live Nautobot instance.

Endpoints implemented:
  GET /api/dcim/locations/
  GET /api/dcim/devices/        (filterable by ?location_id=)
  GET /api/ipam/asns/           (filterable by ?location_id=)

Token auth: any request must carry  Authorization: Token <any non-empty value>
"""

from flask import Flask, jsonify, request, abort

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

TENANTS = {
    "ten-acme":    {"id": "ten-acme",    "name": "Acme Corp",       "slug": "acme-corp"},
    "ten-nordnet": {"id": "ten-nordnet", "name": "Nordic Net",      "slug": "nordic-net"},
    "ten-euroix":  {"id": "ten-euroix",  "name": "EuroIX",          "slug": "euroix"},
    "ten-dcgmbh":  {"id": "ten-dcgmbh",  "name": "DataCenter GmbH", "slug": "datacenter-gmbh"},
}

LOCATION_TYPES = {
    "lt-dc":     {"id": "lt-dc",     "name": "Data Center"},
    "lt-pop":    {"id": "lt-pop",    "name": "PoP"},
    "lt-office": {"id": "lt-office", "name": "Office"},
    "lt-ix":     {"id": "lt-ix",     "name": "Internet Exchange"},
}

STATUSES = {
    "status-active":  {"id": "status-active",  "name": "Active",  "label": "Active"},
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
    },
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

# ASNs keyed by location_id
ASNS = {
    "loc-cph":  [{"asn": 65001, "description": "Acme Corp primary ASN", "tenant": TENANTS["ten-acme"]}],
    "loc-cph2": [{"asn": 65002, "description": "Nordic Net Denmark ASN", "tenant": TENANTS["ten-nordnet"]}],
    "loc-sto":  [{"asn": 65010, "description": "Acme Corp Sweden ASN", "tenant": TENANTS["ten-acme"]}],
    "loc-ams":  [
        {"asn": 65020, "description": "EuroIX AMS-IX ASN", "tenant": TENANTS["ten-euroix"]},
        {"asn": 65021, "description": "EuroIX transit ASN", "tenant": TENANTS["ten-euroix"]},
    ],
    "loc-fra":  [{"asn": 65030, "description": "DataCenter GmbH primary ASN", "tenant": TENANTS["ten-dcgmbh"]}],
    "loc-par":  [{"asn": 65040, "description": "Acme Corp France ASN", "tenant": TENANTS["ten-acme"]}],
    "loc-lon":  [
        {"asn": 65050, "description": "Acme Corp UK primary ASN", "tenant": TENANTS["ten-acme"]},
        {"asn": 65051, "description": "Acme Corp UK backup ASN",  "tenant": TENANTS["ten-acme"]},
    ],
}


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------
def _check_auth():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Token ") or len(auth) <= 6:
        abort(403, description="Invalid or missing API token")


# ---------------------------------------------------------------------------
# Paginate helper
# ---------------------------------------------------------------------------
def _paginate(items: list) -> dict:
    from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

    limit = int(request.args.get("limit", 200))
    offset = int(request.args.get("offset", 0))
    page = items[offset: offset + limit]
    next_url = None
    if offset + limit < len(items):
        parsed = urlparse(request.url)
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

@app.route("/api/dcim/locations/")
def locations():
    _check_auth()
    return jsonify(_paginate(LOCATIONS))


@app.route("/api/dcim/location-types/")
def location_types():
    _check_auth()
    return jsonify(_paginate(list(LOCATION_TYPES.values())))


@app.route("/api/tenancy/tenants/")
def tenants():
    _check_auth()
    return jsonify(_paginate(list(TENANTS.values())))


@app.route("/api/extras/statuses/")
def statuses():
    _check_auth()
    return jsonify(_paginate(list(STATUSES.values())))


@app.route("/api/dcim/devices/")
def devices():
    _check_auth()
    # Accept both "location" (Nautobot 3.x) and "location_id" (legacy) filter params.
    location_id = request.args.get("location") or request.args.get("location_id")
    if location_id:
        items = DEVICES.get(location_id, [])
    else:
        items = [d for devs in DEVICES.values() for d in devs]
    return jsonify(_paginate(items))


@app.route("/api/ipam/asns/")
def asns():
    _check_auth()
    location_id = request.args.get("location_id")
    if location_id:
        items = ASNS.get(location_id, [])
    else:
        items = [a for asn_list in ASNS.values() for a in asn_list]
    return jsonify(_paginate(items))


@app.route("/api/")
def api_root():
    return jsonify({"message": "Mock Nautobot API"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
