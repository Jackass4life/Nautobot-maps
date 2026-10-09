# Nautobot Maps

Your Nautobot sites on a map, and an alert board that shows which sites are down, since when, and who has a case on them.

<!-- Screenshots: add the images to docs/images/ and remove this comment around them.
![The map](docs/images/map.png)
![The alert board](docs/images/alert-board.png)
![The wall view](docs/images/wall-view.png)
-->

## Features

- **Map** of every Nautobot location with coordinates, coloured by alert level, with clustering for large inventories. Click a site for its devices, ASNs and circuits; search by address or `lat,lon` for sites within 5 km.
- **Alert board**: each site's level (Critical, Medium, Low), its down devices with IP, role and down-since time, downtime, and case numbers. It opens on sites with alarms and updates itself.
- **Wall view** for a NOC screen: alarms only, no buttons, and a loud warning when it stops updating.
- **Cases and ITSM**: attach a case number to down devices, or copy a site as text for a ticket. History and an activity feed show what went down and came back.
- **LibreNMS** status (optional) next to Nautobot's.
- **Notifications** to a webhook, Microsoft Teams or email when a site becomes Critical and when it recovers.
- **Maintenance windows** for sites or single devices, now or planned ahead: planned work is not an alarm.
- **Sign-in and roles** through your SSO proxy (viewer, operator, admin).
- **MCP server** (optional) so AI assistants can read the board and add cases.
- **Read-only towards Nautobot**: the app never changes anything there, so a read-only API token is enough.

## Try it

No Nautobot needed: the demo comes with a mock Nautobot holding nine European sites.

```bash
docker compose -f demo/docker-compose.yml up --build
# → http://localhost:5000
```

See [`demo/README.md`](demo/README.md) for the seed data and things to try.

## Install

You need Docker and a Nautobot 2.x or 3.x with an API token.

```bash
git clone https://github.com/Jackass4life/Nautobot-maps.git
cd Nautobot-maps
cp .env.example .env    # set NAUTOBOT_URL, NAUTOBOT_TOKEN and POSTGRES_PASSWORD
docker compose up --build -d
# → http://localhost:5000
```

> **Use `docker compose up`, not `docker compose build && docker compose start`.** `start` only restarts containers that already exist and fails with *"service … has no container to start"* on a fresh checkout.

The bundled PostgreSQL holds the alert board's data. Set `POSTGRES_PASSWORD` before the first start: it is fixed when the data volume is created. For LibreNMS, sign-in and everything else, see [configuration](docs/configuration.md). In production, run a released image rather than a local build ([operations](docs/operations.md#run-a-released-version)).

Without Docker (for development): `pip install -r requirements.txt`, then `python app.py`. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation

| | |
|---|---|
| [Configuration](docs/configuration.md) | Every setting |
| [The alert board](docs/alert-board.md) | Alert levels, hiding sites and devices, one row per Site, cases, automatic updates, wall view |
| [Running in production](docs/operations.md) | Released images, overrides, backup and restore, migrations, monitoring |
| [Notifications](docs/notifications.md) | Webhook, Teams and email when a site's level changes |
| [Sign-in and roles](docs/authentication.md) | SSO proxy setup and what each role can do |
| [MCP server](docs/mcp.md) | Connecting AI assistants |
| API | **API** in the app's top bar opens `/docs`: every endpoint with its parameters and a **Try it** button |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each release |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup, tests, code layout |

## Nautobot versions

Tested with Nautobot 2.x and 3.x. In 3.x, ASNs are read from the field on each Location (the `ipam/asns/` endpoint needs the BGP Models plugin), and names missing from brief nested objects are looked up separately, so both work without extra setup. Only locations with both latitude and longitude appear on the map.

## License

[Apache License 2.0](LICENSE).
