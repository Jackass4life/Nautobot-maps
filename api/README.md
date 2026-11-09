# api/

The app (#572, the image since #613): FastAPI, managed with [uv](https://docs.astral.sh/uv/),
PostgreSQL. It serves `web/`'s built pages; the only thing it shares with `web/` is
`openapi.json`.

## Commands (run from `api/`)

```bash
cp .env.example .env                  # DATABASE_URL=postgresql+psycopg://...
uv sync
uv run alembic upgrade head           # apply migrations
uv run python -m app.serve            # http://127.0.0.1:5000 (FLASK_RUN_PORT), docs at /api/docs; FLASK_DEBUG=true reloads
uv run python -m app.export_openapi   # write openapi.json (a test fails if stale)
uv run pytest                         # API_TEST_DATABASE_URL (default fastapi_nuxt_test): a separate DB, reset by the tests
uv run ruff check . && uv run ruff format --check .
```

Dependencies: `uv add <pkg>` / `uv add --dev <pkg>` (never pip). Commit `uv.lock`.

## Layout (`app/`)

| Path | What |
|---|---|
| `main.py` | App, middleware, routers under `/api/v1`, spec at `/api/openapi.json` |
| `v1/<resource>/controller.py` | `APIRouter`: HTTP only; DTOs in, DTOs out |
| `v1/<resource>/dtos.py` | Pydantic request/response models |
| `services/<resource>_service.py` | Business rules; ORM ↔ DTO conversion |
| `repositories/<resource>_repository.py` | All queries; takes a session, returns ORM objects |
| `models/` | SQLAlchemy models; `Base` in `models/base.py`, every model imported in `models/__init__.py` |
| `session/__init__.py` | Engine, sessionmaker, `DbSessionMiddleware`, `DbSession` dependency |
| `utils/` | Settings (`config.py`), domain errors + handlers (`errors.py`) |

Layering is strict: `controller → service → repository → model`, no skipping.

`DbSessionMiddleware` opens one session per `/api/v1` request, commits when the
response status is < 400, otherwise rolls back, and always closes it.

## Adding things

- **Model:** add it to `app/models/__init__.py`, then
  `uv run alembic revision --autogenerate -m "..."` and review the generated file.
- **Endpoint:** set an explicit `response_model` and a camelCase `operation_id`
  (it becomes the name in the generated TypeScript), document error responses with
  `ERROR_RESPONSES`, then re-export the spec and run `npm run generate:api` in `web/`.

## Settings (#588)

Every setting of the first app, same names and meaning: see
[docs/configuration.md](../docs/configuration.md). Read with `get_settings().name`
(`app/utils/config.py`, the only module that reads the environment); an empty value means
the default. Only the new stack's own:

| Setting | Default | Meaning |
|---|---|---|
| `NAUTOBOT_MAPS_DATABASE_URL` / `DATABASE_URL` | local `app` database | PostgreSQL URL; the first wins. `postgresql://` URLs get the psycopg driver. |
| `DATABASE_PASSWORD` | — | The password on its own, no URL encoding; replaces the URL's. |
| `CORS_ORIGINS` | — | Other sites' pages that may call the API (JSON list or comma-separated); the app's own pages share its origin. |
| `WEB_BUILD_DIR` | `../web/.output/public` | `web/`'s built pages; the image sets it. |
| `SQL_ECHO` | `false` | Log every SQL statement (development). |

## Database (#590)

The schema is the first app's (version 33 of `nautobot_maps/db.py`): `app/models/` has
its 27 tables as they are, and the first Alembic revision creates exactly that
(`tests/schema_v33.json` is the reference; `tests/test_schema.py` compares column by column).

`python -m app.migrate` (the container runs it at start) brings any database to the newest schema:

- **The first app's database** (`schema_migrations` at version 33): stamped at the baseline
  revision, its data kept, then upgraded. The first app can still start on it until the
  schema changes.
- **Empty, or Alembic's already:** upgraded.
- **Refused, with what to do:** the first app's schema below or above 33, or its tables
  without a migration record. Below 33: start the first app's last release (an image before
  #613) once on it, then this one.

Both apps take the same PostgreSQL lock while migrating.

## Command line (#611)

The first app's commands, as `python -m app …` (in the container: `docker compose exec
app python -m app …`); same options, output and exit status:

| Command | What it does |
|---|---|
| `python -m app migrate` | `python -m app.migrate`: adopt the first app's database, upgrade to head. |
| `python -m app schema-version` | The database's Alembic revision and this release's head; the first app's schema version when it has one. |
| `python -m app contract-docs` | `docs/data-contract.md` from the data contract. |
| `python -m app token create NAME --role ROLE [--tenant T] [--expires-days N]` | A token, printed once (the first admin token is made so). |
| `python -m app token list [--all] [--tenant T]` | The active tokens (`--all`: revoked and expired too), never the secrets. |
| `python -m app token revoke ID` | Revoke one. |
| `python -m app token purge [--older-than DAYS] [--dry-run]` | Delete revoked and expired ones for good. |
| `python -m app status-link TENANT [--new]` | The tenant's status page link; `--new` replaces it. |
| `python -m app wireless-check` | Read each wireless controller now; exit status 1 if one fails. |

## Ported code (#600)

The first app's modules (`nautobot_maps/`) moved here area by area (#587). Where their behaviour is pinned
by many tests, they are ported close to verbatim, on two thin layers:

- `app/db.py`: the first app's connection helpers (`db.get_conn()`, `db.transaction()`,
  `db.row_to_dict()`, `db.try_advisory_lock()`, ...). A psycopg connection in autocommit
  with dict rows from a SQLAlchemy engine's pool, with the connect and statement timeouts,
  so the current SQL runs unchanged. The same advisory lock keys: the two apps exclude each
  other during the switchover.
- `app/utils/legacy_settings.py`: `settings.NAUTOBOT_URL` reads `get_settings().nautobot_url`
  at call time; a test's `monkeypatch.setattr(settings, "NAME", value)` changes it for every
  module.

So far: `app/services/inventory_service.py` (the inventory sync into the snapshot tables, its
reads), `app/services/scheduler.py` (one thread per process, the `background_scheduler` lock,
the due syncs every tick; `BACKGROUND_SYNC_ENABLED=false` turns it off). Their tests are
ported from the first app's `tests/`, and `tests/test_nautobot_live.py` runs the sync
against CI's real Nautobot 3.x.

## The first app's routes (#604)

`app/v1/routes/` holds `nautobot_maps/api/`'s routers, ported with their logic as it is. Their
API is under `/api/v1` (the pages, `/healthz`, `/metrics` and `/mcp` keep their paths), with
the current answer models (`app/v1/answers/`) as each route's `response_model`, and a stable
camelCase `operation_id` (the verb and the function's name: `getLocations`, `postAlertCases`).
They stand on:

- `app/v1/deps.py`: `Json` (answers as the first app writes them), `AppRoute`, the router
  dependencies, `require_role`;
- `app/services/legacy_auth.py`: the current `auth` functions over #592's rules (one sign-in);
- `app/utils/requestctx.py`: the request being answered, for code below the routes (MCP).

**Sections (#623).** The API is grouped by the app's pages: `app/v1/sections.py` lists them in
the menu's order (Map, Alert Board, … Documentation), then Auth, Incoming alerts, Shared,
Monitoring and Examples. Every route has exactly one section as its tag
(`tags=sections.tag("map")`, on the router or, where a module serves several, on the route);
`/api/docs` and `/api/v1/endpoints` show them in that order, and `tests/test_sections.py` fails
on a route without one. A new route takes its page's section.

Errors answer `{"error": ...}`: 404 and 405 after the sign-in check, 400 for bad input
(not FastAPI's 422), 500 with the security headers. HEAD answers as GET without the body.

## Container (#613)

The repository's `Dockerfile` (`docker compose up --build` at the root): uv-installed,
non-root, `web/`'s pages and `docs/` (the help pages) built in. `docker-entrypoint.sh` runs
`python -m app migrate` (`MIGRATE_ON_START=false` skips it), then gunicorn with uvicorn workers
(`gunicorn_config.py`, `app/server.py`: no proxy headers, no server header) on
`GUNICORN_BIND` (`0.0.0.0:5000`); healthcheck on `GET /healthz`.

- The access log (`app/utils/access_log.py`, logger `app.access`), the log format
  (`LOG_FORMAT`, `app/utils/logs.py`) and the startup log (`app/startup.py`) are the first app's.
- The first app's `/api/<path>` answers 308 to `/api/v1/<path>` when that is a route
  (`app/v1/legacy_paths.py`), so webhooks and scripts keep working until they are changed.

## Pages, headers, gzip (#594)

- `app/frontend/`: `web/`'s pages as the first app serves `frontend/`: each path in
  `PAGES` answers the shell (`200.html`, `no-cache`; 503 "not built" without one),
  `/_nuxt/<file>` the content-named files (a year's cache, ETag/304, gzip for text, never
  outside the folder). `/_nuxt/` is public; each data route checks sign-in itself.
- Every answer (errors and 404s too): `Content-Security-Policy` (only this origin, images
  also from `MAP_TILE_URL`'s host, no inline scripts or styles), `nosniff`, `DENY`,
  `strict-origin-when-cross-origin` (`app/frontend/security.py`).
- gzip by the current rules (`app/frontend/compression.py`): text of 1 KiB or more, a 200
  answer to GET, not `Cache-Control: no-transform` (new API tokens), not streamed.
  `COMPRESS_RESPONSES=false` turns it off.
- `/api/docs` is web's API page (Swagger UI bundled, #609), under the CSP.
- `GET /healthz` (`getHealth`): `{"status", "checks": {"app", "database"}}`, 503 when the
  database doesn't answer within 2 s. `/api/healthz` is an alias.

## Sign-in and roles (#582, #592)

The first app's rules (`nautobot_maps/auth.py`) and setting names, so the bundled
oauth2-proxy + GitLab setup fits both. Code: `app/services/auth_service.py`.

| Setting | Default | Meaning |
|---|---|---|
| `AUTH_MODE` | `disabled` | `disabled`: nobody signs in; reads and the board's own changes are open, administration needs `ALLOW_UNAUTHENTICATED_WRITES`. `header`: a sign-in proxy sets the user and group headers. |
| `AUTH_HEADER_USER` / `AUTH_HEADER_GROUPS` | `X-Forwarded-User` / `X-Forwarded-Groups` | Identity headers (groups separated by `,` or `;`). |
| `AUTH_TRUSTED_PROXIES` | `127.0.0.1/32,::1/128` | Peers whose identity headers count (CSV of CIDRs). Anyone else is anonymous. |
| `AUTH_PROXY_SECRET` | empty | When set, the proxy must also send it as `X-Auth-Proxy-Secret`. |
| `AUTH_VIEWER_GROUPS` / `AUTH_OPERATOR_GROUPS` / `AUTH_ADMIN_GROUPS` | empty | Groups (CSV, case-insensitive) per role; the highest wins. |
| `AUTH_DEFAULT_ROLE` | empty | Role of a signed-in user in none of the groups. |
| `AUTH_REQUIRE_VIEWER` | `false` | Header mode: everything but `/healthz`, `/metrics` and the status pages needs the viewer role (otherwise reads are public). |
| `ALLOW_UNAUTHENTICATED_WRITES` | `false` | Disabled mode: administrative changes too, without signing in. |

- **API tokens** (`Authorization: Bearer nmt_…`, `/api/v1/tokens`, admin): sign in as
  `token:<name>` with the token's role, in either mode and from any address; they win over
  identity headers. A rejected (unknown, revoked, expired) token is a 401 everywhere but the
  public paths. A `status` token opens only its tenant's status page.
- Routes need a role through `require_role("operator")` (401 not signed in, 403 too low);
  `open_when_disabled=True` for the board's own changes.
- Refusals answer `{"error": "..."}`, with `detail`, `required_role`, `current_role` where
  they apply, as the first app does.
- `GET /api/v1/me` (`getCurrentUser`): who the API sees, and `can_write` / `can_administer`
  by these rules.
- Changes a browser sends from another site are refused (Sec-Fetch-Site, else the Origin's
  host), except from `CORS_ORIGINS`, and except with an API token. Those origins may send
  cookies (CORS credentials), unless `CORS_ORIGINS` contains `*`.
- uvicorn must run with `--no-proxy-headers` (the container's entrypoint does): otherwise
  `X-Forwarded-For` replaces the proxy's address and `AUTH_TRUSTED_PROXIES` checks the end user.
- The pages and the API share one origin, behind the one sign-in proxy (#594).
