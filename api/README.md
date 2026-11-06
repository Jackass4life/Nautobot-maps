# api/

The new backend (#572): FastAPI, managed with [uv](https://docs.astral.sh/uv/), PostgreSQL.
It is a separate project from the current app (`nautobot_maps/`, `app.py`, root
`requirements*.txt`), which it does not import or replace yet. The only thing it
shares with `web/` is `openapi.json`.

## Commands (run from `api/`)

```bash
cp .env.example .env                  # DATABASE_URL=postgresql+psycopg://...
uv sync
uv run alembic upgrade head           # apply migrations
uv run uvicorn app.main:app --reload --no-proxy-headers  # http://127.0.0.1:8000, docs at /api/docs
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

Every setting of the current app, same names and meaning: see
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

The schema is the current app's (version 33 of `nautobot_maps/db.py`): `app/models/` has
its 27 tables as they are, and the first Alembic revision creates exactly that
(`tests/schema_v33.json` is the reference; `tests/test_schema.py` compares column by column).

`python -m app.migrate` (the container runs it at start) brings any database to the newest schema:

- **The current app's database** (`schema_migrations` at version 33): stamped at the baseline
  revision, its data kept, then upgraded. The current app can still start on it until the
  schema changes.
- **Empty, or Alembic's already:** upgraded.
- **Refused, with what to do:** the current app's schema below or above 33, or its tables
  without a migration record. Start the current app once on it first (below 33).

Both apps take the same PostgreSQL lock while migrating.

## Ported code (#600)

The current app's modules move here area by area (#587). Where their behaviour is pinned
by many tests, they are ported close to verbatim, on two thin layers:

- `app/db.py`: the current app's connection helpers (`db.get_conn()`, `db.transaction()`,
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
ported from the current app's `tests/`, and `tests/test_nautobot_live.py` runs the sync
against CI's real Nautobot 3.x.

## Container

`api/Dockerfile` (#577): uv-installed, non-root, `python -m app.migrate` on start
(`MIGRATE_ON_START=false` skips it), healthcheck on `GET /healthz`. It builds `web/` (named
build context `web`: `docker build --build-context web=web -t api api/` from the repository
root) and serves its pages. `deploy/next/compose.yml` runs it with PostgreSQL.

## Pages, headers, gzip (#594)

- `app/frontend/`: `web/`'s pages as the current app serves `frontend/`: each path in
  `PAGES` answers the shell (`200.html`, `no-cache`; 503 "not built" without one),
  `/_nuxt/<file>` the content-named files (a year's cache, ETag/304, gzip for text, never
  outside the folder). `/_nuxt/` is public; each data route checks sign-in itself.
- Every answer (errors and 404s too): `Content-Security-Policy` (only this origin, images
  also from `MAP_TILE_URL`'s host, no inline scripts or styles), `nosniff`, `DENY`,
  `strict-origin-when-cross-origin` (`app/frontend/security.py`).
- gzip by the current rules (`app/frontend/compression.py`): text of 1 KiB or more, a 200
  answer to GET, not `Cache-Control: no-transform` (new API tokens), not streamed.
  `COMPRESS_RESPONSES=false` turns it off.
- `/api/docs` (FastAPI's Swagger UI) loads its scripts from a CDN with an inline one, which
  the CSP blocks in a browser; the current app's API page bundles Swagger UI and moves to
  `web/` in step 11 of #587. Until then, use `/api/openapi.json`.
- `GET /healthz` (`getHealth`): `{"status", "checks": {"app", "database"}}`, 503 when the
  database doesn't answer within 2 s. `/api/healthz` stays as an alias until the switchover.

## Sign-in and roles (#582, #592)

The current app's rules (`nautobot_maps/auth.py`) and setting names, so the bundled
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
  they apply, as the current app does.
- `GET /api/v1/me` (`getCurrentUser`): who the API sees, and `can_write` / `can_administer`
  by these rules.
- Changes a browser sends from another site are refused (Sec-Fetch-Site, else the Origin's
  host), except from `CORS_ORIGINS`, and except with an API token. Those origins may send
  cookies (CORS credentials), unless `CORS_ORIGINS` contains `*`.
- uvicorn must run with `--no-proxy-headers` (the container's entrypoint does): otherwise
  `X-Forwarded-For` replaces the proxy's address and `AUTH_TRUSTED_PROXIES` checks the end user.
- The pages and the API share one origin, behind the one sign-in proxy (#594).
