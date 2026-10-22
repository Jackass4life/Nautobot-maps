# Configuration

Every setting is an environment variable, read from `.env` (copy `.env.example` to start). Only the first two are required. With Docker, `docker-compose.yml` passes each of them to the container; leaving one empty means its default.

## Nautobot

| Variable | Default | Description |
|---|---|---|
| `NAUTOBOT_URL` | **required** | Base URL of your Nautobot instance, e.g. `https://nautobot.example.com` (validated at startup) |
| `NAUTOBOT_TOKEN` | **required** | Nautobot API token. Read-only is enough: the app never writes to Nautobot |
| `NAUTOBOT_API_VERSION` | *(server default)* | Pin a Nautobot REST API version (e.g. `2.0`, `3.0`) |
| `NAUTOBOT_VERIFY_SSL` | `true` | Certificate verification: `true`, `false` (e.g. self-signed), or a path to a CA bundle |

## Database (PostgreSQL)

The alert board needs PostgreSQL; the map works without it. The bundled `docker-compose.yml` includes one.

| Variable | Default | Description |
|---|---|---|
| `NAUTOBOT_MAPS_DATABASE_URL` | bundled PostgreSQL (Docker) | `postgresql://...` URL for the inventory snapshot, alert history, cases and criticality overrides. Without it the alert board stays empty and says so |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `nautobot_maps` | Credentials of the bundled PostgreSQL (Docker only). Its port is not published, but set your own password **before the first start**: it is fixed when the data volume is created |
| `DB_CONNECT_TIMEOUT_SECONDS` | `5` | Give up connecting after this long (`/healthz` uses 2 s), instead of waiting for the operating system when the database drops packets |
| `DB_STATEMENT_TIMEOUT_SECONDS` | `60` | Cancel any single SQL statement after this long, so a runaway query can't hold a worker (migrations at startup are exempt) |
| `ALERT_HISTORY_RETENTION_DAYS` | `730` (two years) | Once a day, delete resolved alerts (with their events and cases) and site level changes older than this many days. `0` keeps everything. Open alerts are never deleted |

## Syncing and caching

| Variable | Default | Description |
|---|---|---|
| `BACKGROUND_SYNC_ENABLED` | `true` | Run due syncs and record alert history in the background with no page open. `false`: syncs only run when pages are loaded |
| `INVENTORY_SYNC_INTERVAL_SECONDS` | `CACHE_TTL` | Minimum seconds between Nautobot inventory syncs |
| `LIBRENMS_SYNC_INTERVAL_SECONDS` | `CACHE_TTL` | Minimum seconds between LibreNMS status refreshes |
| `CACHE_TTL` | `300` | Seconds to cache Nautobot API responses |
| `CACHE_TYPE` | `SimpleCache` (`RedisCache` in `docker-compose.yml`) | Cache backend (cachelib). `SimpleCache` is per worker; `RedisCache` shares the cache and rate limits between workers |
| `CACHE_REDIS_URL` | — (`redis://redis:6379/0` in `docker-compose.yml`) | Redis URL; required with `CACHE_TYPE=RedisCache` |
| `COMPRESS_RESPONSES` | `true` | gzip text answers over 1 KB (pages, JSON, CSS, JS, CSV) for browsers that accept it (#349): at 1,200 sites the board's data goes from about 1.3 MB to a tenth. `false` where the proxy in front compresses instead (see [operations](operations.md#what-the-container-does)) |

## LibreNMS (optional)

Set both `LIBRENMS_URL` and `LIBRENMS_API_TOKEN` to take device up/down status from LibreNMS. Each sync also caches the address LibreNMS polls for every device (`overwrite_ip` if set, otherwise `ip`); the alert board shows it when Nautobot has no primary IP for the device.

| Variable | Default | Description |
|---|---|---|
| `LIBRENMS_URL` | — | Base URL of your LibreNMS |
| `LIBRENMS_API_TOKEN` | — | LibreNMS API token |
| `LIBRENMS_VERIFY_SSL` | `true` | `true`, `false`, or a path to a CA bundle |

For a LibreNMS signed by an internal CA, mount the CA certificate into the container and point the setting at it (e.g. `LIBRENMS_VERIFY_SSL=/certs/internal-ca.pem`). `false` skips verification and should be a last resort, since the API token travels over that connection. A path that doesn't exist is logged as an error at startup.

### Near-real-time status: LibreNMS alert pushes

Without pushes, a device going down shows on the board after LibreNMS has polled it **and** the next LibreNMS sync here (`LIBRENMS_SYNC_INTERVAL_SECONDS`, default 300 s). With an alert transport, LibreNMS tells the app right away, and the board shows it within seconds. The periodic sync keeps running as the safety net.

1. Create an [API token](authentication.md#api-tokens) with the `operator` role:
   `docker compose exec nautobot-maps python -m nautobot_maps token create librenms --role operator`
2. In LibreNMS: **Alerts → Alert Transports → Create**, type **API**:
   - API Method: `POST`
   - API URL: `https://<nautobot-maps>/api/librenms/alert`
   - Headers: `Authorization=Bearer nmt_...` (the token)
   - Body: `{"device_id": "{{ $device_id }}"}`
3. Attach the transport to your device-down rules (e.g. "Devices up/down"), with **recovery** alerts on, so a device coming back is pushed too.
4. Use LibreNMS's **Test** button on the transport: the app answers `{"updated": true, ...}` with the device's status. If the body variable isn't filled in by your LibreNMS version, check its [alert template variables](https://docs.librenms.org/Alerting/Templates/) (`hostname` works as well as `device_id`).

What a push does: the app asks the LibreNMS API for that one device and stores its status; the push only says *which* device, so duplicate, late or non-device-down alerts can't set a wrong status. Each stored status remembers which LibreNMS request it came from, and an older answer never overwrites a newer one (from another push or from the periodic sync), so a quick down and up end in the right state; a repeated push just refreshes again. `{"updated": false}` means nothing changed: LibreNMS doesn't know the device, a newer answer was already stored, or the device was added to LibreNMS after the last sync here (the next sync picks it up) (not an error, so LibreNMS doesn't keep retrying). The parameters can also be sent as a form or in the query string (`?device_id=42`).

## Other monitoring tools (optional)

Prometheus Alertmanager and any tool that can call a webhook can mark devices down. See [other monitoring sources](sources.md).

| Variable | Default | Description |
|---|---|---|
| `ALERTMANAGER_DEVICE_LABELS` | `instance,hostname,host,device` | Alert labels that name the device; the first one an alert has is used (a port or URL is stripped) |
| `ALERTMANAGER_ALERTNAMES` | — | Only alerts with these `alertname`s; empty: every alert Alertmanager sends here |
| `WEBHOOK_ALERT_TTL_HOURS` | `24` | A firing alert that isn't repeated for this long stops counting, in case its "resolved" was lost. Keep Alertmanager's `repeat_interval` below it |

## Alert board

What these do is explained in [the alert board guide](alert-board.md). List settings take comma- or semicolon-separated values; in the status settings, `null` matches an empty status.

| Variable | Default | Description |
|---|---|---|
| `ALERT_BOARD_SITE_LOCATION_TYPE` | — | Location type that gets the board's rows (e.g. `Site`); devices in its child locations roll up into it. Empty: one row per location ([one row per Site](alert-board.md#one-row-per-site)) |
| `ALERT_BOARD_EXCLUDED_LOCATION_TYPES` | `graveyard,warehouse` | Location types hidden from the board |
| `ALERT_BOARD_EXCLUDED_LOCATION_STATUSES` | — | Location statuses hidden from the board |
| `ALERT_BOARD_EXCLUDED_LOCATION_TAGS` | — | Nautobot tag names hidden from the board |
| `ALERT_BOARD_EXCLUDED_LOCATION_NAMES` | — | Location names hidden from the board (fallback) |
| `ALERT_BOARD_EXCLUDED_DEVICE_STATUSES` | — | Device statuses ignored: not counted, not listed |
| `ALERT_MEDIUM_PERCENT` | `25` | More than this share of a site's monitored devices down is **Medium** ([alert levels](alert-board.md#alert-levels)) |
| `ALERT_CRITICAL_PERCENT` | `100` | This share or more down is **Critical** (100: only when all are down); a down core device is always Critical, and a device or tier set to a level gives at least that level |
| `COUNTRY_LOCATION_TYPE` | `Country` | The Nautobot location type whose locations are countries (case ignored): a site's country is the nearest one above it, before the address is looked at ([country](alert-board.md#one-row-per-site)). Changing it makes the next sync a full one |
| `SITE_TIER_FIELD` | — | Nautobot custom field on the location with its importance tier (e.g. `tier`) ([importance](alert-board.md#importance-tier)) |
| `TENANT_SCOPE_FIELD` | — | Nautobot JSON custom field on each Location mapping tenant names to device roles counted on that tenant's status page; missing tenant entries count all devices, and no role matches hides that site ([tenant status page](status-page.md#per-site-device-roles)) |
| `SITE_TIERS` | `gold,silver,bronze` | The tier values, most important first; other values are no tier |
| `TIER_DEVICE_LEVEL` | — | What any down device makes a site of that tier, e.g. `gold:critical,silver:medium`; a device's own field overrides it ([device and site criticality](alert-board.md#device-and-site-criticality)) |
| `DEVICE_CRITICALITY_FIELD` | — | Nautobot custom field on the device (a select: `critical`, `medium`, `low`): what that device makes its site when down; it wins over the tier, the overrides and the role keywords. Setting it makes the next sync a full one |
| `CASE_URL_TEMPLATE` | — | Makes every case number a link to its ticket, e.g. `https://itsm.example.com/ticket/{case}` (`{case}` is the URL-encoded number; only `http(s)://`) |
| `AVAILABILITY_BASIS` | `any_down` | What the [availability report](availability.md) counts as downtime: `any_down` (any monitored device of the site down) or `critical` (the site at Critical) |
| `FLAP_CHANGES` | `4` | A device that goes up or down this many times within 30 minutes is flapping: one incident, a FLAPPING label, a steady site level ([flapping](alert-board.md#flapping)). `0`: off |
| `ALERT_DELAY_SECONDS` | `0` | A device must be down this long before it alarms; shorter blips are not shown, counted, recorded or notified ([alert delay](alert-board.md#alert-delay)) |
| `CRITICAL_ROLE_KEYWORDS` | `core,spine,distribution,router,gateway` | Device role keywords that make a down device **Critical** |
| `CRITICALITY_RULES_FILE` | — | JSON file with keywords per location type, overriding `CRITICAL_ROLE_KEYWORDS`; see `criticality_rules.json` |
| `SITE_TENANT_RELATIONSHIPS` | — | Nautobot Relationships (keys or labels) that link a location to more tenants, shown next to its own tenant. Empty: every Location ↔ Tenant relationship |

## Sign-in and roles

See [authentication](authentication.md).

| Variable | Default | Description |
|---|---|---|
| `AUTH_MODE` | `disabled` | `disabled`, or `header` behind a sign-in proxy |
| `AUTH_HEADER_USER` | `X-Forwarded-User` | Header with the user name, set by the proxy |
| `AUTH_HEADER_GROUPS` | `X-Forwarded-Groups` | Header with the user's groups, set by the proxy |
| `AUTH_VIEWER_GROUPS` / `AUTH_OPERATOR_GROUPS` / `AUTH_ADMIN_GROUPS` | — | Comma-separated group names for each role |
| `AUTH_DEFAULT_ROLE` | — | Role (`viewer`, `operator`, `admin`) for signed-in users with no matching group |
| `AUTH_TRUSTED_PROXIES` | `127.0.0.1/32,::1/128` | IPs/CIDRs of the proxy; identity headers from anywhere else are ignored and logged |
| `AUTH_PROXY_SECRET` | — | When set, identity headers only count if the proxy also sends it in `X-Auth-Proxy-Secret` |
| `AUTH_REQUIRE_VIEWER` | `false` | Header mode: every page and API (except `/healthz`, `/metrics`) needs at least `viewer` |
| `ALLOW_UNAUTHENTICATED_WRITES` | `false` | `AUTH_MODE=disabled` only: allow the operator and admin changes without sign-in: criticality overrides, maintenance windows and sending test notifications (logged as a warning) |

### The bundled HTTPS and sign-in proxy

Optional: nginx and oauth2-proxy in front of the app, with `COMPOSE_FILE=docker-compose.yml:deploy/proxy/compose.yml` ([how to set it up](authentication.md#the-bundled-proxy-https-and-company-sign-in-423)). It sets `AUTH_MODE=header`, the trusted proxy and `AUTH_REQUIRE_VIEWER=true` for you.

| Variable | Default | Description |
|---|---|---|
| `PUBLIC_HOSTNAME` | required | The host name people open, e.g. `maps.example.com`; the certificate must match it |
| `OIDC_ISSUER_URL` | required | Your identity provider, e.g. `https://login.microsoftonline.com/<tenant-id>/v2.0` |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | required | The app's registration with the identity provider |
| `AUTH_PROXY_SECRET` | required | A long random value nginx sends to the app (`openssl rand -hex 32`) |
| `OAUTH2_COOKIE_SECRET` | required | 32 random bytes, base64 URL-safe, for the session cookie |
| `OIDC_GROUPS_CLAIM` | `groups` | The ID token claim with the user's groups |
| `OIDC_EMAIL_CLAIM` | `email` | Entra ID: `preferred_username` |
| `OIDC_SCOPE` | `openid email profile` | The scopes asked for |
| `PROXY_HTTP_PORT` / `PROXY_HTTPS_PORT` | `80` / `443` | The ports nginx listens on |
| `PROXY_SUBNET` / `PROXY_IP` | `172.30.0.0/24` / `172.30.0.10` | nginx's own network and address, the one the app trusts; change both if the range is taken |

The certificate goes in `deploy/proxy/certs/` as `fullchain.pem` and `privkey.pem`.

## Notifications

See [notifications](notifications.md). Each channel is on when its setting is filled in.

| Variable | Default | Description |
|---|---|---|
| `NOTIFY_MIN_LEVEL` | `critical` | `low`, `medium` or `critical`: notify when a site reaches it (or gets worse above it) and when it drops below it |
| `NOTIFY_MIN_LEVEL_BY_TIER` | — | `NOTIFY_MIN_LEVEL` for some importance tiers, e.g. `gold:low,silver:medium` |
| `NOTIFY_TENANTS` | — | Only sites with one of these tenants (comma-separated) |
| `NOTIFY_BOARD_URL` | — | The app's address, for the link in messages, e.g. `https://nautobot-maps.example.com` |
| `NOTIFY_SUMMARY_THRESHOLD` | `5` | More messages due at once on a channel go out as one summary |
| `NOTIFY_WEBHOOK_URL` | — | Webhook: JSON `POST` to this URL |
| `NOTIFY_WEBHOOK_SECRET` | — | Signs the webhook body (`X-Nautobot-Maps-Signature: sha256=…`) |
| `NOTIFY_TEAMS_WEBHOOK_URL` | — | Microsoft Teams Workflows webhook URL |
| `NOTIFY_EMAIL_TO` | — | Email recipients (comma-separated); needs `SMTP_HOST` |
| `SMTP_HOST` / `SMTP_PORT` | — / `587` | Mail server |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | — | Login; none for an internal relay |
| `SMTP_FROM` | `SMTP_USERNAME`, else `nautobot-maps@localhost` | Sender address |
| `SMTP_STARTTLS` | `true` | Encrypt the connection with STARTTLS |

## MCP server

See [MCP server](mcp.md).

| Variable | Default | Description |
|---|---|---|
| `MCP_ENABLED` | `false` | Serve the MCP server at `/mcp`; off: 404 |
| `MCP_ALLOWED_ORIGINS` | — | Browser origins (`https://host[:port]`) allowed to call `/mcp`. MCP clients send no `Origin`; any request that does is refused unless listed |

## Map and address search

| Variable | Default | Description |
|---|---|---|
| `MAP_TILE_URL` | OpenStreetMap | Tile URL template (`{s}`, `{z}`, `{x}`, `{y}`), e.g. an internal tile server |
| `MAP_TILE_ATTRIBUTION` | OpenStreetMap | Attribution shown on the map (HTML allowed) |
| `MAP_DEFAULT_VIEW` | empty | The map's first view as `lat,lon,zoom` (zoom 1–19), e.g. `55.7,12.6,6` for your region. Empty: the map fits itself to every site with coordinates (not 0,0, which is a site without them). Set it when that fit is too wide: sites on both sides of the 180° meridian, or one with wrong coordinates. A wrong value is ignored with a warning in the log |
| `GEOCODER_ENABLED` | `true` | `false`: address search off, only `lat,lon` searches; nothing is sent to a geocoder |
| `GEOCODER_URL` | `https://nominatim.openstreetmap.org` | Nominatim-compatible geocoder |
| `GEOCODER_USER_AGENT` | `nautobot-maps (+repo URL)` | User agent sent to the geocoder (the public Nominatim requires one that identifies you) |

By default viewers' browsers load tiles from `tile.openstreetmap.org`, and the app sends address searches to the public [Nominatim](https://nominatim.org/). On closed networks, or to keep that data inside, point both settings at internal services or turn address search off. Geocoding results are cached for a day, and the geocoder is asked at most once a second (a busy search gets "try again in a second"), as the public service's usage policy requires.

## Server and logging

| Variable | Default | Description |
|---|---|---|
| `GUNICORN_WORKERS` | `4` | Worker processes (Docker image): gunicorn runs uvicorn workers, each serving several requests at once |
| `GUNICORN_TIMEOUT` | `120` | Worker timeout in seconds; lower values are raised to 120 |
| `GUNICORN_BIND` | `0.0.0.0:5000` | Listen address |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`, for the app and gunicorn |
| `LOG_FORMAT` | `text` | `text`, or `json` (one object per line, for Loki, ELK, Splunk) |
| `METRICS_ENABLED` | `true` | Prometheus metrics at `/metrics`; `false`: 404 |
| `FLASK_DEBUG` | `false` | Flask debug mode (development server only) |
| `FLASK_RUN_PORT` | `5000` | Port of the development server (e.g. when macOS AirPlay Receiver has 5000) |
