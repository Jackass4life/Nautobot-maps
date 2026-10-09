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
| `ALERT_HISTORY_RETENTION_DAYS` | `0` (keep all) | Once a day, delete resolved alerts (with their events and cases) and site level changes older than this many days. Open alerts are never deleted |

## Syncing and caching

| Variable | Default | Description |
|---|---|---|
| `BACKGROUND_SYNC_ENABLED` | `true` | Run due syncs and record alert history in the background with no page open. `false`: syncs only run when pages are loaded |
| `INVENTORY_SYNC_INTERVAL_SECONDS` | `CACHE_TTL` | Minimum seconds between Nautobot inventory syncs |
| `LIBRENMS_SYNC_INTERVAL_SECONDS` | `CACHE_TTL` | Minimum seconds between LibreNMS status refreshes |
| `CACHE_TTL` | `300` | Seconds to cache Nautobot API responses |
| `CACHE_TYPE` | `SimpleCache` | Flask-Caching backend. Use `RedisCache` in production with several workers |
| `CACHE_REDIS_URL` | — | Redis URL, e.g. `redis://redis:6379/0`; required with `CACHE_TYPE=RedisCache` |

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

What a push does: the app asks the LibreNMS API for that one device and stores its status; the push only says *which* device, so duplicate, late or non-device-down alerts can't set a wrong status. Several pushes for one device within 5 seconds ask LibreNMS once. A device LibreNMS doesn't know is answered `{"updated": false}` (not an error, so LibreNMS doesn't keep retrying). The parameters can also be sent as a form or in the query string (`?device_id=42`).

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

## Notifications

See [notifications](notifications.md). Each channel is on when its setting is filled in.

| Variable | Default | Description |
|---|---|---|
| `NOTIFY_MIN_LEVEL` | `critical` | `low`, `medium` or `critical`: notify when a site reaches it (or gets worse above it) and when it drops below it |
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
| `GEOCODER_ENABLED` | `true` | `false`: address search off, only `lat,lon` searches; nothing is sent to a geocoder |
| `GEOCODER_URL` | `https://nominatim.openstreetmap.org` | Nominatim-compatible geocoder |
| `GEOCODER_USER_AGENT` | `nautobot-maps (+repo URL)` | User agent sent to the geocoder (the public Nominatim requires one that identifies you) |

By default viewers' browsers load tiles from `tile.openstreetmap.org`, and the app sends address searches to the public [Nominatim](https://nominatim.org/). On closed networks, or to keep that data inside, point both settings at internal services or turn address search off. Geocoding results are cached for a day, and the geocoder is asked at most once a second (a busy search gets "try again in a second"), as the public service's usage policy requires.

## Server and logging

| Variable | Default | Description |
|---|---|---|
| `GUNICORN_WORKERS` | `4` | Worker processes (Docker image) |
| `GUNICORN_TIMEOUT` | `120` | Worker timeout in seconds; lower values are raised to 120 |
| `GUNICORN_BIND` | `0.0.0.0:5000` | Listen address |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`, for the app and gunicorn |
| `LOG_FORMAT` | `text` | `text`, or `json` (one object per line, for Loki, ELK, Splunk) |
| `METRICS_ENABLED` | `true` | Prometheus metrics at `/metrics`; `false`: 404 |
| `FLASK_DEBUG` | `false` | Flask debug mode (development server only) |
| `FLASK_RUN_PORT` | `5000` | Port of the development server (e.g. when macOS AirPlay Receiver has 5000) |
