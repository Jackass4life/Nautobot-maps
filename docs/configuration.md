# Configuration

Every setting the app has, with its default. This page is for the person installing and running the app; what to fill in **in Nautobot** is on [What to set in Nautobot and LibreNMS](nautobot-setup.md).

## In short

- **Where settings go:** every setting is in the `.env` file next to `docker-compose.yml`, and only there: `cp .env.example .env && chmod 600 .env`. After a change: `docker compose up -d`.
- **Required:** only `NAUTOBOT_URL` and `NAUTOBOT_TOKEN`, plus your own `POSTGRES_PASSWORD` (in it and in `NAUTOBOT_MAPS_DATABASE_URL`) before the first start.
- **Leaving a setting empty** (or out) means its default.
- **No files:** the wireless controllers and the criticality rules are JSON in their setting, a company CA is the certificate's text. [How .env works](#how-env-works).

**The settings most people change:**

| You want… | Setting | Section |
|---|---|---|
| HTTPS and company sign-in | `COMPOSE_FILE=docker-compose.yml:reverse-proxy/compose.yml` and the proxy settings | [The bundled proxy](#the-bundled-https-and-sign-in-proxy) |
| LibreNMS status | `LIBRENMS_URL`, `LIBRENMS_API_TOKEN` | [LibreNMS](#librenms-optional) |
| Access points from controllers | `WIRELESS_CONTROLLERS` | [Wireless controllers](#wireless-controllers-optional) |
| A company CA (Nautobot, LibreNMS, controllers, AD) | `EXTRA_CA_PEM` | [Nautobot](#nautobot) |
| One board row per Site | `ALERT_BOARD_SITE_LOCATION_TYPE=Site` | [Alert board](#alert-board) |
| Important sites and devices | `SITE_TIER_FIELD`, `TIER_DEVICE_LEVEL`, `DEVICE_CRITICALITY_FIELD` | [Alert board](#alert-board) |
| Case numbers that open your ticket | `CASE_URL_TEMPLATE` | [Alert board](#alert-board) |
| Your SLA target | `AVAILABILITY_TARGET_PCT` | [Alert board](#alert-board) |
| Messages to Teams or email | `NOTIFY_TEAMS_WEBHOOK_URL`, `NOTIFY_EMAIL_TO`, `SMTP_*` | [Notifications](#notifications) |

### How .env works

Every container gets the whole `.env` (`env_file: .env` in each compose file, #739): the API, the pages, the database and, with the bundled proxy, nginx and oauth2-proxy. The compose files set no settings of their own.

- **Keep it private:** `.env` holds the passwords (the database, the LDAP bind account, the wireless controllers', the proxy's secrets): `chmod 600 .env`. It is git-ignored and never goes into an image (each `.dockerignore` leaves it out). Back it up: it is not in the database.
- **Values as written:** no `${VAR}` references. Put single quotes around JSON and around a value with a `$` in it.
- **JSON settings** (`WIRELESS_CONTROLLERS`, `CRITICALITY_RULES`): on one line, in single quotes, e.g. `CRITICALITY_RULES='{"office": ["core", "router"]}'`. A mistake stops the app at startup and says where (never a password).
- **A certificate** (`EXTRA_CA_PEM`): the PEM text on one line with `\n` for each line break, or base64 of the file (`base64 -w0 company-ca.pem`).
- **Outside Docker** (`uv run python run.py` in `api/`): the app reads `api/.env`; copy `.env.example` there and use `localhost` instead of the service names (`postgres`, `redis`).
- **Read by** (the column in each table below): which part acts on the setting. The others get it too and ignore it.
  - `api`: the API container (`app/core/settings.py`, the only place the API reads settings).
  - `gunicorn`: the API container's server (`api/gunicorn_config.py`).
  - `web`: the pages' container (Nuxt's server).
  - `postgres`: the bundled PostgreSQL.
  - `nginx` / `oauth2-proxy`: the bundled proxy's containers.
  - `compose`: `docker compose` itself, while it starts the containers (a port, a network, a file to mount, oauth2-proxy's command line, nginx's build). `compose (nginx build)` needs `docker compose up -d --build` after a change.
  - `build`: only when the pages are built (`bun run generate`); a running container ignores it.
  - `dev server`: only `uv run python run.py` (development), never the Docker image.
- **Check it:** `python -m app.check_env` lists what in your `.env` nothing reads (a typo, a leftover, another app's names), what the API would refuse, a name set twice (only the last line counts) and lines set to the default anyway. It prints names and line numbers, never a value, and exits with 1 when something is unread or invalid. With Docker, from the folder with `.env`:
  ```
  docker compose run --rm --no-deps -T --entrypoint python api -m app.check_env - < .env
  ```
  At startup the API also warns about a name in its environment that looks like a setting but isn't one: `Unknown setting AUTH_MOD (did you mean AUTH_MODE?): ignored`.
- **Upgrading from a version with `config/`:** `WIRELESS_CONTROLLERS_FILE`, `CRITICALITY_RULES_FILE` and `LDAP_CA_FILE` stop the app at startup and say what replaces them; a `*_VERIFY_SSL` with a file's path does too. [Operations](operations.md#every-setting-in-env-no-config-folder-739) has the steps.

## Nautobot

| Variable | Read by | Default | Description |
|---|---|---|---|
| `NAUTOBOT_URL` | api | **required** | Base URL of your Nautobot instance, e.g. `https://nautobot.example.com` (validated at startup) |
| `NAUTOBOT_TOKEN` | api | **required** | Nautobot API token. Read-only is enough: the app never writes to Nautobot |
| `NAUTOBOT_API_VERSION` | api | *(server default)* | Pin a Nautobot REST API version (e.g. `2.0`, `3.0`) |
| `NAUTOBOT_VERIFY_SSL` | api | `true` | Certificate verification: `true` or `false` (e.g. self-signed). Signed by a company CA: keep `true` and set `EXTRA_CA_PEM` |
| `EXTRA_CA_PEM` | api | — | Your company CA's certificate(s), as PEM text (`\n` for line breaks) or base64 of it (#739). Trusted together with the public CAs for Nautobot, LibreNMS and the wireless controllers; alone for the domain controller (AD sign-in). Checked at startup |

## Database (PostgreSQL)

The alert board needs PostgreSQL; the map works without it. The bundled `docker-compose.yml` includes one.

| Variable | Read by | Default | Description |
|---|---|---|---|
| `NAUTOBOT_MAPS_DATABASE_URL` | api | — | `postgresql://...` URL for the inventory snapshot, alert history, cases and criticality overrides. With the bundled PostgreSQL: `postgresql://nautobot_maps:<POSTGRES_PASSWORD>@postgres:5432/nautobot_maps` (in `.env.example`). Without it the alert board stays empty and says so |
| `DATABASE_PASSWORD` | api | — | The database password, as is (no URL encoding: `@`, `:` or `%` need no escaping); used instead of the one in `NAUTOBOT_MAPS_DATABASE_URL`, which can then leave it out |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | postgres | `nautobot_maps` | Credentials of the bundled PostgreSQL (Docker only). Its port is not published, but set your own password **before the first start**: it is fixed when the data volume is created |
| `DB_CONNECT_TIMEOUT_SECONDS` | api | `5` | Give up connecting after this long (`/healthz` uses 2 s), instead of waiting for the operating system when the database drops packets |
| `DB_STATEMENT_TIMEOUT_SECONDS` | api | `60` | Cancel any single SQL statement after this long, so a runaway query can't hold a worker (migrations at startup are exempt) |
| `ALERT_HISTORY_RETENTION_DAYS` | api | `730` (two years) | Once a day, delete resolved alerts (with their events and cases) and site level changes older than this many days. `0` keeps everything. Open alerts are never deleted |

## Sign-in and roles

See [authentication](authentication.md).

| Variable | Read by | Default | Description |
|---|---|---|---|
| `AUTH_MODE` | api | `disabled` | `disabled`, or `header` behind a sign-in proxy |
| `AUTH_HEADER_USER` | api | `X-Forwarded-User` | Header with the user name, set by the proxy |
| `AUTH_HEADER_GROUPS` | api | `X-Forwarded-Groups` | Header with the user's groups, set by the proxy |
| `AUTH_VIEWER_GROUPS` / `AUTH_OPERATOR_GROUPS` / `AUTH_ADMIN_GROUPS` | api | — | Comma-separated group names for each role |
| `AUTH_NOC_GROUPS` / `AUTH_CUSTOMER_GROUPS` | api | — | Comma-separated group names for the NOC and customer roles ([roles](authentication.md#roles)) |
| `AUTH_DOV_GROUPS` | api | — | Deprecated: the old name of `AUTH_NOC_GROUPS` (the NOC role was DOV until 0.5.1), read only when `AUTH_NOC_GROUPS` is empty |
| `AUTH_DEFAULT_ROLE` | api | — | Role (`viewer`, `operator`, `admin`) for signed-in users with no matching group |
| `AUTH_TRUSTED_PROXIES` | api | `127.0.0.1/32,::1/128` | IPs/CIDRs of the proxy; identity headers from anywhere else are ignored and logged |
| `AUTH_PROXY_SECRET` | api, nginx | — | When set, identity headers only count if the proxy also sends it in `X-Auth-Proxy-Secret` |
| `AUTH_SIGN_OUT_URL` | api | — | Header mode: where the top bar's **Sign out** goes, your proxy's sign-out address (a path such as `/oauth2/sign_out`, or an `http(s)://` address); empty: no Sign out. The bundled proxy sets it ([signing out](authentication.md#signing-out)) |
| `AUTH_REQUIRE_VIEWER` | api | `false` | Header mode: every page and API (except `/healthz`, `/metrics`) needs at least `viewer` |
| `AUTH_GROUP_ROLES` | api | `true` | Someone signed in without an [identity](authentication.md#identities) gets the role their groups give; `false`: no access. Set it to `false` once everyone has an identity |
| `LDAP_URL` | api | — | Sign-in with AD on the app's own page ([how](authentication.md#sign-in-with-ad)): `ldaps://dc1.example.local`. Needs `AUTH_MODE=header`, `LDAP_BASE_DN` and `JWT_SIGNING_SECRET` |
| `LDAP_BASE_DN` | api | — | Where users are looked up, e.g. `DC=example,DC=local` |
| `LDAP_BIND_DN` / `LDAP_BIND_PASSWORD` | api | — | The read-only service account that finds a user before their own sign-in, e.g. `svc-maps@example.local` |
| `LDAP_USER_ATTRIBUTE` | api | `sAMAccountName` | The attribute people sign in with (its value is their identity's name) |
| `LDAP_ALLOW_PLAINTEXT` | api | `false` | Allow `ldap://` (passwords unencrypted): tests only |
| `LDAP_TIMEOUT_SECONDS` | api | `10` | Connecting to and asking the directory |
| `JWT_SIGNING_SECRET` | api | — | Signs the app's sign-in tokens: at least 32 random characters (`openssl rand -base64 48`). Changing it signs everyone out |
| `JWT_LIFETIME_SECONDS` | api | `28800` | How long a sign-in lasts (8 hours) |
| `JWT_ISSUER` / `JWT_AUDIENCE` | api | `nautobot-maps` | Written in and checked on every token |
| `ALLOW_UNAUTHENTICATED_WRITES` | api | `false` | `AUTH_MODE=disabled` only: allow the operator and admin changes without sign-in: criticality overrides, maintenance windows, sending test notifications, API tokens, status page links and the wireless check, and the Admin page (logged as a warning) |

### The bundled HTTPS and sign-in proxy

Optional: nginx and oauth2-proxy in front of the app, with `COMPOSE_FILE=docker-compose.yml:reverse-proxy/compose.yml` ([how to set it up](authentication.md#the-bundled-proxy-https-and-company-sign-in)).

The API behind it needs these in `.env` too (#739: the compose files set nothing): `AUTH_MODE=header`, `AUTH_REQUIRE_VIEWER=true`, `AUTH_TRUSTED_PROXIES=<PROXY_IP>/32` (`172.30.0.10/32` by default) and, with oauth2-proxy, `AUTH_SIGN_OUT_URL=/oauth2/sign_out`. The API refuses to start with `AUTH_PROXY_SECRET` set but without `AUTH_MODE=header`.

Or nginx alone, with the app's own sign-in (AD on `/auth`) instead of oauth2-proxy: `COMPOSE_FILE=docker-compose.yml:reverse-proxy/compose.app-sign-in.yml` ([how](authentication.md#with-the-bundled-proxy)). It needs `PUBLIC_HOSTNAME`, `AUTH_PROXY_SECRET`, the ports and network below, and the [AD settings](#sign-in-and-roles) (`LDAP_URL`, `LDAP_BASE_DN`, `JWT_SIGNING_SECRET`, …); none of the `OIDC_*`. The proxy's image is then built with `SIGN_IN=app` (`reverse-proxy/Dockerfile`'s build argument; default `oauth2-proxy`).

| Variable | Read by | Default | Description |
|---|---|---|---|
| `PUBLIC_HOSTNAME` | compose (nginx build, oauth2-proxy) | required | The host name people open, e.g. `maps.example.com`; the certificate must match it |
| `OIDC_ISSUER_URL` | compose (oauth2-proxy), nginx | required | Your identity provider, e.g. `https://login.microsoftonline.com/<tenant-id>/v2.0` |
| `OIDC_CLIENT_ID` | compose (oauth2-proxy) | required | The app's registration with the identity provider |
| `OAUTH2_PROXY_CLIENT_SECRET` | oauth2-proxy | required | Its secret, under oauth2-proxy's own name (it was `OIDC_CLIENT_SECRET` before #739) |
| `AUTH_PROXY_SECRET` | api, nginx | required | A long random value nginx sends to the app (`openssl rand -hex 32`) |
| `OAUTH2_PROXY_COOKIE_SECRET` | oauth2-proxy | required | 32 random bytes, base64 URL-safe, for the session cookie (`OAUTH2_COOKIE_SECRET` before #739) |
| `OIDC_GROUPS_CLAIM` | compose (oauth2-proxy) | `groups` | The ID token claim with the user's groups |
| `OIDC_EMAIL_CLAIM` | compose (oauth2-proxy) | `email` | Entra ID: `preferred_username` |
| `OIDC_SCOPE` | compose (oauth2-proxy) | `openid email profile` | The scopes asked for |
| `OIDC_CA_FILE` / `OAUTH2_PROXY_PROVIDER_CA_FILES` | compose (oauth2-proxy) / oauth2-proxy | — | A PEM file with the CA(s) of an identity provider whose certificate comes from a company CA, e.g. `./reverse-proxy/certs/company-ca.pem`, and `/etc/oauth2-proxy/provider-ca.pem` (where it is mounted) so oauth2-proxy reads it; set both. Trusted together with the public CAs ([company CA](authentication.md#company-ca)). oauth2-proxy reads only a file: the one file setting left |
| `OIDC_LOGOUT_URL` | nginx | — | Your identity provider's logout address: **Sign out** ends the app's session, then goes here so the provider's session ends too ([signing out](authentication.md#signing-out)). Empty (GitLab has none): a "You are signed out" page, linking the provider to sign out there |
| `PROXY_HTTP_PORT` / `PROXY_HTTPS_PORT` | compose | `80` / `443` | The ports nginx listens on. HTTPS must stay on 443: the sign-in's redirect and the HTTP→HTTPS redirect name no port. HTTP may move (port 80 taken); only the HTTP→HTTPS redirect is then lost |
| `PROXY_SUBNET` / `PROXY_IP` | compose | `172.30.0.0/24` / `172.30.0.10` | nginx's own network and address, the one the app trusts; change both if the range is taken |

The certificate goes in `reverse-proxy/certs/` as `fullchain.pem` and `privkey.pem` ([your own certificate](authentication.md#your-own-certificate)).

## The containers

What `docker compose` starts, and how the containers find each other. `.env.example` has the values for the bundled stack.

| Variable | Read by | Default | Description |
|---|---|---|---|
| `COMPOSE_FILE` | compose | `docker-compose.yml` | The compose files to use, `:`-separated: add `reverse-proxy/compose.yml` (or `reverse-proxy/compose.app-sign-in.yml`) for [the bundled proxy](#the-bundled-https-and-sign-in-proxy) |
| `API_URL` | web | — (`http://api:5000` in `.env.example`) | Where the pages' container sends the API's paths (`/api/`, `/healthz`, `/metrics`, …). Without it they answer 404, which is fine behind the bundled proxy: nginx sends them to the API itself |
| `HEALTHCHECK_PORT` | api (health check) | `5000` | The port the API image's Docker health check asks `/healthz` on: set it with `GUNICORN_BIND` when the API listens on another port |
| `MIGRATE_ON_START` | api | `true` | The API image's entrypoint (`api/docker-entrypoint.sh`) applies the database migrations before starting; `false` skips that (run `python -m app migrate` yourself) |
| `NUXT_PUBLIC_API_BASE` | build | empty (the API is the pages' own origin) | Another address for the API, built into the pages by `bun run generate`. The published web image is built without it: setting it in `.env` changes nothing |

## Server and logging

| Variable | Read by | Default | Description |
|---|---|---|---|
| `GUNICORN_WORKERS` | gunicorn | `4` | Worker processes (Docker image): gunicorn runs uvicorn workers, each serving several requests at once |
| `GUNICORN_TIMEOUT` | gunicorn | `120` | Worker timeout in seconds; lower values are raised to 120 |
| `GUNICORN_BIND` | gunicorn | `0.0.0.0:5000` | Listen address |
| `LOG_LEVEL` | api | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`, for the app and gunicorn |
| `LOG_FORMAT` | api | `text` | `text`, or `json` (one object per line, for Loki, ELK, Splunk). Either way each line names its part (`sync`, `api`, …): see [finding things in the logs](operations.md#finding-things-in-the-logs) |
| `METRICS_ENABLED` | api | `true` | Prometheus metrics at `/metrics`; `false`: 404 |
| `FLASK_DEBUG` | dev server | `false` | Reload on code changes (development server, `python run.py`, only; the name is from the Flask days) |
| `FLASK_RUN_PORT` | dev server | `5000` | Port of the development server (e.g. when macOS AirPlay Receiver has 5000) |

## Syncing and caching

| Variable | Read by | Default | Description |
|---|---|---|---|
| `BACKGROUND_SYNC_ENABLED` | api | `true` | Run due syncs and record alert history in the background with no page open. `false`: syncs only run when pages are loaded |
| `INVENTORY_SYNC_INTERVAL_SECONDS` | api | `CACHE_TTL` | Minimum seconds between Nautobot inventory syncs |
| `LIBRENMS_SYNC_INTERVAL_SECONDS` | api | `CACHE_TTL` | Minimum seconds between LibreNMS status refreshes |
| `CACHE_TTL` | api | `300` | Seconds to cache Nautobot API responses |
| `CACHE_TYPE` | api | `SimpleCache` (`RedisCache` in `.env.example`, for the bundled Redis) | Cache backend (cachelib). `SimpleCache` is per worker; `RedisCache` shares the cache and rate limits between workers |
| `CACHE_REDIS_URL` | api | — (`redis://redis:6379/0` in `.env.example`) | Redis URL; required with `CACHE_TYPE=RedisCache` |
| `COMPRESS_RESPONSES` | api | `true` | gzip text answers over 1 KB (pages, JSON, CSS, JS, CSV) for browsers that accept it: at 1,200 sites the board's data goes from about 1.3 MB to a tenth. `false` where the proxy in front compresses instead (see [operations](operations.md#what-the-container-does)) |
| `CORS_ORIGINS` | api | — | Other sites' pages that may call the API from the browser, e.g. `https://portal.example.com` (comma-separated, or a JSON list). The app's own pages share its origin and need nothing; a sign-in proxy's cookie may come along (never with `*`) |

## LibreNMS (optional)

Set both `LIBRENMS_URL` and `LIBRENMS_API_TOKEN` to take device up/down status from LibreNMS. Each sync also caches the address LibreNMS polls for every device (`overwrite_ip` if set, otherwise `ip`); the alert board shows it when Nautobot has no primary IP for the device.

| Variable | Read by | Default | Description |
|---|---|---|---|
| `LIBRENMS_URL` | api | — | Base URL of your LibreNMS |
| `LIBRENMS_API_TOKEN` | api | — | LibreNMS API token |
| `LIBRENMS_VERIFY_SSL` | api | `true` | `true` or `false`; a company CA goes in `EXTRA_CA_PEM` |

For a LibreNMS signed by an internal CA, put the CA certificate in `EXTRA_CA_PEM` ([Nautobot](#nautobot)) and keep `LIBRENMS_VERIFY_SSL=true`; the same CA covers Nautobot and the [wireless controllers](#wireless-controllers-optional). `false` skips verification and should be a last resort, since the API token travels over that connection.

### Near-real-time status: LibreNMS alert pushes

Without pushes, a device going down shows on the board after LibreNMS has polled it **and** the next LibreNMS sync here (`LIBRENMS_SYNC_INTERVAL_SECONDS`, default 300 s). With an alert transport, LibreNMS tells the app right away, and the board shows it within seconds. The periodic sync keeps running as the safety net.

1. Create an [API token](authentication.md#api-tokens) with the `operator` role:
   `docker compose exec api python -m app token create librenms --role operator`
2. In LibreNMS: **Alerts → Alert Transports → Create**, type **API**:
   - API Method: `POST`
   - API URL: `https://<nautobot-maps>/api/v1/librenms/alert`
   - Headers: `Authorization=Bearer nmt_...` (the token)
   - Body: `{"device_id": "{{ $device_id }}"}`
3. Attach the transport to your device-down rules (e.g. "Devices up/down"), with **recovery** alerts on, so a device coming back is pushed too.
4. Use LibreNMS's **Test** button on the transport: the app answers `{"updated": true, ...}` with the device's status. If the body variable isn't filled in by your LibreNMS version, check its [alert template variables](https://docs.librenms.org/Alerting/Templates/) (`hostname` works as well as `device_id`).

What a push does: the app asks the LibreNMS API for that one device and stores its status; the push only says *which* device, so duplicate, late or non-device-down alerts can't set a wrong status. Each stored status remembers which LibreNMS request it came from, and an older answer never overwrites a newer one (from another push or from the periodic sync), so a quick down and up end in the right state; a repeated push just refreshes again. `{"updated": false}` means nothing changed: LibreNMS doesn't know the device, a newer answer was already stored, or the device was added to LibreNMS after the last sync here (the next sync picks it up) (not an error, so LibreNMS doesn't keep retrying). The parameters can also be sent as a form or in the query string (`?device_id=42`).

## Wireless controllers (optional)

Access point status from **Cisco Catalyst Center** and **HPE Aruba Networking New Central**, any number of instances of each. Access points get their address by DHCP, so a controller watches them: one it lists is monitored without a primary IP in Nautobot, shown with the address the controller reports (never written to Nautobot), and down on the board when the controller says so. A site with only access points down is at `WIRELESS_AP_LEVEL` under the board's **Wireless** chip: not an alarm, not notified, and operational on the status page with them counted (an access point with its own level set is an ordinary alarm) ([alert board](alert-board.md#access-points)). The instances, with their read-only accounts (Catalyst Center) and API clients (New Central), are listed in `WIRELESS_CONTROLLERS`, as JSON ([how .env works](#how-env-works)). Every key, the setup and how they are read: [other sources](sources.md#wireless-controllers-access-points). Check them with `python -m app wireless-check`.

| Variable | Read by | Default | Description |
|---|---|---|---|
| `WIRELESS_CONTROLLERS` | api | — | The instances, as JSON on one line in single quotes (#739; [every key](sources.md#wireless-controllers-access-points)): `'{"catalyst_center": [...], "aruba_central": [...]}'`. A mistake in it stops the app at startup, naming the instance, never a password |
| `WIRELESS_SYNC_INTERVAL_SECONDS` | api | `300` | How often each wireless controller is read (at least 60) |
| `WIRELESS_AP_LEVEL` | api | `low` | The level of a site with only access points down (`critical`, `medium` or `low`), under the **Wireless** chip, never notified. An access point with a level set in Nautobot (`DEVICE_CRITICALITY_FIELD`), or marked critical on the board, is an alarm at that level instead |

## Other monitoring tools (optional)

Prometheus Alertmanager and any tool that can call a webhook can mark devices down. See [other monitoring sources](sources.md).

| Variable | Read by | Default | Description |
|---|---|---|---|
| `ALERTMANAGER_DEVICE_LABELS` | api | `instance,hostname,host,device` | Alert labels that name the device; the first one an alert has is used (a port or URL is stripped) |
| `ALERTMANAGER_ALERTNAMES` | api | — | Only alerts with these `alertname`s; empty: every alert Alertmanager sends here |
| `WEBHOOK_ALERT_TTL_HOURS` | api | `24` | A firing alert that isn't repeated for this long stops counting, in case its "resolved" was lost. Keep Alertmanager's `repeat_interval` below it |

## Alert board

What these do is explained in [the alert board guide](alert-board.md). List settings take comma- or semicolon-separated values; in the status settings, `null` matches an empty status.

| Variable | Read by | Default | Description |
|---|---|---|---|
| `ALERT_BOARD_SITE_LOCATION_TYPE` | api | — | Location type that gets the board's rows (e.g. `Site`); devices in its child locations roll up into it. Empty: one row per location ([one row per Site](alert-board.md#one-row-per-site)) |
| `ALERT_BOARD_EXCLUDED_LOCATION_TYPES` | api | `graveyard,warehouse` | Location types hidden from the board |
| `ALERT_BOARD_EXCLUDED_LOCATION_STATUSES` | api | — | Location statuses hidden from the board |
| `ALERT_BOARD_EXCLUDED_LOCATION_TAGS` | api | — | Nautobot tag names hidden from the board |
| `ALERT_BOARD_EXCLUDED_LOCATION_NAMES` | api | — | Location names hidden from the board (fallback) |
| `ALERT_BOARD_EXCLUDED_DEVICE_STATUSES` | api | — | Device statuses ignored: not counted, not listed |
| `ALERT_MEDIUM_PERCENT` | api | `25` | More than this share of a site's monitored devices down is **Medium** ([alert levels](alert-board.md#alert-levels)) |
| `ALERT_CRITICAL_PERCENT` | api | `100` | This share or more down is **Critical** (100: only when all are down); a down core device is always Critical, and a device or tier set to a level gives at least that level |
| `COUNTRY_LOCATION_TYPE` | api | `Country` | The Nautobot location type whose locations are countries (case ignored): a site's country is the nearest one above it, before the address is looked at ([country](alert-board.md#one-row-per-site)). Changing it makes the next sync a full one |
| `SITE_TIER_FIELD` | api | — | Nautobot custom field on the location with its importance tier (e.g. `tier`) ([importance](alert-board.md#importance-tier)) |
| `TENANT_SCOPE_FIELD` | api | — | Nautobot JSON custom field on each Location mapping tenant names to device roles counted on that tenant's status page; missing tenant entries count all devices, and no role matches hides that site ([tenant status page](status-page.md#per-site-device-roles)) |
| `STATUS_PAGE_TENANT_DEVICES` | api | `false` | On a tenant's [status page](status-page.md#the-tenants-own-devices), list by name each monitored device whose Tenant in Nautobot is that customer: up, down (and since when) or in maintenance. Off: counts only. Turning it on names every device that already has a tenant |
| `SITE_TIERS` | api | `gold,silver,bronze` | The tier values, most important first; other values are no tier |
| `TIER_DEVICE_LEVEL` | api | — | What any down device makes a site of that tier, e.g. `gold:critical,silver:medium`; a device's own field overrides it ([device and site criticality](alert-board.md#device-and-site-criticality)) |
| `DEVICE_CRITICALITY_FIELD` | api | — | Nautobot custom field on the device (a select: `critical`, `medium`, `low`): what that device makes its site when down; it wins over the tier, the overrides and the role keywords. Setting it makes the next sync a full one |
| `CASE_URL_TEMPLATE` | api | — | Makes every case number a link to its ticket, e.g. `https://itsm.example.com/ticket/{case}` (`{case}` is the URL-encoded number; only `http(s)://`): on the board, the wall, the + Case and History panels, the map's site panel, Copy, notifications and the incidents CSV ([links to tickets](alert-board.md)) |
| `AVAILABILITY_BASIS` | api | `any_down` | What the [availability report](availability.md) counts as downtime: `any_down` (any monitored device of the site down) or `critical` (the site at Critical) |
| `AVAILABILITY_TARGET_PCT` | api | `99.9` | The availability target (the SLA), in percent: a site or tenant below it is red on [Availability](availability.md) and counted in **Below 99.9 %**, and the CSV header's `target_pct`. Above 0 and below 100 (e.g. `99.5`, `99.95`), else the app doesn't start. One target for every tenant |
| `FLAP_CHANGES` | api | `4` | A device that goes up or down this many times within 30 minutes is flapping: one incident, a FLAPPING label, a steady site level ([flapping](alert-board.md#flapping)). `0`: off |
| `ALERT_DELAY_SECONDS` | api | `0` | A device must be down this long before it alarms; shorter blips are not shown, counted, recorded or notified ([alert delay](alert-board.md#alert-delay)) |
| `CRITICAL_ROLE_KEYWORDS` | api | `core,spine,distribution,router,gateway` | Device role keywords that make a down device **Critical** |
| `CRITICALITY_RULES` | api | — | Keywords per location type, as JSON on one line in single quotes (#739), overriding `CRITICAL_ROLE_KEYWORDS`: `'{"default": ["core", "router"], "datacenter": ["core", "firewall"]}'` (a fuller example in `.env.example`). Not JSON, or not that shape: the app stops at startup |
| `ACCESS_POINT_ROLES` | api | `Access Point` | Nautobot device roles (comma-separated, capitals don't matter) that are access points: Availability shows each site's "12 network · 40 APs", every other monitored device counting as network equipment ([availability](availability.md)); the [status page](status-page.md) counts them too, and how many are down. A device a [wireless controller](sources.md#wireless-controllers-access-points) lists is an access point too; one of these roles is also matched by name when its serial isn't in the controller |
| `SITE_TENANT_RELATIONSHIPS` | api | — | Nautobot Relationships (keys or labels) that link a location to more tenants, shown next to its own tenant. Empty: every Location ↔ Tenant relationship |

## Notifications

See [notifications](notifications.md). Each channel is on when its setting is filled in.

| Variable | Read by | Default | Description |
|---|---|---|---|
| `NOTIFY_MIN_LEVEL` | api | `critical` | `low`, `medium` or `critical`: notify when a site reaches it (or gets worse above it) and when it drops below it |
| `NOTIFY_MIN_LEVEL_BY_TIER` | api | — | `NOTIFY_MIN_LEVEL` for some importance tiers, e.g. `gold:low,silver:medium` |
| `NOTIFY_TENANTS` | api | — | Only sites with one of these tenants (comma-separated) |
| `NOTIFY_BOARD_URL` | api | — | The app's address, for the link in messages, e.g. `https://nautobot-maps.example.com` |
| `NOTIFY_SUMMARY_THRESHOLD` | api | `5` | More messages due at once on a channel go out as one summary |
| `NOTIFY_WEBHOOK_URL` | api | — | Webhook: JSON `POST` to this URL |
| `NOTIFY_WEBHOOK_SECRET` | api | — | Signs the webhook body (`X-Nautobot-Maps-Signature: sha256=…`) |
| `NOTIFY_TEAMS_WEBHOOK_URL` | api | — | Microsoft Teams Workflows webhook URL |
| `NOTIFY_EMAIL_TO` | api | — | Your own email recipients (comma-separated); needs `SMTP_HOST`. Customers' addresses are set on their status tokens ([customer mails](notifications.md#customers)) |
| `SMTP_HOST` / `SMTP_PORT` | api | — / `587` | Mail server; also turns on [customer mails](notifications.md#customers) |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | api | — | Login; none for an internal relay |
| `SMTP_FROM` | api | `SMTP_USERNAME`, else `nautobot-maps@localhost` | Sender address |
| `SMTP_STARTTLS` | api | `true` | Encrypt the connection with STARTTLS |

## Map and address search

| Variable | Read by | Default | Description |
|---|---|---|---|
| `MAP_TILE_URL` | api | OpenStreetMap | Tile URL template (`{s}`, `{z}`, `{x}`, `{y}`), e.g. an internal tile server |
| `MAP_TILE_ATTRIBUTION` | api | OpenStreetMap | Attribution shown on the map (HTML allowed) |
| `MAP_DEFAULT_VIEW` | api | empty | The map's first view as `lat,lon,zoom` (zoom 1–19), e.g. `55.7,12.6,6` for your region. Empty: the map fits itself to every site with coordinates (not 0,0, which is a site without them). Set it when that fit is too wide: sites on both sides of the 180° meridian, or one with wrong coordinates. A wrong value is ignored with a warning in the log |
| `GEOCODER_ENABLED` | api | `true` | `false`: address search off, only `lat,lon` searches; nothing is sent to a geocoder |
| `GEOCODER_URL` | api | `https://nominatim.openstreetmap.org` | Nominatim-compatible geocoder |
| `GEOCODER_USER_AGENT` | api | `nautobot-maps (+repo URL)` | User agent sent to the geocoder (the public Nominatim requires one that identifies you) |

By default viewers' browsers load tiles from `tile.openstreetmap.org`, and the app sends address searches to the public [Nominatim](https://nominatim.org/). On closed networks, or to keep that data inside, point both settings at internal services or turn address search off. Geocoding results are cached for a day, and the geocoder is asked at most once a second (a busy search gets "try again in a second"), as the public service's usage policy requires.

## MCP server

See [MCP server](mcp.md).

| Variable | Read by | Default | Description |
|---|---|---|---|
| `MCP_ENABLED` | api | `false` | Serve the MCP server at `/mcp`; off: 404 |
| `MCP_ALLOWED_ORIGINS` | api | — | Browser origins (`https://host[:port]`) allowed to call `/mcp`. MCP clients send no `Origin`; any request that does is refused unless listed |

## Not in .env: development, tests, demo and CI

These are set in the shell for one command, never in `.env`; nothing in production reads them.

| Variable | Read by | What it is | More |
|---|---|---|---|
| `DATABASE_URL` / `SQL_ECHO` / `WEB_BUILD_DIR` | dev server | The development server's database (when `NAUTOBOT_MAPS_DATABASE_URL` is empty), its SQL log, and where `bun run generate` put the pages | [api/README](../api/README.md) |
| `API_TEST_DATABASE_URL` | `api/tests/conftest.py` | The tests' database (default `fastapi_nuxt_test`, created when missing) | [CONTRIBUTING](../CONTRIBUTING.md) |
| `NAUTOBOT_LIVE_URL` / `NAUTOBOT_LIVE_TOKEN` | `api/tests/test_nautobot_live.py` | A real Nautobot to test against; unset: skipped | [development/README](../development/README.md) |
| `AD_LIVE_URL` / `AD_LIVE_CA_FILE` | `api/tests/test_ad_live.py` | A real domain controller (the development stack's Samba) and its CA; unset: skipped | [development/README](../development/README.md) |
| `E2E_BASE_URL` / `E2E_OPERATOR_TOKEN` / `E2E_ADMIN_TOKEN` / `E2E_NOC_TOKEN` / `E2E_CUSTOMER_TOKEN` | `e2e/test_e2e.py` | The running app the browser tests open, and an API token for each role they sign in as | [CONTRIBUTING](../CONTRIBUTING.md) |
| `E2E_AD_BASE_URL` | `e2e/test_ad_sign_in.py` | The app with sign-in with AD, for its browser test | [CONTRIBUTING](../CONTRIBUTING.md) |
| `MOCK_SCALE` / `MOCK_AP_DHCP` | `demo/mock_nautobot.py` | The demo's fake Nautobot: `MOCK_SCALE=1200` adds that many generated sites; `MOCK_AP_DHCP=1` drops the access points' primary IPs | [demo/README](../demo/README.md) |
| `NAUTOBOT_URL` / `NAUTOBOT_TOKEN` / `NAUTOBOT_VERIFY_SSL` | `development/seed_nautobot.py` | The development stack's Nautobot to fill with test data (the app's own names, defaults for that stack) | [development/README](../development/README.md) |
| `STATUS_LINK` / `STATUS_TOKEN` / `OIDC_LOGOUT_URL` | `reverse-proxy/ci/smoke.py` | CI's check of the bundled proxy: a status page link and its key, and the logout address Sign out must go to | the script's docstring |
| `SESSION` | `reverse-proxy/ci/smoke_app.py` | CI's check of the app's own sign-in behind nginx: a signed-in session cookie | the script's docstring |

Nautobot's own containers in `development/` (Nautobot, its database and Redis, Samba) keep their settings in `development/docker-compose.yml`; the app's containers there read `development/maps.env`.

