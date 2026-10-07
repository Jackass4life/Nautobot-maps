# Demo – Nautobot Maps with Mock Nautobot

This `docker-compose.yml` spins up a **complete demo environment** in two containers:

| Service | Description | Port |
|---|---|---|
| `mock-nautobot` | Lightweight Flask app that mimics the Nautobot REST API with pre-seeded European locations, devices, and ASNs | 8080 |
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
| Paris PoP | 🇫🇷 Paris | Active | Acme Corp | AS65040 | 1 (Cisco ASR) |
| London HQ | 🇬🇧 London | Active | Acme Corp | AS65050, AS65051 | 8 (Cisco ASR9006, Palo Alto, F5, …) – 2 access switches **Offline** |
| London Colo | 🇬🇧 London | Active | Nordic Net | AS65052 | 1 (Juniper MX204) – same coordinates as London HQ |

Every device has a primary IPv4 address except `lon-oob-sw01`, which is left
without one to show that the alert board ignores devices without a primary IP.

## Demo scenarios to try

### Scenario 1 – Browse all locations
All 9 locations appear as colour-coded pins:
- **Green** = Active
- **Orange** = Planned

Click any pin to see its location details (tenant, ASN, address), then watch the popup expand with the full device list.

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
> **Copy** on London HQ, and **Wall view** in the top bar.

### Scenario 6 – Tenant descriptions
On the alert board, London HQ has four tenants; the row shows the first three
and "+1 more". Hover over the **(i)** after *Acme Corp* or *DataCenter GmbH* to
read the tenant's description from Nautobot; *EuroIX* has none, so it has no
(i). On the map, click Copenhagen DC: its tenant has the same (i) in the site
panel.

## Stopping the demo

```bash
docker compose -f demo/docker-compose.yml down
```
