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
uv run uvicorn app.main:app --reload  # http://127.0.0.1:8000, docs at /api/docs
uv run python -m app.export_openapi   # write openapi.json (a test fails if stale)
uv run pytest                         # TEST_DATABASE_URL: a separate DB, reset by the tests
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

## Container

`api/Dockerfile` (#577): uv-installed, non-root, migrations on start
(`MIGRATE_ON_START=false` skips them), healthcheck on `GET /api/healthz`.
`deploy/next/compose.yml` runs it with PostgreSQL and `web/`.
