# Tenant status page

`/status/<tenant>` answers "are we affected?" for one tenant, without the whole board. It is meant for account managers and customers (#291).

It shows:
- the tenant;
- one line for the whole tenant ("All 5 sites operational" or "2 of 5 sites affected");
- each of the tenant's sites with a state and since when, worst first. A tenant with **more than 10 sites** sees only the ones that need a look first (Outage, Degraded, Planned maintenance, No data). The operational ones fold into one line, "▸ 72 sites operational", which opens to list them and stays open across the minute's refresh (#407). With every site fine, that line is all there is under "All 75 sites operational":

| State | Board level |
|-------|-------------|
| Outage | Critical |
| Degraded | Medium or Low |
| Planned maintenance (until …) | In a maintenance window |
| No data | No data, for a site with monitored devices |
| Operational | OK |

A site belongs to the tenant when it is the site's own tenant or one linked by a Relationship, the same rule as the board's tenant filter. Tenant names are matched without regard to case. Locations with nothing monitored (regions, buildings) are left out.

The page never shows devices, IP addresses, addresses, cases, acknowledgements, maintenance reasons or other tenants. It refreshes every minute and follows the viewer's light or dark setting.

## Per-site device roles

Set `TENANT_SCOPE_FIELD` to the name of a JSON custom field on Nautobot Locations to choose which device **roles** count for each tenant at each site:

```json
{"Acme Corp": ["Access Switch", "Firewall"]}
```

Tenant and role names are matched without regard to case. A tenant with no entry sees the whole site, as before. A tenant with an entry sees the state and downtime start calculated from devices in those roles; no device names or IP addresses are returned. If no monitored device at a site has a matching role, the site is hidden for that tenant. Maintenance and virtual chassis stacks are counted as they are on the board.

Leave `TENANT_SCOPE_FIELD` empty to turn this off. If the configured field contains malformed JSON or has an invalid shape, the sync logs one warning and that site falls back to all monitored devices.

## Who can see it

- **Staff:** the same people who can see the board. With `AUTH_REQUIRE_VIEWER=true` that is the `viewer` role. Without it the page is as open as the board is.
- **A customer:** a **status token** for their tenant, sent in a link.

Create the token on the command line:

```sh
docker compose exec nautobot-maps python -m nautobot_maps token create acme-status --role status --tenant "Acme Corp" --expires-days 365
```

Or with the API (admin role): `POST /api/tokens` with `{"name": "acme-status", "role": "status", "tenant": "Acme Corp", "expires_in_days": 365}`.

Then send this link:

```
https://maps.example.com/status/Acme%20Corp#key=nmt_…
```

How the link and token behave:
- The key goes after `#`, so the browser never sends it to the server: it isn't in access logs, proxy logs or a `Referer`. The page's script sends it as `Authorization: Bearer`.
- A status token opens **only** that tenant's `/api/status/…`. Everything else answers 403, including other tenants, the board and the API.
- To cut a link off, revoke the token (`token revoke <id>` or `POST /api/tokens/<id>/revoke`). The page then says the link no longer works.

## Behind an SSO proxy

The page itself carries no data, so a customer's browser must reach these paths without signing in:
- `/status/`
- `/api/status/`
- `/static/css/alerts.css`
- `/static/js/status/main.js` and `/static/js/status/page.js`
- `/static/js/lib/format.js` and `/static/js/lib/html.js` (imported by the page's script)

Before #294 the script was the single file `/static/js/status.js`: a proxy rule for it alone now leaves the page without its data.

Let these past the proxy's sign-in (oauth2-proxy: `--skip-auth-route`). The app then checks access on `/api/status/` itself.

As on every unauthenticated route, the proxy must **remove** `X-Forwarded-User` and `X-Forwarded-Groups` from the client (see [authentication](authentication.md#api-tokens)).

## API

`GET /api/status/<tenant>` returns:

```json
{
  "tenant": "Acme Corp",
  "checked_at": "2026-10-09T12:00:00Z",
  "summary": {"sites": 5, "affected": 2},
  "sites": [
    {"name": "London HQ", "state": "outage", "since": "2026-10-09T11:00:00Z", "until": null},
    {"name": "Amsterdam", "state": "maintenance", "since": null, "until": "2026-10-09T14:00:00Z"}
  ]
}
```

It answers 404 when the tenant has no sites.
