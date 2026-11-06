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
| `CORS_ORIGINS` | `http://localhost:3000` | Browser origins that may call the API (JSON list or comma-separated). |
| `SQL_ECHO` | `false` | Log every SQL statement (development). |

## Container

`api/Dockerfile` (#577): uv-installed, non-root, migrations on start
(`MIGRATE_ON_START=false` skips them), healthcheck on `GET /api/healthz`.
`deploy/next/compose.yml` runs it with PostgreSQL and `web/`.

## Sign-in and roles (#582)

Same model and setting names as the current app, so the bundled oauth2-proxy + GitLab
setup fits both.

| Setting | Default | Meaning |
|---|---|---|
| `AUTH_MODE` | `disabled` | `disabled`: no sign-in, everyone is admin (development only). `header`: a sign-in proxy sets the user and group headers. |
| `AUTH_HEADER_USER` / `AUTH_HEADER_GROUPS` | `X-Forwarded-User` / `X-Forwarded-Groups` | Identity headers (groups comma-separated). |
| `AUTH_TRUSTED_PROXIES` | `127.0.0.1/32,::1/128` | Peers whose identity headers count (CSV of CIDRs). Anyone else is anonymous. |
| `AUTH_PROXY_SECRET` | empty | When set, the proxy must also send it as `X-Auth-Proxy-Secret`. |
| `AUTH_VIEWER_GROUPS` / `AUTH_OPERATOR_GROUPS` / `AUTH_ADMIN_GROUPS` | empty | Groups (CSV, case-insensitive) per role; the highest wins. |
| `AUTH_DEFAULT_ROLE` | empty | Role of a signed-in user in none of the groups. |
| `AUTH_REQUIRE_VIEWER` | `false` | Reads need the viewer role too (otherwise public). |

- Writes (create/delete) need **operator**: 401 when not signed in, 403 with too low a role.
- `GET /api/v1/me` (`getCurrentUser`) says who the API sees; always open.
- Changes sent by a browser from another origin (scheme, host and port) are refused
  (Sec-Fetch-Site / Origin), except from `CORS_ORIGINS`. Those may send cookies (CORS
  credentials), unless `CORS_ORIGINS` contains `*`.
- uvicorn must run with `--no-proxy-headers` (the container's entrypoint does): otherwise
  `X-Forwarded-For` replaces the proxy's address and `AUTH_TRUSTED_PROXIES` checks the end user.
- `web/` renders pages server-side and forwards the identity headers it got, so the web
  container's address belongs in `AUTH_TRUSTED_PROXIES` too, and `AUTH_PROXY_SECRET` must be
  set (web forwards only requests carrying it, `NUXT_AUTH_PROXY_SECRET`). Browsers should reach the API
  through the same sign-in proxy (ideally the same origin, `/api` routed to `api/`).
