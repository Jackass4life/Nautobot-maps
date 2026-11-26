# Code guide

A tour of the code for a developer who is new to the project: what the parts are, where to find things, and how the main features work from start to finish. It is meant to be read once from top to bottom, then used as a map.

To set up a development environment and run the tests, see [Contributing](../CONTRIBUTING.md). For what the app does for its users, see [How it works](how-it-works.md).

## In short

- **Three parts, three folders:** `api/` is the server (Python, FastAPI), `web/` is the pages people see (TypeScript, Vue, Nuxt), and `reverse-proxy/` is an optional nginx in front that adds HTTPS and company sign-in.
- **Nautobot is the source of truth, and the app never writes to it.** On a timer, the app copies Nautobot's sites and devices (and LibreNMS's up/down status) into its own PostgreSQL database. The pages read from that copy, so they stay fast even with 20,000 devices.
- **The alert board is computed, not entered.** After each sync, the app works out every site's alert level from its devices and stores the result. The board, the map, notifications and the status pages all read that one result.
- **The browser only talks to the API,** through a typed client generated from the API's own description (OpenAPI). If the API changes and the types are not regenerated, CI fails.
- **Every rule has a test.** About 1,750 Python tests, Vitest tests for the pages' logic, and Playwright tests that click through the real pages in Chromium.

## The big picture

```
 Browser ──HTTPS──► reverse-proxy/ (nginx + oauth2-proxy, optional)
                        │  signs people in, adds X-Forwarded-User / -Groups
                        ├──────────────► web/  (Nuxt on bun, port 3000)
                        │                  the page shell and its JS/CSS files
                        └──────────────► api/  (FastAPI under gunicorn, port 5000)
                                           │  /api/v1/…  /healthz  /metrics  /mcp
                                           ├──► PostgreSQL  (the copy of Nautobot, alerts, cases, tokens…)
                                           ├──► Redis       (the response cache, shared by the workers)
                                           └──► Nautobot, LibreNMS, wireless controllers (read only)
```

Without the bundled proxy, `web` answers on port 5000 itself and passes every API path on to `api`, so the browser still sees one address.

Two kinds of work happen in the API:

1. **Answering requests.** A page asks `/api/v1/alerts`, and the API reads the stored board and answers it.
2. **Background work.** The **scheduler** wakes up every few seconds (at most 30 apart). It syncs from Nautobot and LibreNMS when they are due, rebuilds the board when something changed, and sends notifications. Only one process does this at a time; a PostgreSQL *advisory lock* makes sure of that.

## Where to find what

| Folder | What is in it |
|---|---|
| `api/app/` | The API's code (details [below](#the-api-module-by-module)) |
| `api/alembic/` | Database migrations: one file per schema change, in `api/alembic/versions/` |
| `api/tests/` | pytest tests; `conftest.py` has the shared fixtures, `db.py` the database helpers |
| `api/run.py` | The development server (`uv run python run.py`) |
| `api/docker-entrypoint.sh` | What the API container runs: migrate the database, then start gunicorn |
| `api/scripts/generate_openapi.py` | Writes the API's description to `web/api/openapi.json` |
| `web/pages/` | One file per page; the file name is the address (`alerts.vue` is `/alerts`) |
| `web/layouts/` | `default.vue`: the top bar, mounted once, with the page under it |
| `web/components/` | Pieces of pages (`AlertBoard.vue`, `SitePanel.vue`, …) |
| `web/utils/` | Plain TypeScript functions with the pages' logic, tested in `web/tests/` |
| `web/composables/` | Shared state: `usePageConfig` (who is signed in, settings), `useCan` (permissions), `useTheme` |
| `web/api/` | The generated API description and types; never edit them by hand |
| `web/server/` | The small server in the `web` image: security headers, and passing API paths on |
| `reverse-proxy/` | The nginx image: `nginx.conf` (the whole configuration) with its includes, `substitute.py` (fills in the build args), `entrypoint/` (start checks), `ci/` (its tests) |
| `e2e/` | Playwright tests that use the real pages in Chromium |
| `demo/` | A fake Nautobot (`mock_nautobot.py`) to run the app without a real one |
| `development/` | A real Nautobot 3.x (and a test Active Directory) in Docker, for integration tests |
| `scripts/ci/` | `scripts/ci/test.sh`: the API, page and browser tests as CI runs them, on GitHub, GitLab or your machine |
| `docs/` | These guides; the app shows them under **Documentation** |

## The API, module by module

Everything below is in `api/app/`.

### How a request travels through the API

`app/api.py` builds the app. A request passes through these layers, outermost first:

1. **Access log** (`app/logs/middleware.py`): one log line per request.
2. **HEAD as GET** (`app/v1/deps.py`, `HeadAsGet`).
3. **Security headers and gzip** (`app/frontend/middleware.py`): the Content-Security-Policy and friends on every answer, errors included.
4. **Old paths** (`app/v1/legacy_paths.py`): `/api/alerts` answers a redirect to `/api/v1/alerts`.
5. **CORS** (`app/core/cors.py`), only when `CORS_ORIGINS` is set.
6. **Database session** (`app/db/middleware.py`): one database session per `/api/v1` request. It is committed when the answer is a success and rolled back otherwise.
7. **The route.** Before it runs, `deps.ROUTER_DEPENDENCIES` checks who is calling and whether they may (see [sign-in](#sign-in-and-permissions)).

`app/lifespan.py` runs once when a worker starts: it sets up the cache, loads the roles from the database and starts the scheduler. At shutdown it stops them again.

### `core/`: settings, errors, version

- `core/settings.py` is **the only place that reads environment variables.** Each setting is a field on `Settings`, named exactly like its environment variable (`NAUTOBOT_URL: str`, #760), with its type and checks, and no default (#760: every setting is in `.env`; `api/local.env` has them all for development). They are read once, into one shared object: code imports it with `from app.core.settings import settings` and reads `settings.NAUTOBOT_URL`. Always **inside the function**, never at import (`TIMEOUT = settings.X` at the top of a module), so a test that changes a setting reaches the code: the `override_settings` fixture (`override_settings(NAUTOBOT_URL="")`), `with overridden(...)` from `api/tests/overrides.py`, or `configure(NAUTOBOT_URL="")` through the environment.
- `core/exceptions.py` turns errors into answers. Every refusal is `{"error": "<one plain sentence>"}` with a status code.
- `core/version.py` reads the version from `api/pyproject.toml`.

### `db/` and `models/`: the database

- `models/` describes every table as an SQLAlchemy class, one file per area (`alerts.py`, `inventory.py`, `access.py` for tokens, `identity.py` for roles and identities, …). `models/__init__.py` imports them all so Alembic sees every table.
- `api/alembic/` holds the migrations. To change the schema, change a model, then run `uv run alembic revision --autogenerate -m "what"` and check the file it writes. `app/migrate.py` applies them (`python -m app migrate`); the container does this on every start.
- `db/session.py` creates the database engine (asyncpg) and has helpers for code that runs outside a request:
  - `spawn` runs background work as a task.
  - `run_sync` runs blocking code in a worker thread.
  - `from_sync` lets that thread call back into async code.
- `db/aconnection.py` is a thin layer for raw SQL: `await conn.execute(sql, params)`, `conn.transaction()`, and `try_advisory_lock(name)` for "only one process at a time" work.
- `repositories/` keeps the SQL for one area in one place. Examples: `board_repository.py` (the alert board's tables) and `token_repository.py` (API tokens).

Everything is **async**: a function that talks to the database is `async def` and is called with `await`. The upstream HTTP clients are the exception. They are blocking, so the services run them in threads (`asyncio.to_thread`).

### `v1/`: the routes

The API is split into **sections**, one per page of the app, plus a few that no page owns. `v1/sections.py` lists them. Each section is a folder:

| Folder | Page or purpose |
|---|---|
| `v1/map/` | The map: locations, a site's detail, search, coordinate check |
| `v1/alert_board/` | The board: alerts, acknowledgements, cases, history, the feed, criticality overrides |
| `v1/availability/` | Uptime per site and tenant, and its CSV |
| `v1/maintenance/` | Maintenance windows |
| `v1/circuits/` | Circuit mapping |
| `v1/status_page/` | A tenant's status page and its links |
| `v1/administration/` | The Admin page: tokens, identities, roles, the data contract, test notification |
| `v1/documentation/` | These guides (`/help`) and the API explorer (`/api/docs`) |
| `v1/auth/` | Who am I (`/api/v1/me`), and the app's own sign-in with AD |
| `v1/incoming_alerts/` | What other tools push in: LibreNMS, Alertmanager, a generic webhook |
| `v1/shared/` | What every page reads: `page-config` and `app-status` |
| `v1/monitoring/` | `/healthz`, `/metrics`, `/mcp` |
| `v1/examples/` | A small, complete example (`items`) of the layout, for reference |

Each section folder has two files:

- `controller.py` holds the **routes**: small functions that read the request, call a service and return the answer. Each route says the permission it needs with `Depends(deps.require_permission(...))`.
- `dtos.py` holds the **shapes** of requests and answers as Pydantic models (DTO = data transfer object). The routes' `response_model` points at them. They become the OpenAPI description, and from that the TypeScript types in `web/`.

`v1/deps.py` has what all routes share: `Json` (how answers are written), `ApiError` (a refusal), `AppRoute` (names each route's `operation_id` and takes its summary from the docstring) and the permission dependency. `api/tests/test_sections.py` checks that every route follows this layout.

### `services/`: the logic

The real work happens here, one module per job. The big ones:

| Module | What it does |
|---|---|
| `inventory_service.py` | **Sync.** Fetches locations, devices, tenants, circuits and cables from Nautobot (and devices from LibreNMS), normalises them and writes them to the `nautobot_*_cache` tables. Entry point: `ensure_snapshot()` |
| `alerts_service.py` | **The alert board.** Works out each device's state and each site's level, finds the likely cause, records alert history and builds the board. Entry point: `board()`; the heavy lifting is in `build_alert_board_payload()` |
| `scheduler.py` | **Background work.** `tick()` sends notifications, syncs what is due, rebuilds the board when something changed. `board_changed()` is called after a case, acknowledgement or maintenance change |
| `notify_service.py` | **Notifications.** Queues a message when a site's level crosses the threshold, and sends it by webhook, Teams or email. Customers get their own mails |
| `maintenance_service.py` | Maintenance windows: planned work is not an alarm |
| `availability_service.py` | Uptime per site, tenant and period, from the recorded alert history |
| `status_service.py` | A tenant's status page: only what a customer may see |
| `webhooks_service.py` | Device status from Alertmanager and generic webhooks |
| `wireless_service.py` | Access point status from Cisco Catalyst Center and Aruba New Central |
| `neighbours_service.py` | Which switch and port each access point is on |
| `circuits_service.py` | Circuit mapping: circuits without a termination, matched by address |
| `auth_service.py` | **Who is calling and what they may do** (see [sign-in](#sign-in-and-permissions)) |
| `token_service.py` | API tokens: create, check, revoke |
| `identity_service.py`, `role_service.py` | Identities (people the app knows) and roles with their scopes |
| `ad_service.py`, `login_service.py` | The app's own sign-in against Active Directory (through the company's `sit_core`), and its signed token |
| `status_link_service.py` | Each tenant's private status page link |
| `mcp_service.py` | The MCP server: the board as tools for AI assistants |
| `metrics_service.py`, `health_service.py` | `/metrics` for Prometheus, `/healthz` for probes |
| `contract_service.py` | The data contract: what the app reads from Nautobot and checks that it is there |
| `guide_service.py` | Turns these Markdown guides into the `/help` pages |

### `clients/`: talking to other systems

- `nautobot.py`: Nautobot's REST API. **Read only**: it never writes to Nautobot.
- `librenms.py`: LibreNMS's API.
- `catalyst.py` and `arubacentral.py`: the wireless controllers.
- `http.py`: the shared HTTP client with retries and certificate settings.
- `caching.py`: the response cache (Redis in Docker, in memory in development).

### `auth/`: permissions

- `auth/permissions.py` is **the one place where a role's rights are written down**. Each permission is a *scope* such as `alerts:ack` or `page:board`; a role holds a set of scopes.
- `auth/scopes.py` matches scopes. `*` stands for any one part, so `alerts:*` covers `alerts:ack`.
- `auth/dependencies.py` has the FastAPI dependencies the routes use.

### Other bits

- `__main__.py`: the command line, `python -m app …` (`migrate`, `token create`, `identity …`, `role …`, `status-link`, `wireless-check`, …). `check_env.py`: `python -m app.check_env`, a check of a `.env` file (its own command: it must run when the settings in the environment are broken).
- `logs/`: logging. Each line names its part (`[sync]`, `[api]`, …) so you can filter by it (`logs/parts.py`).
- `context/request.py`: the current request, for code deep below a route that needs a header.
- `frontend/`: in development, serves the built pages and adds the security headers.
- `utils/timeutil.py`: **the** way to get "now" (`timeutil.iso_utc_now()`), so tests can freeze time.

## How the main features work

### From Nautobot to the alert board

1. **Sync.** The scheduler's `tick()` calls `inventory.ensure_snapshot()`. When the interval has passed, it fetches from Nautobot and LibreNMS and writes the cache tables in one transaction. A *watermark* (the time of the last successful sync) means it fetches only what changed since then; once a day a full sync fetches everything, to catch what was deleted.
2. **Build.** If the sync changed anything, `scheduler.rebuild_board()` calls `alerts.board(rebuild=True)`, which runs `build_alert_board_payload()`. For every site it:
   - takes its devices from the cache and their state from LibreNMS, webhooks and the wireless controllers;
   - leaves out what is not monitored, muted, or in a maintenance window;
   - computes the site's level (critical, medium, low) from how many devices are down, which ones (core devices count more) and the site's tier;
   - records changes in alert history (`alert_instances`, `alert_events`, `site_level_changes`).
3. **Store.** The finished board goes in the cache, and every API worker serves that same copy. Only one build runs at a time; a worker that finds one running waits and takes its result.
4. **Read.** The board page calls `GET /api/v1/alerts` (`v1/alert_board/controller.py`), which returns the stored board.

A LibreNMS alert can arrive sooner than the next sync. LibreNMS calls `POST /api/v1/librenms/alert` (`v1/incoming_alerts/`), the app saves it and rebuilds the board straight away. The tick catches any rebuild that lost the lock.

When someone acknowledges an alarm, adds a case or starts maintenance, the route saves it and calls `scheduler.board_changed()`. That drops the stored board and rebuilds it in the background.

### Notifications

1. While building the board, `alerts_service` notices when a site's level changes. In the same database transaction, `notify_service.enqueue_change()` writes one row per channel to the `notification_outbox` table. Writing the row together with the change means a crash can't lose a notification.
2. Customer mails (`customer_email`) are queued for each address on the tenant's status tokens. Their content is the tenant's status page entry for the site (`status_service.tenant_site()`, the page's own function, #763), so a mail never says more than the page. Each one waits its token's `notify_after_minutes`, and is cancelled if the site comes back first.
3. On each tick, `scheduler.send_notifications()` calls `notify_service.send_pending()`. It takes the due rows (`FOR UPDATE SKIP LOCKED`, so two senders never take the same row), sends them, and marks them sent, or retries later with a growing delay.

### Sign-in and permissions

Every route runs `auth_service.check_request()` before its own code. It works out **who** is calling:

- With the bundled proxy (`AUTH_MODE=header`), nginx and oauth2-proxy sign the person in and send `X-Forwarded-User` and `X-Forwarded-Groups`. The app believes these headers only from a trusted proxy address (`AUTH_TRUSTED_PROXIES`) carrying the shared secret (`AUTH_PROXY_SECRET`).
- With an API token (`Authorization: Bearer nmt_…`), `token_service` looks the token up. A token has exactly its identity's rights.
- With the app's own sign-in (`AD_HOST` set), the person signs in on `/auth` against Active Directory and gets a signed cookie (`login_service`).
- With `AUTH_MODE=disabled`, nobody is signed in: reading is open, changes are not.

Then it works out **what they may do**. An identity (managed on the Admin page) gives roles and scopes directly; otherwise the person's groups map to a role (`AUTH_*_GROUPS`). The route's required permission is checked against those scopes. Writes from another website are refused (a CSRF check).

The pages hide buttons a person can't use (`useCan` in `web/`), but that is only for convenience: **the API is the real check.**

### A customer's status page

1. Staff press **Share…** on Availability or Customers. The app creates a status link (`/status/<random id>`) and a status token for that tenant. The token's key goes after `#key=` in the link, a part of the address the browser never sends to servers or logs.
2. The customer opens the link. The page (`web/pages/status/[id].vue`) reads the key from the address and calls `GET /api/v1/status/<id>` with it.
3. `auth_service.status_access()` checks that the key belongs to that tenant. `status_service.tenant_status()` builds the answer from the stored board, leaving out everything a customer must not see: device names, addresses, cases, other tenants.

## The web pages

`web/` is a Nuxt 4 app written in Vue and TypeScript, built with bun. It runs in the browser only (`ssr: false`): the server sends the same empty page shell for every address, and the JavaScript draws the page.

- **Pages** are in `web/pages/`. The file name is the address: `alerts.vue` is `/alerts`, `tenants/[name].vue` is `/tenants/<name>`.
- **The top bar** (`components/AppBar.vue`) is in `layouts/default.vue`, so it is drawn once and stays while you move between pages. Its ☰ menu lists the pages from `utils/navigation.ts`, which is in the web code, so the menu is there at once; when page-config says what the user may open, the other pages are hidden. The links open pages inside the app, without reloading it, so a page's code doesn't end when you leave it: work that waits (a fetch in `onMounted`) must stop once the page is gone, and timers and listeners are removed in `onBeforeUnmount` (see `pages/index.vue`'s `gone`, #743). The wall, the sign-in page and a customer's status page have no bar.
- **Components** in `web/components/` are the reusable pieces. The UI kit is [Nuxt UI](https://ui.nuxt.com) (`<UButton>`, `<UTable>`, …), with a few [shadcn-vue](https://www.shadcn-vue.com) components in `components/ui/` (named `<S…>`).
- **Logic** lives in `web/utils/` as plain functions (for example `utils/board.ts`, `utils/availability.ts`), so Vitest can test it without a browser.
- **The API client:** `web/plugins/api.ts` creates `$api`, a typed client. A call looks like `await unwrap($api.GET("/api/v1/alerts"))`. Because the types come from the API's own description, a wrong path or field is a type error.
- **Page config:** `usePageConfig()` fetches `/api/v1/page-config` once per page load. It says who is signed in, what they may do, and the settings the pages need.
- **Workspaces:** `web/middleware/workspace.global.ts` sends each role to its own start page (`/noc`, `/nwo`, `/customer`, …).
- **Security:** the Content-Security-Policy allows no inline scripts or styles. `web/server/plugins/security.ts` sets the same headers as the API, and a test checks that they match.

After changing the API, regenerate the types: `uv run python scripts/generate_openapi.py` in `api/`, then `bun run api:generate` in `web/`.

## The reverse proxy

`reverse-proxy/` builds an nginx image. At build time `substitute.py` fills in the build args (`DOMAIN`, the ports, …) and `nginx -t` checks the result; at start, nginx fills in the secrets (`AUTH_PROXY_SECRET`, …). The certificate is mounted at start, never in the image.

- `nginx.conf` is the whole configuration and decides which paths need sign-in. The status page and its files don't, because customers have no company login.
- `proxy-to-api.inc` and `proxy-to-web.inc` pass requests on, with the signed-in user's headers.
- `sign-in/` has two variants: oauth2-proxy (company sign-in such as GitLab or Entra ID), or the app's own sign-in.
- `entrypoint/` scripts refuse to start without a certificate or required settings, with a clear log line.
- `ci/smoke.py` tests the real nginx against a stub sign-in.
- `listener.py` (started with `start_listener.sh`) prints every request it gets, secrets masked: put it where the API would be to see what nginx sends.

## Tests

| Where | What | Run it |
|---|---|---|
| `api/tests/` | The API's rules, with a real PostgreSQL | `uv run pytest` in `api/` |
| `web/tests/` | The pages' logic (`web/utils/`) | `bun run test` in `web/` |
| `e2e/` | The real pages in Chromium, against the fake Nautobot | `scripts/ci/test.sh e2e`, or see [Contributing](../CONTRIBUTING.md) |
| `reverse-proxy/ci/` | The proxy with real nginx | in CI |

A few rules the code follows, which the tests rely on:

- **Call across modules as `module.function()`,** never `from module import function`. Tests replace a function on its module (`monkeypatch.setattr(inventory, "fetch_tenants", fake)`), and that only works if callers look it up there.
- **Read settings when needed** (`settings.NAME` in the function), not copied at import. `tests/test_layout.py` fails on a read at import.
- **Get the time from `timeutil`,** so tests can set it.
- **Importing a module must not touch the database.**
- Helpers for database tests are in `api/tests/db.py`. For example, `adb.run(fn, …)` runs an async function with a session and commits.

## "I want to…"

**…add a setting.**
1. Add a field to `Settings` in `api/app/core/settings.py`, with a comment.
2. Document it in `docs/configuration.md` and `.env.example`. The compose files pass nothing themselves: every container reads `.env`. A name another part reads (compose, nginx, the web container) goes in `OTHER_PARTS` in `api/app/core/env_names.py` too, or the API warns that nothing reads it.

Add it to `api/local.env` too: the tests start from it, and the API has no defaults.

**…add an API route.**
1. Pick the section folder in `api/app/v1/`.
2. Add the request and answer models to its `dtos.py`.
3. Add the route to `controller.py` with the permission it needs, and put the logic in a service.
4. Regenerate the OpenAPI file and the TypeScript types.
5. Add tests in `api/tests/`.

**…change the database.**
1. Change the model in `api/app/models/`.
2. Run `uv run alembic revision --autogenerate -m "what"` in `api/` and read the new file in `api/alembic/versions/`.
3. Run `uv run python -m app migrate`, then `uv run alembic check`.

**…add or change a page.**
1. Put the page in `web/pages/` and its pieces in `web/components/`. For a menu entry, add it to `web/utils/navigation.ts` and to `PAGES` in `api/app/services/navigation_service.py` (a test checks they match), with its permission in `api/app/auth/permissions.py`.
2. Put the logic in `web/utils/` with a test in `web/tests/`.
3. Look at it in Chromium in light and dark mode and at phone width.
4. Add an e2e test for the main path.

**…see where a route's data comes from.** Open the controller in its section, follow the service it calls, then the repository or client that service uses.

**…run it locally.** Start the fake Nautobot (`uv run --project api python demo/mock_nautobot.py`), migrate a database, and run `uv run python run.py` in `api/` with `NAUTOBOT_URL=http://127.0.0.1:8080`. [Contributing](../CONTRIBUTING.md) has the full steps.

## Words used in the code

| Word | Meaning |
|---|---|
| **Location / site** | A place from Nautobot. The board shows one row per site; a building inside a site can roll up to it (`ALERT_BOARD_SITE_LOCATION_TYPE`) |
| **Tenant** | A customer, from Nautobot. Sites and devices belong to tenants |
| **Snapshot / cache tables** | The app's copy of Nautobot in PostgreSQL (`nautobot_*_cache`) |
| **Sync** | Copying Nautobot and LibreNMS into the snapshot |
| **Alert level** | A site's state on the board: critical, medium, low, or OK |
| **Tier** | How important a site is (a Nautobot custom field); it changes how serious an outage counts |
| **Criticality** | How important a device is; a core device down counts more |
| **Alert instance / event** | One outage of a device, and the things that happened to it (down, up, acknowledged) |
| **Case** | A ticket number someone added to an alarm |
| **Maintenance window** | Planned work. Devices in one are not alarms |
| **Outbox** | The `notification_outbox` table: messages waiting to be sent |
| **Token / key** | An API token (`nmt_…`). A *status token* opens one tenant's status page |
| **Identity** | A person the app knows by user name, with roles and scopes |
| **Role / scope** | A role (admin, operator/NWO, NOC, viewer, customer) is a set of scopes; a scope (`alerts:ack`) is one permission |
| **Workspace** | A role's own set of pages, under its own path (`/noc`, `/nwo`, `/customer`) |
| **Advisory lock** | A PostgreSQL lock by name, used so only one process syncs or builds at a time |
| **DTO** | A request or answer shape (`dtos.py`) |
| **Data contract** | The list of what the app reads from each source and expects to be there |
