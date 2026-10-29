# Tenant status page

`/status/<id>` answers "are we affected?" for one tenant, without the whole board. It is meant for account managers and customers (#291).

Each tenant's page has its own random link, such as `/status/q3Vx9…` (24 characters), not the tenant's name, so nobody can find another customer's page by guessing a name (#500). A link by name (`/status/Acme%20Corp`, the form used before #500) opens nothing: it gets the same "No sites found" answer as a link that doesn't exist, and the page tells the customer to ask for a new link. The links are kept in the app's PostgreSQL database, so the status page needs one (`NAUTOBOT_MAPS_DATABASE_URL`); without it, `/api/status/…` answers 503 and says so.

It shows:
- the tenant, with its description from Nautobot after a dash when it has one ("Acme Corp – Retail bank, 24/7", #482): the same text as the tenant's (i) on the board and Availability, so keep internal notes out of Nautobot's tenant descriptions;
- one line for the whole tenant ("All 5 sites operational", or "2 of 5 sites affected · 4 devices down");
- each of the tenant's sites with a state and since when, and under its name where it is: its address from Nautobot (or its facility, as on the board), or its GPS coordinates when it has no address, opening OpenStreetMap in a new tab: a pin when the coordinates are known, else a search for the address (#493); and how many devices it has (#503): with something down, how many of each kind, "3 of 12 network devices down · 1 of 40 access points down", else just how many, "12 network devices · 40 access points", quietly; worst first. Access points a [wireless controller](sources.md#wireless-controllers-access-points) reports down show at a site that is otherwise operational too, and in the headline ("All 75 sites operational · 3 devices down"), without changing its state (#508): a site with only access points down stays Operational, also while the board lists it under Wireless (#521). Under that, folded, the site's **circuits** (#511): "▸ 3 circuits", which opens to one line each, "DK-TELIA-123456 · Internet Transit · 1 Gbps" (circuit ID, type and bandwidth, never the provider). Only the tenant's own circuits (their tenant in Nautobot is this one; your uplinks and other customers' are never shown, so set the tenant on customer circuits) and only **Active** ones: those that end at the site, and those without a termination whose description is its address, as on the map (#383). A site without any has no line. A tenant with **more than 10 sites** sees only the ones that need a look first (Outage, Degraded, Planned maintenance, No data). The operational ones fold into one line, "▸ 72 sites operational", which opens to list them and stays open across the minute's refresh (#407). With every site fine, that line is all there is under "All 75 sites operational":

| State | Board level |
|-------|-------------|
| Outage | Critical |
| Degraded | Medium or Low |
| Planned maintenance (until …) | In a maintenance window |
| No data | No data, for a site with monitored devices |
| Operational | OK |

A site belongs to the tenant when it is the site's own tenant or one linked by a Relationship, the same rule as the board's tenant filter. Tenant names are matched without regard to case. Locations with nothing monitored (regions, buildings) are left out.

Access points are the device roles in `ACCESS_POINT_ROLES` (default `Access Point`), every other monitored device is network equipment, as on [Availability](availability.md). A device in maintenance, or at a site in maintenance, counts at its site but isn't down; nor are the devices of a site with no data. Only counts are shown: the page never shows device names, roles, IP addresses, cases, acknowledgements, maintenance reasons or other tenants. A site's address and coordinates are shown (#493): with a status token a customer only ever sees their own sites, and where the page is open to anyone, the board is too, and it already shows every site's address. It refreshes every minute and follows the viewer's light or dark setting.

## Per-site device roles

Set `TENANT_SCOPE_FIELD` to the name of a JSON custom field on Nautobot Locations to choose which device **roles** count for each tenant at each site:

```json
{"Acme Corp": ["Access Switch", "Firewall"]}
```

Tenant and role names are matched without regard to case. A tenant with no entry sees the whole site, as before. A tenant with an entry sees the state and downtime start calculated from devices in those roles; no device names or IP addresses are returned, and the device counts are of those roles only (#503), never another tenant's devices. If no monitored device at a site has a matching role, the site is hidden for that tenant. Maintenance and virtual chassis stacks are counted as they are on the board.

Leave `TENANT_SCOPE_FIELD` empty to turn this off. If the configured field contains malformed JSON or has an invalid shape, the sync logs one warning and that site falls back to all monitored devices.

## Who can see it

- **Staff:** the same people who can see the board. With `AUTH_REQUIRE_VIEWER=true` that is the `viewer` role. Without it the page is as open as the board is. A tenant's name on the Availability page opens its page.
- **A customer:** a **status token** for their tenant, sent in a link.

Create the token on the command line:

```sh
docker compose exec nautobot-maps python -m nautobot_maps token create acme-status --role status --tenant "Acme Corp" --expires-days 365
```

It prints the token, and below it the link to send, with the tenant's page and the key: `Share: /status/<id>#key=nmt_…`. The tenant must be one Nautobot has (as of the last sync; capitals don't matter), and the token keeps Nautobot's spelling: a typo such as `"Acme"` for "Acme Corp" is refused with `no tenant 'Acme' in Nautobot (as of the last sync)`, instead of making a link that shows nothing (#528).

Or with the API (admin role): `POST /api/tokens` with `{"name": "acme-status", "role": "status", "tenant": "Acme Corp", "expires_in_days": 365}`. The answer's `status_link` is the tenant's page, `/status/<id>`.

Then send this link:

```
https://maps.example.com/status/<id>#key=nmt_…
```

The tenant's page link on its own: `python -m nautobot_maps status-link "Acme Corp"` (a tenant Nautobot has, as of the last sync; a typo is refused). If a link leaks, `status-link "Acme Corp" --new` replaces it at once: the old link stops working, and every customer of that tenant needs the new one (their tokens keep working).

The same through the API (admin role, #529): `GET /api/status-links/Acme%20Corp` answers `{"tenant": "Acme Corp", "status_link": "/status/<id>"}`, and `POST /api/status-links/Acme%20Corp/replace` replaces it. A tenant Nautobot doesn't have answers 404.

How the link and token behave:
- The key goes after `#`, so the browser never sends it to the server: it isn't in access logs, proxy logs or a `Referer`. The page's script sends it as `Authorization: Bearer`.
- A status token opens **only** that tenant's `/api/status/…`. Everything else answers 403, including other tenants, the board and the API.
- To cut a link off, revoke the token (`token revoke <id>` or `POST /api/tokens/<id>/revoke`). The page then says the link no longer works.

## Behind an SSO proxy

The page itself carries no data, so a customer's browser must reach these paths without signing in:
- `/status/`
- `/api/status/`
- `/_nuxt/`: the page's files (it is built with Nuxt, #463). They are the app's code only, no data.

Before #463 the page loaded files from `/static/` (`css/alerts.css`, `js/status/…`, `js/lib/…`); a proxy rule for those alone now leaves the page blank.

Let these past the proxy's sign-in (oauth2-proxy: `--skip-auth-route`). The app then checks access on `/api/status/` itself.

As on every unauthenticated route, the proxy must **remove** `X-Forwarded-User` and `X-Forwarded-Groups` from the client (see [authentication](authentication.md#api-tokens)).

## API

`GET /api/status/<id>` (the tenant's link id; a name answers 404) returns:

```json
{
  "tenant": "Acme Corp",
  "description": "Retail bank, 24/7",
  "checked_at": "2026-10-09T12:00:00Z",
  "summary": {"sites": 5, "affected": 2, "devices_down": 4},
  "sites": [
    {
      "name": "London HQ", "state": "outage", "since": "2026-10-09T11:00:00Z", "until": null,
      "address": "1 Canada Square, London E14 5AB", "latitude": 51.5054, "longitude": -0.0235,
      "network_devices": 12, "access_points": 40, "network_devices_down": 3, "access_points_down": 1,
      "circuits": [{"cid": "DK-TELIA-123456", "type": "Internet Transit", "bandwidth": "1 Gbps"}]
    },
    {
      "name": "Amsterdam", "state": "maintenance", "since": null, "until": "2026-10-09T14:00:00Z",
      "address": null, "latitude": null, "longitude": null,
      "network_devices": 4, "access_points": 0, "network_devices_down": 0, "access_points_down": 0,
      "circuits": []
    }
  ]
}
```

It answers 404 when the tenant has no sites.
