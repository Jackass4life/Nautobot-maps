# Demo – Nautobot Maps with Mock Nautobot

This `docker-compose.yml` spins up a **complete demo environment** in two containers:

| Service | Description | Port |
|---|---|---|
| `mock-nautobot` | Lightweight FastAPI app that mimics the Nautobot REST API with pre-seeded European locations, devices, and ASNs | 8080 |
| `nautobot-maps` | The Nautobot Maps web application, pointed at the mock server | 5000 |

## Quick start

```bash
# From the repository root:
docker compose -f demo/docker-compose.yml up --build

# Open your browser:
open http://localhost:5000
```

## What's in the seed data?

9 European locations with GPS coordinates are pre-loaded:

| Location | City | Status | Tenant | ASN(s) | Devices |
|---|---|---|---|---|---|
| Copenhagen DC | 🇩🇰 Copenhagen | Active | Acme Corp | AS65001 | 3 (Cisco ASR, Catalyst, Palo Alto) |
| Copenhagen Colocation | 🇩🇰 Copenhagen | Active | Nordic Net | AS65002 | 1 (Juniper MX204) |
| Stockholm PoP | 🇸🇪 Stockholm | Active | Acme Corp | AS65010 | 1 (Cisco ASR9001) |
| Oslo Office | 🇳🇴 Oslo | Planned | Nordic Net | — | 0 |
| Amsterdam IX | 🇳🇱 Amsterdam | Active | EuroIX | AS65020, AS65021 | 3 (Juniper PTX/QFX) |
| Frankfurt DC | 🇩🇪 Frankfurt | Active | DataCenter GmbH | AS65030 | 2 (Nokia SR, Cisco Nexus) |
| Paris PoP | 🇫🇷 Paris | Active | Acme Corp | AS65040 | 5 (Cisco ASR, a stack of 4 switches: only the master has a name and an IP) |
| London HQ | 🇬🇧 London | Active | Acme Corp | AS65050, AS65051 | 8 (Cisco ASR9006, Palo Alto, F5, …) – 2 access switches **Offline** |
| London Colo | 🇬🇧 London | Active | Nordic Net | AS65052 | 1 (Juniper MX204) – same coordinates as London HQ |

Every device has a primary IPv4 address except `lon-oob-sw01`, which is left
without one to show that the alert board ignores devices without a primary IP.

## Demo scenarios to try

### Scenario 1 – Browse all locations
The 9 locations appear on the map, coloured by alert level as on the board (the
**i** in the map's corner says what each colour means). London HQ is in alarm, so it is always its
own pin; quiet sites group into numbered circles when zoomed out.

Click a pin, or a site under **1 in alarm**, to open the site panel: its address
and tenant, its state in one line, the devices that are down, and folded below
them all devices, circuits and ASNs. Copenhagen DC,
Stockholm PoP and Frankfurt DC also list a circuit **By address** (its
description is the site's address, written a little differently); London HQ and
London Colo share one, marked *address shared with 1 other site*.

### Scenario 2 – Search by address
Type `Copenhagen` in the search box and press Enter.
> The two Copenhagen locations (DC and Colocation, ~1.2 km apart) both appear
> within the 5 km radius circle.

### Scenario 3 – Search by GPS coordinates
Type `55.6761, 12.5683` (Copenhagen DC) in the search box.
> Same result as above – both Copenhagen locations are highlighted.

### Scenario 4 – Out-of-range search
Type `Berlin` or `52.52, 13.40` (Berlin).
> No Nautobot locations are within 5 km of Berlin; the sidebar shows a "no results" message.

### Scenario 5 – Alert board
Go to http://localhost:5000/alerts. The first visit starts the inventory sync;
the board fills in by itself after a few seconds.
> It opens on **Alarms**: London HQ is Medium with 2 down devices
> (`lon-acc-sw01`, `lon-acc-sw02`), and *Etage 2* (a floor in the Danish
> hierarchy) is Critical. **All sites** shows every site. Try **+ Case** and
> **Copy** on London HQ, and **Wall view** in the ☰ menu.

### Scenario 6 – Tenant descriptions
On the alert board, London HQ has four tenants; the row shows the first and
**+3** for the rest. Hover over the **(i)** after *Acme Corp* or *DataCenter GmbH* to
read the tenant's description from Nautobot; *EuroIX* has none, so it has no
(i). On the map, click Copenhagen DC: its tenant has the same (i) in the site
panel.

### Scenario 7 – A NOC's real load
`MOCK_SCALE=1200` adds 1,200 generated sites across Europe (about 19,000
devices, half of them access points) to the nine above, 27 of them in alarm
(Critical, Medium and Low; 29 on the board with London HQ and Etage 2). One is
a core outage: *Copenhagen Office 01* has 34 of its 40 devices down, across
eight tenants. The sites and devices are the same on every start; the outages
begin when the mock starts.

Sixteen customers have 75 sites each, and four of them have a description in
Nautobot (search for "Container" in a tenant filter to find *Tidal Ports*).
On Availability, click *Tidal Ports* for its status page: 3 of its 75 sites
affected, with how many network devices and access points are down at each,
and the other 72 folded into one line.

```bash
MOCK_SCALE=1200 docker compose -f demo/docker-compose.yml up --build
```

This is how the README's screenshots are made (`scripts/screenshots.py`).

### Scenario 8 – Access points from Catalyst Center and Aruba New Central
The mock also answers as a Cisco Catalyst Center (#508): it lists the access
points, with *sto-ap02* unreachable (and one in 200 at scale), plus one,
*lab-ap99*, that isn't in Nautobot. Run the app outside Docker with
`WIRELESS_CONTROLLERS_FILE=demo/wireless.yml` (the file points at the mock on
`127.0.0.1:8080`), then:

- `python -m nautobot_maps wireless-check` lists 4 access points, 1 down, 3
  matching a Nautobot device, and *lab-ap99* as not in Nautobot.
- On the board, *Stockholm PoP* is under the **Wireless** chip, not Alarms:
  Low, "1 AP down", *sto-ap02* "per Catalyst Center demo" with the address
  the controller reports. Nothing is notified, and the activity feed doesn't
  list it (#521).
- Acme Corp's status page says *Stockholm PoP* has "1 network device · 1 of 3 access
  points down" while it stays Operational.
- Start the mock with `MOCK_AP_DHCP=1` to drop the access points' primary IPs,
  as on a network where they get their address by DHCP: they stay monitored
  through the controller, and the map's site panel shows *sto-ap02*'s address
  "from Catalyst Center demo". Without the wireless file they are "Not
  monitored: no primary IP".
- Each access point is on its site's first switch (#524): Catalyst Center's
  topology says "GigabitEthernet1/0/n", Nautobot's cables "Gi1/0/n". A down
  access point's line says "Connected to … · …"; at scale, the site whose
  access points are all down says "16 of 16 behind <its switch>".
- *sto-ap04* and *sto-ap05* are being onboarded with Plug and Play (#534): not
  in the controller's inventory, only in its Plug and Play list. The map's
  site panel for *Stockholm PoP* says "Onboarding (PnP: Unclaimed)" and "PnP
  error" ("Image download failed: timeout"); the board shows nothing for them.

With `MOCK_SCALE=1200` it is an Aruba New Central too (`demo/wireless.yml`
lists both): Catalyst Center has the even scale sites' access points, New
Central the odd ones', each with one in 200 offline, so `wireless-check`
shows two instances of about 4,800 access points each. One site's access
points are all unreachable in Catalyst Center at once: on the board its row
folds them into one line, "16 access points down".

## Stopping the demo

```bash
docker compose -f demo/docker-compose.yml down
```
