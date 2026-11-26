# Contributing to Nautobot Maps

Thank you for your interest in contributing to Nautobot Maps! This document provides guidelines to help you get started. For a guided tour of the code, see the [Code guide](docs/code-guide.md).

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
   (cd web && bun install && bun run generate)          # the pages, for the development server
   ```
3. **Make** your changes in small, focused commits.
4. **Test** your changes, the way CI does (see [Tests](#tests)):
   ```bash
   scripts/ci/test.sh pytest tests/test_board_once.py   # while you work: only the API tests you name
   scripts/ci/test.sh all                               # before the PR: API, pages and end-to-end
   uv run --project api ruff check . && uv run --project api ruff format --check .   # the files outside api/ and web/, as CI
   ```
5. **Update** `CHANGELOG.md`: add a line under `[Unreleased]` (Added / Changed / Fixed / Removed) for anything a user or operator notices — behaviour, settings, API, database tables. Pure refactors and test-only changes don't need one.
6. **Submit** a pull request against `main` with a clear description of the change.

### Development Setup

Install and run the app as in the [README](README.md); every setting is in [docs/configuration.md](docs/configuration.md). Without Docker: build the pages (`cd web && bun install && bun run generate`), copy `api/local.env` to `api/.env` (every setting, #760: the API has no defaults; `localhost`, no Redis; set `NAUTOBOT_URL` and `NAUTOBOT_TOKEN` there), then `cd api && uv sync && uv run python -m app migrate && uv run python run.py` (port `FLASK_RUN_PORT`, default 5000; `FLASK_DEBUG=true` reloads on changes). For live reload of the pages, `bun run dev` in `web/` serves them on :3000 and sends `/api/` to the API on :8000 (`FLASK_RUN_PORT=8000`). For a real Nautobot 3.x to develop against, see [`development/README.md`](development/README.md).

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

New to the code? Start with the [Code guide](docs/code-guide.md): the parts, where to find what, how the main features work, and recipes for common changes. The list below is the short version.

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
    Each field is named like its environment variable (`NAUTOBOT_URL: str`, #760). They are
    read once into one shared object: `from app.core.settings import settings`, then
    `settings.NAUTOBOT_URL` inside the function that needs it, never at import (`tests/test_layout.py`
    checks); tests change them with the `override_settings`
    fixture (`override_settings(NAUTOBOT_URL="")`) or `with overridden(...)` from
    `tests/overrides.py`, read as `.env` is (#780).
    Add a new setting there and document it in `docs/configuration.md` (with its **Read by**:
    `api`) and `.env.example`. Compose passes nothing itself: every
    container reads `.env` (#739). A variable a script or test reads from the environment goes
    in configuration.md's "Not in .env" table (#749).
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

Every page is built with [Nuxt](https://nuxt.com) (Vue, TypeScript, [Nuxt UI](https://ui.nuxt.com)) in `web/`. In production the `web` image serves them (Nuxt's server on bun, port 3000) and passes the API's paths on to `api`; in development `python run.py` serves the static build (`WEB_BUILD_DIR`). No Node anywhere.

```bash
cd web
bun install         # bun 1.4 (the version in package.json's packageManager)
bun run generate    # the static build the development server serves: web/.output/public
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

CI fails when a lock file doesn't match, or when `pip-audit` (any known vulnerability), `bun audit` or the Trivy image scan (high or critical; Trivy only those with a fix released) finds one. `web/scripts/audit.mjs` lists the few `bun audit` advisories accepted until a fix is released, each with why it can't be reached (#756). Dependabot proposes updates weekly.

### Tests

The tests need PostgreSQL and nothing else: Nautobot, LibreNMS, the wireless controllers, AD and Redis are faked, and the settings come from `api/local.env` (a deployment's `.env` doesn't reach them; #774).

**Running them**

| What | Command |
|---|---|
| Some API tests, while you work | `scripts/ci/test.sh pytest tests/test_x.py -k name` (any pytest arguments) |
| The same in the dev container (its PostgreSQL is running) | `uv run pytest tests/test_x.py -k name` in `api/` |
| Everything CI runs for the API: ruff, migrations, pytest | `scripts/ci/test.sh api` |
| The pages: types, lint, formatting, vitest, build | `scripts/ci/test.sh web` |
| End to end: the app on the mock Nautobot, Playwright in Chromium | `scripts/ci/test.sh e2e` |
| All of it | `scripts/ci/test.sh all` |

`scripts/ci/test.sh` (#765) is what GitHub Actions and GitLab CI run. It uses a throwaway PostgreSQL: `TEST_PG_URL=postgresql://user:password@host:port` (it creates and drops its own databases there) or else a Docker container. It picks free ports and reads every setting from `api/local.env` into its environment; your `api/.env` is not touched. `uv run pytest` makes its own database (`fastapi_nuxt_test` on the local server, or `API_TEST_DATABASE_URL`), migrated with Alembic and emptied after each test; with no server reachable it stops at once and says so (#776).

The end-to-end tests against an app you started yourself (e.g. to watch it in a browser): `E2E_BASE_URL=http://127.0.0.1:5055 uv run --project api python -m pytest e2e/`, with an API token per role in `E2E_*_TOKEN` (`e2e/test_e2e.py`'s docstring; `scripts/ci/test.sh`'s `e2e_start` and `e2e_tokens` do it all).

**Writing one**

- Add tests for new functionality, and only tests that catch a real bug (wrong behaviour, security, data loss).
- Calling the API: the `client` fixture (FastAPI's `TestClient`; `client.get("/api/v1/...").json()`), from 127.0.0.1 (a trusted proxy by default) to `http://localhost`, redirects not followed, no Accept or gzip unless the test sends them. From another address, or with other options: `http_client.make(address=("10.9.9.9", 50000))` (`from tests import http_client`; `raise_server_exceptions=False` to see a crash's 500 answer).
- Settings: `override_settings(AUTH_MODE="header", AUTH_REQUIRE_VIEWER="true")` for the test's duration; `with overridden(...)` (`tests/overrides.py`) for a block or a module-scoped fixture. Values are read as `.env` is (#780): text parsed, derived settings worked out again, and a combination startup refuses is refused here too, so a test can't run with settings no deployment could have.
- The database: `tests/db.py` (`from tests import db as adb`): `adb.sql("SELECT ...", params)` for rows, `adb.call(fn, *args)` to run a service function, `adb.run(fn)` for one with a session. Every table is emptied after each test.
- Nautobot, LibreNMS and the controllers: patch `request` in `app.clients.http` (imported as `upstream_http`; failures as httpx raises them in `tests/upstream.py`); `test_wireless.py`'s `controllers` fixture fakes the wireless controllers; `board` gives a board built from the database only, at a clock you set (`board["now"]`).
- "Now": `app.utils.timeutil` (patch `timeutil.iso_utc_now`), never `datetime.now()` in the code.
- Every JSON answer a test gets is checked against its route's `response_model` automatically.

The suite:

- `api/tests/test_app.py`: the first app's unit tests, mock-based, with the database.
- One file per feature for the rest (`test_wireless.py`, `test_tokens.py`, `test_status_page.py`, `test_cli.py`, `test_server.py`, …).
- `api/tests/answers.py`: every JSON answer a test gets is checked against its route's
  `response_model`; `test_openapi.py`: every route's `response_model` and `operation_id`, the spec up to date.
- `api/tests/test_schema.py`: Alembic's schema is the first app's version 33, column by column.
- `api/tests/test_settings.py`: every setting parsed, and a mistake stops startup; `test_help_pages.py`:
  every link in the guides lands on a heading.
- `web/tests/`: Vitest for the pages' logic in `utils/`; `e2e/`: Playwright in Chromium.
- `api/tests/test_mcp.py`: the MCP server.
- `api/tests/test_ad_login.py`: AD sign-in (#717) as the app sees it, with `ad_service.authenticate` faked (#774); `test_ad_service.py`: the AD service around `sit_core`'s `BasicAuthenticator`, with a pretend `sit_core` (#782: the company's package isn't on PyPI, so neither the tests nor CI install it).
- `api/tests/test_ad_live.py`: AD sign-in (#717) against a real Active Directory, skipped unless `AD_LIVE_HOST` is set and `sit_core` is installed (so not in GitHub's CI, #782). `development/samba_ad.sh start` runs one (Samba AD, LDAPS with its own CA; `sudo apt-get install -y samba samba-ad-dc` first), then `AD_LIVE_HOST=dc1.nmaps.test uv run pytest tests/test_ad_live.py`; another AD: `AD_LIVE_USER`/`AD_LIVE_PASSWORD` too. In Chromium: `e2e/test_ad_sign_in.py`, with `E2E_AD_BASE_URL` pointing at an app with `AD_HOST` set, jdoe an identity with the noc role.
- `api/tests/test_nautobot_live.py`: skipped unless `NAUTOBOT_LIVE_URL` and `NAUTOBOT_LIVE_TOKEN`
  point at a real Nautobot (see `development/`).

### CI

`scripts/ci/test.sh` (#765) holds the api, web and e2e tests; GitHub Actions (`.github/workflows/ci.yml`) and GitLab CI (`.gitlab-ci.yml`) only install the tools and call it, so a local run does the same. Every check of a part runs even after one fails, and the part then fails; on GitHub a failed check is an annotation and a line in the job summary, on GitLab ruff's findings show in the merge request (Code Quality). GitHub's other jobs (lint, audit, the live integrations, the images and the proxy) are GitHub-only.

The tools' versions are in one place each: uv in `api/pyproject.toml` (`required-version`; also `api/Dockerfile` and `.devcontainer/devcontainer.json`, bumped together), bun in `web/package.json` (`packageManager`), Python in `api/.python-version`, Playwright in `api/uv.lock`.

**CI on GitLab** needs a shell runner tagged `shell`, set up once:

- `curl`, `git`, `unzip` and `procps`.
- PostgreSQL: Docker, with the `gitlab-runner` user in the `docker` group (that is root on the runner's host: give it a host of its own), or a server for the runner's CI variable `TEST_PG_URL` (`postgresql://user:password@host:port`, a user that may create databases; the jobs create and drop their own).
- Chromium's system libraries, as root: `sudo "$(command -v uv)" run --project api --group e2e python -m playwright install-deps chromium` in a checkout (or set `PLAYWRIGHT_WITH_DEPS=1` when the runner may use sudo).
- Outbound HTTPS to astral.sh and github.com (uv), bun.sh (bun), pypi.org and files.pythonhosted.org, registry.npmjs.org, cdn.playwright.dev and playwright.download.prss.microsoft.com (Chromium), and Docker Hub for `postgres:16-alpine`. Behind a proxy: `HTTPS_PROXY` and the company CA (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`) in the runner's environment.

The tests run before deploy and need nothing that only the server has (#774): after loading the settings (`api/local.env`), the api and e2e parts set `CACHE_TYPE=SimpleCache` and an empty `AD_HOST`, and the API tests do the same, so a deployment's `.env` with Redis or AD doesn't reach them.

The first pipeline installs uv and bun into the runner user's `~/.local/bin` and `~/.bun`; later ones find them there. Each job uses free ports and its own databases, so two pipelines can run on one runner at once.

### Screenshots

The README's pictures (`docs/images/`) come from `scripts/screenshots.py` (`uv run --project api --group e2e --with Pillow python scripts/screenshots.py`), run against the
scale demo (`MOCK_SCALE=1200`, [demo/README.md](demo/README.md)), the demo's controllers (`set -a; . ../demo/wireless.env; set +a` in `api/`)
and an empty database, with an admin token (for the Admin page) and AD sign-in on (for the sign-in page: `development/samba_ad.sh`); its docstring has the steps. Run it again when a page they show changes; `--only admin,sign-in` writes just those.

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
