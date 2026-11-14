# Contributing to Nautobot Maps

Thank you for your interest in contributing to Nautobot Maps! This document provides guidelines to help you get started.

## How to Contribute

### Reporting Issues

- Use [GitHub Issues](../../issues) to report bugs or request features.
- Search existing issues before creating a new one to avoid duplicates.
- When reporting a bug, include:
  - Steps to reproduce the issue
  - Expected vs. actual behavior
  - Your environment (Python version, Nautobot version, browser, OS)

### Submitting Changes

1. **Fork** the repository and create a feature branch from `main`.
2. **Set up** the development environment: [uv](https://docs.astral.sh/uv/) for `api/` (Python 3.13) and [bun](https://bun.sh) 1.4 for `web/`:
   ```bash
   pip install --user uv
   (cd api && uv sync)                                  # the API's dependencies, dev tools included
   (cd web && bun install && bun run generate)          # the pages the API serves
   ```
3. **Make** your changes in small, focused commits.
4. **Test** your changes:
   ```bash
   (cd api && uv run pytest && uv run ruff check . && uv run ruff format --check .)
   (cd web && bun run format:check && bun run lint && bun run type:check && bun run test)
   uv run --project api ruff check e2e demo scripts deploy   # the files outside api/ and web/
   ```
   For UI changes, also run the end-to-end tests in Chromium against the mock Nautobot (CI's `e2e` job does the same):
   ```bash
   (cd api && uv sync --group e2e && uv run python -m playwright install chromium)
   uv run --project api python demo/mock_nautobot.py &         # :8080
   export NAUTOBOT_URL=http://127.0.0.1:8080 NAUTOBOT_TOKEN=x NAUTOBOT_MAPS_DATABASE_URL=postgresql://...
   (cd api && uv run python -m app migrate && FLASK_RUN_PORT=5055 uv run python run.py) &
   # Planning maintenance needs the operator role: an API token for the tests.
   export E2E_OPERATOR_TOKEN="$(cd api && uv run python -m app token create e2e --role operator --expires-days 1 | head -n 1)"
   # The Admin page needs the admin role (#530).
   export E2E_ADMIN_TOKEN="$(cd api && uv run python -m app token create e2e-admin --role admin --expires-days 1 | head -n 1)"
   # Each role's workspace: /noc and /customer (#697).
   export E2E_NOC_TOKEN="$(cd api && uv run python -m app token create e2e-noc --role noc --expires-days 1 | head -n 1)"
   export E2E_CUSTOMER_TOKEN="$(cd api && uv run python -m app token create e2e-customer --role customer --expires-days 1 | head -n 1)"
   E2E_BASE_URL=http://127.0.0.1:5055 uv run --project api python -m pytest e2e/ -v
   ```
5. **Update** `CHANGELOG.md`: add a line under `[Unreleased]` (Added / Changed / Fixed / Removed) for anything a user or operator notices — behaviour, settings, API, database tables. Pure refactors and test-only changes don't need one.
6. **Submit** a pull request against `main` with a clear description of the change.

### Development Setup

Install and run the app as in the [README](README.md); every setting is in [docs/configuration.md](docs/configuration.md). Without Docker: build the pages (`cd web && bun install && bun run generate`), copy `.env.example` to `.env`, then `cd api && uv sync && uv run python -m app migrate && uv run python run.py` (port `FLASK_RUN_PORT`, default 5000; `FLASK_DEBUG=true` reloads on changes). For live reload of the pages, `bun run dev` in `web/` serves them on :3000 and sends `/api/` to the API on :8000 (`FLASK_RUN_PORT=8000`). For a real Nautobot 3.x to develop against, see [`development/README.md`](development/README.md).

#### Dev container (optional)

The repository ships a [dev container](https://containers.dev/) in `.devcontainer/`. Open the
folder in VS Code and choose **Reopen in Container** (requires Docker and the Dev Containers
extension), or start it in GitHub Codespaces. It provides:

- uv with `api/`'s dependencies installed (Python 3.13), so `uv run pytest` and
  `uv run ruff check .` in `api/` work straight away
- PostgreSQL 16 on `localhost:5432` (user and password `nautobot_maps`), with a
  `nautobot_maps` database for the app; the tests make their own (`fastapi_nuxt_test`,
  or `API_TEST_DATABASE_URL`). Data is kept in a Docker volume across rebuilds.
- Node, which builds and tests the pages in `web/`
- GitHub CLI (`gh`) and Claude Code
- The Python and Ruff VS Code extensions
- Port 5000 forwarded for `python run.py`. If 5000 is already taken on your machine, VS Code
  picks another local port; check the **Ports** view.

You still need a `.env` (see [docs/configuration.md](docs/configuration.md)) to point the app at a Nautobot instance; for the
alert board add `NAUTOBOT_MAPS_DATABASE_URL=postgresql://nautobot_maps:nautobot_maps@localhost:5432/nautobot_maps`.
Feature versions are pinned in `.devcontainer/devcontainer-lock.json`. After changes to
`.devcontainer/`, run **Dev Containers: Rebuild Container**.

### Project layout

Two projects, each with its own dependencies, lint and CI job; they share only the API spec,
`web/api/openapi.json`. [`api/README.md`](api/README.md) and [`web/README.md`](web/README.md) have
the details.

- `api/`: FastAPI, managed with uv. `app/api.py` is the app: middleware (the access log, the
  security headers and gzip, the old `/api/...` paths' 308), the routers, the pages. The image
  serves it with gunicorn and uvicorn workers (`gunicorn_config.py`, `app/server.py`);
  `python run.py` runs it for development, `python -m app ...` is the command line.
  - `app/v1/<section>/`: one folder per section of the API, as `/api/docs` shows them
    (`app/v1/sections.py`: the pages in the menu's order, `map/`, `alert_board/`, …
    `documentation/`, then `auth/`, `incoming_alerts/`, `shared/`, `monitoring/`,
    `examples/`). A new route goes in its page's folder. `controller.py` holds the routes,
    with `deps.ROUTER_DEPENDENCIES` (the sign-in check), `route_class=deps.AppRoute`, the
    section's tag and the permission each route needs: `deps.require_permission(...)` (#685;
    the roles' permissions are one matrix in `app/auth/permissions.py`, and
    `tests/test_permissions.py` fails a route without one), `deps.PUBLIC` for one open to
    anyone. Answer with `deps.Json`,
    refuse with `raise deps.ApiError(body, status)`, read a JSON body with `deps.json_body`
    and give its model to the spec with `openapi_extra=deps.body_schema(...)`. `dtos.py`
    holds the section's query and body models and each route's `response_model` (fields as
    sent, `extra="forbid"`); shared ones are in `app/v1/common/dtos.py`. Every route has a
    camelCase `operation_id`: every test checks each answer it gets against its model
    (`tests/answers.py`), so `/api/docs` stays true.
  - `app/services/`: one module per job: `inventory_service` (the sync into the snapshot
    tables), `alerts_service` (the board's rules, no SQL), `maintenance_service`,
    `availability_service`, `notify_service`, `webhooks_service`, `wireless_service` and
    `neighbours_service`, `status_service` and `status_link_service`, `token_service` and
    `legacy_auth` (sign-in and roles), `scheduler` (the background syncs, one thread per
    worker, a PostgreSQL lock), `mcp_service`, `guide_service` (`/help` from `README.md`,
    `docs/*.md` and this file; a new guide goes in `guide_service.PAGES`).
  - `app/clients/`: Nautobot (**read-only**: the app never writes to Nautobot, the source of
    truth), LibreNMS, Catalyst Center, New Central, the response cache.
  - `app/repositories/`: SQL; `board_repository` holds the board's reads and writes, called as
    `board_store.function()`.
  - `app/models/` and `alembic/`: the schema (SQLAlchemy models, Alembic revisions).
  - `app/core/settings.py`: every setting, the only module that reads the environment.
    Code ported from the first app reads them as `settings.NAME` (`app/core/legacy_settings.py`)
    at call time; tests change them with `monkeypatch.setattr(settings, "NAME", value)`.
    Add a new setting there, pass it in `docker-compose.yml` (a test checks this) and document
    it in `docs/configuration.md` and `.env.example`.
  - `app/db/`: `session.py`, SQLAlchemy's async engine (asyncpg) and the `AsyncDbSession`
    dependency; `middleware.py`, one `AsyncSession` per API request; `connection.py`, the
    ported code's row and key helpers (imported as `db`: `db.row_to_dict()`,
    `db.placeholders()`). asyncpg is the only driver (#664). New database code is async: a
    repository on `AsyncBaseRepository` (`app/core/repository.py`) or `await
    session.execute(text(...))` with `:name` parameters. Sync code in a thread reaches the
    app's loop with `session.from_sync(function, ...)`; background work starts with
    `session.spawn(name, function)`.
    The ported code's raw SQL moves to `aconnection.py` (#651): `async with
    aconnection.connect() as conn`, then `await conn.execute(sql, params)` with its `%s`
    placeholders unchanged, `async with conn.transaction()`, `conn.stream()` for a big
    export. asyncpg wants real types: a time is a `datetime`, and a parameter in a `SELECT`
    list needs a cast (`%s::timestamptz`).
  - `app/core/`: the settings, the errors and their handlers (`exceptions.py`), `cors.py`,
    `version.py` (the version is `api/pyproject.toml`'s); `app/auth/` the sign-in and role
    dependencies; `app/context/` the request being answered; `app/logs/` the log setup and
    the access log; `app/lifespan.py` the startup.
  - Logging: `from loguru import logger`, placeholders as `{}` (`logger.warning("Sync of
    {} failed: {}", site, exc)`; tests fail on a `%s` or a wrong count), a stack trace with
    `logger.exception(...)` or `logger.opt(exception=True).warning(...)`. Each line's part
    (`sync`, `api`, …) comes from the module's name through `app/logs/parts.py`: a new
    module that logs gets a line there (a test checks). Tests read the lines with `caplog`.
  - `app/utils/`: small helpers, e.g. `timeutil.py` (tests freeze time with
    `monkeypatch.setattr(timeutil, "iso_utc_now", ...)`).
- `web/`: the pages (Nuxt), below.
- `e2e/`: Playwright tests against a running app; `demo/`: the mock Nautobot; `reverse-proxy/`:
  the bundled HTTPS and sign-in proxy; `scripts/screenshots.py`: the README's pictures.

Importing a module must not touch the database (connections are made when first used).
Modules call each other as `module.function()`, not `from module import function`, so a test
can replace a function where it is defined.

### The pages (#463, #609)

Every page is built with [Nuxt](https://nuxt.com) (Vue, TypeScript, [Nuxt UI](https://ui.nuxt.com)) in `web/`, to static files the API serves; no Node in production.

```bash
cd web
bun install         # bun 1.4 (the version in package.json's packageManager)
bun run generate    # the static build the API serves: web/.output/public
bun run dev         # or: a dev server on :3000 with live reload; /api/ goes to the API on :8000
bun run format:check && bun run lint && bun run type:check && bun run test
```

- Without a build, every page answers 503 and says to build it (the API still works).
- `ssr: false`: a single-page app. A page's route answers the shell; its files come from `/_nuxt/`, cached for a year and public (code only, no data).
- The API only through the typed client: `useNuxtApp().$api` (openapi-fetch) with `unwrap()` from `utils/api.ts`; no raw `fetch`. After an API change: `uv run python scripts/generate_openapi.py` in `api/`, then `bun run api:generate` in `web/` (or both at once: `bun run api:generate:from-source`; CI fails until both are done). The spec and the types are `web/api/openapi.json` and `web/api/api.schema.ts`; never edit them by hand.
- CSP stays strict: `bun run generate` moves Nuxt's inline config script into a file and fails on any other inline script; Nuxt UI's colours are a stylesheet made at build time (`modules/static-ui-colors.ts`), not its inline `<style>`; no web fonts or icons are fetched from elsewhere.
- Logic goes in `utils/` (plain functions, tested with Vitest in `web/tests/`); the pages themselves are tested end to end (`e2e/`).
- Scale: keep big lists (sites, devices) in `shallowRef`, not deep reactivity.

Rules for the pages:

- Components: [Nuxt UI](https://ui.nuxt.com) first (`<UButton>`, `<UTable>`, `<UModal>`, ...).
  [shadcn-vue](https://www.shadcn-vue.com) (#640) only for what Nuxt UI lacks, or where its look
  is wanted; its components are `<S…>` (`<STable>`, `<SSonner>`). Add one with
  `bun run add:component <name>` in `web/` (or copy it from the shadcn-vue repository at the
  pinned version); it lands in `components/ui/` and is then our code. Icons stay the iconify
  Lucide set (`<UIcon name="i-lucide-…">`), not `@lucide/vue`, and nothing inline (`:style`)
  that the CSP would block.
- One set of colours: shadcn's tokens (`--primary`, `--muted`, `--border`, ...) are Nuxt UI's
  variables (`assets/css/shadcn.css`). Change a colour in `ui-colors.ts`, never in
  `shadcn.css`; a new component's border elements go in its `@layer base` rule
  (`web/tests/shadcn.test.ts` checks all of this).
- Toasts: `toast("…")` from `vue-sonner` (`<SSonner />` is in `app.vue`) for new code.
- No inline styles or scripts (#370): the CSP allows only the app's own files, so markup uses
  classes in `assets/css/main.css` (or Nuxt UI's), never `style="…"`; setting
  `element.style` from script is fine.
- Third-party code comes from the npm registry, pinned, into the build (Leaflet and markercluster for the map;
  Swagger UI for `/api/docs`, loaded on that page only): no CDN.
- A rule the server and the browser share is tested on both sides against one fixture, e.g.
  the scope cases for `?tenant=` / `?q=` (`api/app/services/scope_service.py` and `web/utils/scope.ts`).
- `bun.lock`: change it only with bun (`bun add`, `bun install`); CI and the image install with `--frozen-lockfile`.

### Code Style

- Python code must pass `uv run ruff check .` and `uv run ruff format --check .` in `api/`
  (configured in `api/pyproject.toml`; CI runs both). Many findings can be fixed with
  `uv run ruff check --fix .` and `uv run ruff format .`. The files outside `api/` (e2e, demo,
  scripts) use the root `pyproject.toml`'s ruff settings.
- The pages must pass `bun run format:check`, `bun run lint` and `bun run type:check` in `web/` (CI runs all three). `bun run format` formats them (Prettier, #669; `htmlWhitespaceSensitivity: strict`, so a reformat never changes the text on a page). The one-time reformat is in `.git-blame-ignore-revs`: `git config blame.ignoreRevsFile .git-blame-ignore-revs` makes local `git blame` skip it too.
- Write clear commit messages describing what changed and why.

### Database schema changes

The schema is Alembic's (`api/alembic/`). Its first revision is the first app's schema version 33,
which a database that app migrated is adopted at (`app/migrate.py`, #590). To change the schema,
change the models in `api/app/models/`, then:

```bash
cd api
uv run alembic revision --autogenerate -m "alert_instances.foo"   # review the generated file
uv run alembic upgrade head
```

Never edit a revision that may already have been applied. The container runs `python -m app migrate` before gunicorn starts; `python -m app schema-version` shows where a database is.

### Dependencies

`api/`: `uv add <package>` (or `uv add --dev`, `--group e2e`), never pip; `uv.lock` pins every package, transitive ones included, with hashes, so the image and CI install exactly what was tested (#186). `web/`: `bun add --exact <package>` (`--dev` for a build tool); `bun.lock` pins every package with its hash.

CI fails when a lock file doesn't match, or when `pip-audit`, `bun audit` or the Trivy image scan finds a known vulnerability. Dependabot proposes updates weekly.

### Tests

- Add tests for new functionality.
- Ensure all existing tests pass before submitting a pull request.
- `uv run pytest` in `api/` makes its own PostgreSQL database (`fastapi_nuxt_test` on the
  local server, or `API_TEST_DATABASE_URL`), migrated with Alembic and emptied after each test;
  without a database server the tests that need one fail.
- Both unit tests (mocked) and integration tests are welcome.

The suite:

- `api/tests/test_app.py`: the first app's unit tests, mock-based, with the database.
- One file per feature for the rest (`test_wireless.py`, `test_tokens.py`, `test_status_page.py`, `test_cli.py`, `test_server.py`, …).
- `api/tests/answers.py`: every JSON answer a test gets is checked against its route's
  `response_model`; `test_openapi.py`: every route's `response_model` and `operation_id`, the spec up to date.
- `api/tests/test_schema.py`: Alembic's schema is the first app's version 33, column by column.
- `api/tests/test_settings.py`, `test_help_pages.py`: every setting passed and documented, every link in the
  guides lands on a heading.
- `web/tests/`: Vitest for the pages' logic in `utils/`; `e2e/`: Playwright in Chromium.
- `api/tests/test_mcp.py`: the MCP server.
- `api/tests/test_nautobot_live.py`: skipped unless `NAUTOBOT_LIVE_URL` and `NAUTOBOT_LIVE_TOKEN`
  point at a real Nautobot (see `development/`).

### Screenshots

The README's pictures (`docs/images/`) come from `scripts/screenshots.py` (`uv run --project api --group e2e --with Pillow python scripts/screenshots.py`), run against the
scale demo (`MOCK_SCALE=1200`, [demo/README.md](demo/README.md)), `WIRELESS_CONTROLLERS_FILE=demo/wireless.yml`
and an empty database, with an admin token (for the Admin page); its docstring has the steps. Run it again when a page they show changes.

### How the inventory sync works

Requests never call Nautobot or LibreNMS for the alert board: they read the snapshot in
PostgreSQL, which `/api/v1/locations`, `/api/v1/locations/<id>/detail` and `/api/v1/alerts` prefer
as their source. The sync (`inventory.sync_nautobot()`, `inventory.sync_librenms()`) runs
from the scheduler, or is queued by a request once it is due; `?refresh=1` only queues it.

- Nautobot is pulled incrementally with `last_updated__gte=<watermark>`. Device pages use
  `depth=1` so `primary_ip4`/`primary_ip6` carry their address inline.
- The watermark only advances when an incremental pull sees newer upstream `last_updated`
  values; a full reconcile uses its start time.
- A change in the cached extraction version forces a full reconcile; a daily full reconcile
  removes objects deleted in Nautobot.
- LibreNMS refreshes on its own interval.
- On a fresh database the first `/api/v1/alerts` request starts the initial sync in the
  background and returns `sync_pending: true`; the board re-polls until it clears.

## Releasing

Versions follow [Semantic Versioning](https://semver.org/) (a **Breaking** changelog entry means a new major version; before 1.0, a new minor version).

1. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and add a new empty `## [Unreleased]` above it.
2. Merge that to `main`.
3. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The `Release` workflow then builds the two images (#674), pushes `ghcr.io/jackass4life/nautobot-maps-api:X.Y.Z` and `ghcr.io/jackass4life/nautobot-maps-web:X.Y.Z` (plus `X.Y` and `latest`) with provenance and an SBOM, and creates a GitHub Release with that changelog section. It refuses a tag without a matching changelog section. The reverse proxy's image isn't published: its host name is built in, so each install builds it. A newly published package is private on GitHub; make it public (package settings → visibility) or have deployments `docker login ghcr.io`.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
