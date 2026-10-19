# How Nautobot Maps works

An overview for anyone new to the app: what it is made of, how data flows through it, how it is set up, and what it can and can't do. The other pages in `docs/` go into detail; this one links to them.

## In one paragraph

Nautobot Maps reads your sites and devices from **Nautobot** (read-only), asks **LibreNMS**, **Prometheus Alertmanager** or any webhook which devices are down, and shows the result as a **map**, an **alert board** for the NOC, a **wall view** for a screen, **availability** reports, **maintenance** planning and a **status page** per tenant. It keeps its own history (outages, cases, acknowledgements, maintenance) in **PostgreSQL**, and sends **notifications** when a site's level changes. It never writes to Nautobot.

## The building blocks

| Part | What it is | Why |
|---|---|---|
| **Python 3.12 + Flask** | The web app: pages, the JSON API, webhooks, MCP. (Flask, not FastAPI or Django.) | Small, well-known, enough for this load |
| **gunicorn** | The production web server inside the container: 4 worker processes by default (`GUNICORN_WORKERS`) | Several requests at once; a stuck one can't block the rest |
| **PostgreSQL 16** | The app's own database: a copy of the inventory, alert history, cases, acks, maintenance windows, notifications queue, API tokens | History survives restarts; one source of truth for every worker |
| **Redis 7** | Shared cache of Nautobot answers and rate limits between workers | Fewer calls to Nautobot; every worker sees the same cache |
| **Leaflet** (+ markercluster) | The map in the browser, bundled in `static/vendor/` | No CDN needed at runtime |
| **OpenStreetMap** tiles and **Nominatim** address search | Map background and "search by address" (both replaceable: `MAP_TILE_URL`, `GEOCODER_URL`, or off) | Free defaults; point them at your own servers if needed |
| **Plain JavaScript (ES modules)** | The pages' code (`static/js/`), no framework, no build step | Nothing to compile; strict Content Security Policy |
| **Docker / Docker Compose** | Packages the app with PostgreSQL and Redis (`docker-compose.yml`) | One command to start |
| **nginx or another proxy** *(optional, yours)* | In front of the app for HTTPS and sign-in (SSO: oauth2-proxy, nginx with OIDC/SAML…) | The app has no login screen of its own and doesn't do TLS |

The app listens on port 5000 (in Compose, only on `127.0.0.1:5000` of the host). In production you put your proxy in front of it.

```mermaid
flowchart LR
  user[Browser / NOC screen] -->|HTTPS| proxy[nginx / SSO proxy<br/>optional, yours]
  proxy --> app[Nautobot Maps<br/>Flask + gunicorn]
  app -->|read-only API| nb[(Nautobot)]
  app -->|API| lnms[(LibreNMS<br/>optional)]
  lnms -. push alerts .-> app
  am[Alertmanager / Zabbix /<br/>any webhook] -. alerts .-> app
  app --- pg[(PostgreSQL)]
  app --- rd[(Redis)]
  app -->|Teams / email / webhook| notify[Notifications]
  ai[AI assistant] -->|MCP| app
```

## How the data flows

1. **Sync from Nautobot.** A background scheduler (one thread per worker; a PostgreSQL lock lets only one work at a time) fetches locations, devices, ASNs and circuits from Nautobot every `INVENTORY_SYNC_INTERVAL_SECONDS` (default 300 s). Syncs are incremental (only what changed); once a day a full sync removes deleted objects. The result is stored in PostgreSQL.
2. **Who is down.** A device's status comes from Nautobot and, when configured, LibreNMS (polled, and pushed within seconds by LibreNMS alert rules). Alertmanager and a generic webhook can also mark devices down ([other sources](sources.md)). Alerts are matched to Nautobot devices by name or primary IP.
3. **Build the board.** Only **monitored** devices count: those with a primary IP, after exclusions. Each site gets a level: **Critical** (a core device down, or all devices down), **Medium** (over 25 % down), **Low** (one or more down), **OK** or **No data** ([alert levels](alert-board.md#alert-levels)). Maintenance windows, an alert delay and flapping detection keep noise off the board.
4. **Record history.** Every outage, level change, case and acknowledgement is stored, so history and availability are complete even when nobody has a page open.
5. **Notify.** When a site reaches the notify level (Critical by default) or recovers, a message goes to Teams, email or a webhook. During a large outage many changes are sent as one summary, not one message per site ([notifications](notifications.md)).
6. **Show.** The browser pages call the app's JSON API and update themselves. Large answers are gzipped.

## The pages

Every page has the same top bar (#392): the theme button and a **☰** menu with every page below.

| Page | Address | For |
|---|---|---|
| **Map** | `/` | Every location with coordinates, coloured by level; sites in alarm always stand out. Click for devices, ASNs, circuits |
| **Alert board** | `/alerts` | The NOC's work list: sites in alarm, down devices, since when, cases, acknowledge, maintenance, history, CSV |
| **Wall view** | `/alerts?view=wall` | A screen nobody clicks on: alarms only, "NO CASE" stands out, a loud warning when it stops updating |
| **Availability** | `/availability` | Uptime per site and tenant for any period, maintenance not counted; CSV exports |
| **Circuits** | `/circuits` | Circuit mapping: circuits without a termination and the site their address points to, with CSV |
| **Maintenance** | `/maintenance` | Plan windows for many sites or devices at once, see and end them, history |
| **Tenant status** | `/status/<tenant>` | "Are we affected?" for one customer, without the whole board |
| **API docs** | `/docs` | Every API endpoint, with a **Try it** button |
| **Health / metrics** | `/healthz`, `/metrics` | Liveness probe and Prometheus metrics |
| **MCP** | `/mcp` *(off by default)* | AI assistants read the board and add cases |

### Circuits in the site panel

The site panel lists a site's circuits in two ways (#235, #383):

- **Terminated there**: a circuit termination in Nautobot at the location. This is the proper link.
- **By address**: a circuit **without a termination at any location** whose **description is the site's physical address**. They are marked *By address*, after the terminated ones. Once a circuit has a termination (at this site, one of its buildings or anywhere else), it is no longer matched by address.

Addresses written as *street number, postcode town* match by **street, number and postcode**: `Roersvej 33, 5000 Odense C`, `Roersvej 33, 5000 Odense` and `Roersvej 33 st. tv, 5000 Odense` are the same; the town, a floor and door (`st. tv`, `2. th`) and a country don't count, and `33A` is `33 A` but not `33`. A full address in another form (`1 Canada Square, London E14 5AB`), or a description with more than the address (`Fibre 1G – Roersvej 33, …`), only matches exactly the same text. Anything shorter, such as a town or a street without a postcode, is never matched. When the address belongs to more than one site, the card says *address shared with N other sites* (a site's own buildings at the same address don't count). If the site's terminations can't be read from Nautobot, the panel says so and lists no circuits by address either.

Circuits are read from Nautobot in full with every inventory sync, so a new termination counts at the next sync (one request for a few hundred circuits; nothing per site). They and each location's address key are stored, so opening a panel is two indexed database lookups. Nautobot without the circuits app is simply no circuits. To link a circuit for good, add a termination at the location in Nautobot.

### Circuit mapping

**Circuits** in the ☰ menu (`/circuits`, #385) is the worklist for adding those terminations: every circuit without a termination at a location (Decommissioned ones left out), grouped by what its description's address found, worst first:

1. **Several sites**: the address belongs to more than one site; someone chooses where to terminate it.
2. **No site**: the description isn't an address of any site (more text, another form, an address no location has, or none).
3. **One site**: ready; add the termination at that site.

The groups start folded, with their counts; each shows 100 circuits at a time. The search looks in the circuit, provider, type, status, tenant, description and sites. A circuit links to it in Nautobot, a site to its panel on the map (a site without coordinates through a building of it at the address, else to the site in Nautobot). **Download CSV** (`/api/circuit-mapping.csv`) has every circuit with its group, commit rate, suggested sites and Nautobot id, for bulk work in Nautobot; the JSON is `/api/circuit-mapping`. It reads the stored circuits and locations (two queries, nothing asked of Nautobot), so a circuit leaves the list at the next sync after it gets a termination. Viewers and up, like the board.

## Setup

### Quick start (demo, no Nautobot needed)

```bash
docker compose -f demo/docker-compose.yml up --build
# → http://localhost:5000
```

### Real install

You need Docker and a Nautobot 2.x or 3.x API token (read-only is enough).

```bash
git clone https://github.com/Jackass4life/Nautobot-maps.git
cd Nautobot-maps
cp .env.example .env    # set NAUTOBOT_URL, NAUTOBOT_TOKEN and POSTGRES_PASSWORD
docker compose up --build -d
```

This starts three containers: `nautobot-maps` (the app), `postgres` and `redis`. On start the app migrates the database, then runs gunicorn. Everything is configured with environment variables in `.env` ([every setting](configuration.md)). Use `docker compose up`, not `build && start`.

### Production checklist

- Run a **released image**, not a local build ([operations](operations.md#run-a-released-version)).
- Put a **proxy with HTTPS** in front. For sign-in, `AUTH_MODE=header` with your SSO proxy passing the user and groups; roles are **viewer**, **operator**, **admin** ([sign-in and roles](authentication.md)). Let the app trust identity headers only from the proxy (`AUTH_TRUSTED_PROXIES`).
- Set your own `POSTGRES_PASSWORD` **before the first start**, and back up the database ([backup and restore](operations.md#backup-and-restore)).
- Optional: LibreNMS (`LIBRENMS_URL`, `LIBRENMS_API_TOKEN`), notifications (`NOTIFY_*`, `SMTP_*`), case links to your ITSM (`CASE_URL_TEMPLATE`), one row per Site (`ALERT_BOARD_SITE_LOCATION_TYPE`), importance tiers (`SITE_TIER_FIELD`), device and site criticality from Nautobot (`DEVICE_CRITICALITY_FIELD`, `TIER_DEVICE_LEVEL`).
- Monitor `/healthz` and scrape `/metrics` ([monitoring](operations.md#monitoring)).
- API tokens for scripts, Alertmanager and MCP: `docker compose exec nautobot-maps python -m nautobot_maps token create <name> --role operator`.

## Built for a large network

The app is designed for **1,200+ sites and 10,000+ devices**, with 20–30 sites and 50–100 devices down on a normal day:

- Lists show the worst first and collapse the rest ("+N more"); handled and maintenance sites sit quieter, below.
- Bulk actions: a case or acknowledgement for many devices, maintenance for many sites or a whole tenant.
- Notifications are summarised during a wide outage.
- No per-site requests or queries; Nautobot answers are cached in Redis; large answers are compressed (the board's data at 1,200 sites goes from about 1.3 MB to a tenth).

## What it can and can't do

**Can**
- Show every site on a map and on a board, with a level that means the same everywhere.
- Combine status from Nautobot, LibreNMS, Alertmanager and any webhook.
- Track who is on an alarm (case numbers, acknowledgements) and link cases to your ITSM.
- Plan maintenance so planned work is not an alarm, and exclude it from availability.
- Report availability per site and tenant for any period, with CSV exports that add up.
- Notify Teams, email or a webhook, rate-limited and summarised.
- Give each customer a status page; give AI assistants access through MCP.

**Can't (by design or not yet)**
- **Change Nautobot.** It is read-only: fix a site, device or IP in Nautobot, not here.
- **Monitor devices itself.** It doesn't ping or poll SNMP; it relies on Nautobot's status, LibreNMS, Alertmanager or webhooks.
- **Sign people in by itself** or serve HTTPS: that is the proxy's job.
- **Create tickets in your ITSM.** It stores case numbers and links to them; it doesn't open or close tickets.
- **Show sites without coordinates on the map** (they are still on the board).
- **Know the cabling.** "Likely caused by the core router" is a guess from device roles, not from topology.
- **Keep history forever by default.** Resolved history older than `ALERT_HISTORY_RETENTION_DAYS` (two years) is deleted; availability can't go back further.

## The code, briefly

- `app.py`: entry point. `nautobot_maps/`: one module per job (`nautobot`/`librenms` clients, `inventory` sync, `alerts` board, `maintenance`, `availability`, `notify`, `webhooks`, `auth`/`tokens`, `mcp`, `web` routes…).
- `static/js/`: the pages' JavaScript, one folder per page, shared helpers in `lib/`. `templates/`: the HTML.
- `tests/` (pytest, about 950 tests) and `e2e/` (Playwright in Chromium). CI runs lint, tests on Python 3.11 and 3.12, a real Nautobot, a Docker smoke test, a dependency audit and CodeQL.
- Development setup: [CONTRIBUTING.md](../CONTRIBUTING.md).
