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
2. **Set up** the development environment:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements-dev.txt
   (cd frontend && npm ci && npm run generate)   # Node 22: the pages python app.py serves
   ```
   Optionally, run the linter automatically on every commit:
   ```bash
   pip install pre-commit && pre-commit install
   ```
3. **Make** your changes in small, focused commits.
4. **Test** your changes:
   ```bash
   python -m pytest tests/ -v
   (cd frontend && npm run lint && npm run typecheck && npm test)
   ```
   For UI changes, also run the end-to-end tests in Chromium against the mock Nautobot (CI's `e2e` job does the same):
   ```bash
   pip install -r requirements-e2e.txt && python -m playwright install chromium
   python demo/mock_nautobot.py &                       # :8080
   NAUTOBOT_URL=http://127.0.0.1:8080 NAUTOBOT_TOKEN=x \
     NAUTOBOT_MAPS_DATABASE_URL=postgresql://... FLASK_RUN_PORT=5055 python app.py &
   # Planning maintenance needs the operator role: an API token for the tests.
   export E2E_OPERATOR_TOKEN="$(NAUTOBOT_MAPS_DATABASE_URL=postgresql://... python -m nautobot_maps token create e2e --role operator --expires-days 1 | head -n 1)"
   E2E_BASE_URL=http://127.0.0.1:5055 python -m pytest e2e/ -v
   ```
5. **Update** `CHANGELOG.md`: add a line under `[Unreleased]` (Added / Changed / Fixed / Removed) for anything a user or operator notices — behaviour, settings, API, database tables. Pure refactors and test-only changes don't need one.
6. **Submit** a pull request against `main` with a clear description of the change.

### Development Setup

Install and run the app as in the [README](README.md); every setting is in [docs/configuration.md](docs/configuration.md). Without Docker: `pip install -r requirements-dev.txt`, build the pages (`cd frontend && npm ci && npm run generate`), copy `.env.example` to `.env`, then `python app.py`. For a real Nautobot 3.x to develop against, see [`development/README.md`](development/README.md).

#### Dev container (optional)

The repository ships a [dev container](https://containers.dev/) in `.devcontainer/`. Open the
folder in VS Code and choose **Reopen in Container** (requires Docker and the Dev Containers
extension), or start it in GitHub Codespaces. It provides:

- Python 3.11 (the oldest version CI tests) with `requirements-dev.txt` installed, so
  `pytest` and `ruff check .` work straight away
- PostgreSQL 16 on `localhost:5432` (user and password `nautobot_maps`), with a
  `nautobot_maps` database for the app and a `nautobot_maps_test` database for pytest
  (`TEST_DATABASE_URL` is preset). Data is kept in a Docker volume across rebuilds.
- Node, which builds and tests the pages in `frontend/`
- GitHub CLI (`gh`) and Claude Code
- The Python and Ruff VS Code extensions
- Port 5000 forwarded for `python app.py`. If 5000 is already taken on your machine, VS Code
  picks another local port; check the **Ports** view.

You still need a `.env` (see [docs/configuration.md](docs/configuration.md)) to point the app at a Nautobot instance; for the
alert board add `NAUTOBOT_MAPS_DATABASE_URL=postgresql://nautobot_maps:nautobot_maps@localhost:5432/nautobot_maps`.
Feature versions are pinned in `.devcontainer/devcontainer-lock.json`. After changes to
`.devcontainer/`, run **Dev Containers: Rebuild Container**.

### Project layout

`asgi.py` is the app (FastAPI, #436): gunicorn serves `asgi:app` with uvicorn workers, and the
access log (`nautobot_maps/access_log.py`) wraps it. `app.py` is the startup: logging, the cache,
the database and the startup log, once per process; `python app.py` runs the app with uvicorn
for development (`FLASK_DEBUG=true` reloads on changes; the names are kept from the Flask days).
Every route lives in `nautobot_maps/api/`, one router per area, with `deps.ROUTER_DEPENDENCIES`
(the sign-in check), `route_class=deps.AppRoute` (security headers, gzip, the docstring in the
API spec) and `deps.require_role("operator")` for writes. Answer with `deps.Json` (JSON as the
app always wrote it), raise `deps.ApiError(body, status)` to refuse, read a JSON body with
`deps.json_body` and give its model to the spec with `openapi_extra=deps.body_schema(...)`;
query strings are models in `api/models.py`. Describe what a JSON route answers with
`response_model=` from `api/answers/<area>.py` (fields as sent, `extra="forbid"`); the route
still returns its `deps.Json`, and every test checks each answer it gets against that model
(`tests/answers.py`), so `/api/docs` stays true. Code below the routes reads the request through
`nautobot_maps/requestctx.py`. Keep a route's `name` stable (`"board.api_alerts"`): the frozen
route table and `/api/endpoints`' examples use it.
Every test client (`client` and the others; `tests/http_client.py`) is FastAPI's on `asgi.app`
with the calls the tests were written with (`get_json()`, `environ_base`, …), so the tests
check what production serves; `tests.http_client.fake_request()` binds a request for code that
reads one outside a route.
Everything else lives in modules under `nautobot_maps/` (#165):

- `nautobot_maps/settings.py`: every setting read from the environment (`.env`). Add new
  settings here, pass them in `docker-compose.yml` (a test checks this), document them in
  `docs/configuration.md` and `.env.example`, and read them as
  `settings.NAME` at call time; in tests change them with
  `monkeypatch.setattr(settings, "NAME", value)`.
- `nautobot_maps/db.py`: PostgreSQL connections (`db.get_conn()`, `db.transaction()`), the
  schema and its migrations (`db.init_db()`), and small SQL helpers.
- `nautobot_maps/nautobot.py`: the Nautobot REST client, **read-only** (`nautobot.get()`,
  `nautobot.fetch_all_pages()`), and the id → name lookup maps. The app never writes to Nautobot:
  Nautobot is the source of truth, and changes to its data are made there.
- `nautobot_maps/librenms.py`: the LibreNMS REST client (`librenms.get()`,
  `librenms.fetch_inventory()`).
- `nautobot_maps/caching.py`: the shared response cache (cachelib: in-process, Redis or none).
- `nautobot_maps/http.py`: the HTTP session both clients use (one per thread, connections reused), with retries and backoff for transient upstream errors.
- `nautobot_maps/inventory.py`: the inventory sync — normalising Nautobot data, the cache tables,
  sync state, and `inventory.ensure_snapshot()` / `inventory.get_locations()`.
- `nautobot_maps/alerts.py`: the alert logic — severity scoring (`alerts.compute_alert_level()`),
  the LibreNMS status merge, alert history, the alert board (`alerts.get_alert_board_data()`) and
  the map's location detail.
- `nautobot_maps/repositories/board.py`: the alert board's reads and writes in PostgreSQL (#424):
  alert instances and events, device states, site levels and their changes, the feed, cases and
  acknowledgements. `alerts.py` keeps the rules and holds no SQL; it calls these as
  `board_store.function()` (imported under that name, since `alerts.board()` exists). Tests patch
  them on `nautobot_maps.repositories.board`.
- `nautobot_maps/scheduler.py`: the background scheduler (`scheduler.start()`, started by
  gunicorn in each worker).
- `nautobot_maps/web.py`: the wiring: security headers and the CSP, error pages, the sign-in
  check, and `web.register(app)`, which registers the routes.
- `nautobot_maps/guide.py`: the documentation pages at `/help` (#433): `README.md`, `docs/*.md` and
  this file rendered with markdown-it-py (raw HTML off, GitHub's heading anchors, links between the
  docs rewritten). A new guide goes in `guide.PAGES`; `tests/test_help_pages.py` checks every
  internal link lands on a heading.
- `nautobot_maps/api/`: the routes, on FastAPI (#424, #436), one router per area and tag in the
  API spec: `views` (map, board, `/api/docs`, `/api/openapi.json`, `/help`, static files), `locations`
  (the map's API and search), `board` (the board's API: feed, history, cases, acks,
  criticality), `maintenance`, `availability`, `circuits` and `status` (each with its page),
  `ingest` (LibreNMS, Alertmanager, generic webhook), `admin` (tokens, test notification,
  contract), `ops` (health, metrics, app status, MCP, endpoint list). `deps` holds what they
  share, `models` the typed queries and bodies, `answers` what each area's JSON looks like, `pages` and `files` the HTML and file answers.
  `tests/route_table.json` freezes every URL, method and role: `python -m tests.test_route_table`
  rewrites it after a deliberate change.
- `nautobot_maps/auth.py`: optional header-based authentication and roles
  (`@auth.require_role("operator")`).
- `nautobot_maps/timeutil.py`: time helpers; tests freeze time with
  `monkeypatch.setattr(timeutil, "iso_utc_now", ...)`.

Importing a module must not touch the database: `app.py` calls `db.init_db()` once at startup,
after logging is configured (a test enforces this).

Modules call each other as `module.function()`, not `from module import function`, so a test
can replace a function where it is defined.

### The frontend (#463)

Every page is built with [Nuxt](https://nuxt.com) (Vue, TypeScript, [Nuxt UI](https://ui.nuxt.com)) in `frontend/`. The backend serves the build and the API; the one page it renders itself is the error page (`templates/error.html`, so a 401, 403, 404 or 500 reads well even without a build).

```bash
cd frontend
npm ci              # Node 22
npm run generate    # the static build FastAPI serves: frontend/.output/public
npm run dev         # or: a dev server on :3000 with live reload; /api goes to python app.py on :5055, all but the /api/docs page
npm run lint && npm run typecheck && npm test
```

- `python app.py` serves the build; without one, every page answers 503 and says to build it (the API and the error page still work).
- `ssr: false`: a single-page app built to static files, no Node in production (the Docker image builds it in its own stage). A page's route answers `frontend.shell()`; its files come from `/_nuxt/`, cached for a year and public (code only, no data).
- CSP stays strict: `npm run generate` moves Nuxt's inline config script into a file and fails on any other inline script; Nuxt UI's colours are a stylesheet made at build time (`modules/static-ui-colors.ts`), not its inline `<style>`; no web fonts or icons are fetched from elsewhere.
- API types: `app/api/schema.d.ts` is generated from the app's response models. After an API change: `python frontend/scripts/openapi.py && (cd frontend && npm run api-types)`; `tests/test_frontend.py` and CI fail until then.
- Logic goes in `app/utils/` (plain functions, tested with Vitest in `frontend/tests/`); the pages themselves are tested end to end (`e2e/`).
- Scale: keep big lists (sites, devices) in `shallowRef`, not deep reactivity.

Frontend rules:

- No inline styles or scripts (#370): the CSP allows only the app's own files, so markup uses
  classes in `app/assets/css/main.css` (or Nuxt UI's), never `style="…"`; setting
  `element.style` from script is fine. `tests/test_no_inline_styles.py` checks the pages.
- Third-party code comes from npm, pinned, into the build (Leaflet and markercluster for the map,
  `app/utils/leafletmap.ts`; Swagger UI for `/api/docs`, loaded on that page only): no CDN.
- A rule the server and the browser share is tested on both sides against one fixture, e.g.
  `frontend/tests/fixtures/scope-cases.json` for `?tenant=` / `?q=` (`nautobot_maps/scope.py`
  and `app/utils/scope.ts`).

### Code Style

- Python code must pass `ruff check .` (configured in `pyproject.toml`; CI runs it on every PR).
  Many findings can be fixed automatically with `ruff check --fix .`.
- Python code must be formatted with `ruff format .` (CI runs `ruff format --check .`).
  The repo-wide formatting commit is listed in `.git-blame-ignore-revs`; run
  `git config blame.ignoreRevsFile .git-blame-ignore-revs` once so local `git blame` skips it.
- The frontend must pass `npm run lint` and `npm run typecheck` in `frontend/` (CI runs both).
- Write clear commit messages describing what changed and why.

### Database schema changes

The schema is versioned (#201). `db.baseline_schema()` is version 1 and **frozen** (a test fails if it changes). To change the schema, append a step to `db.MIGRATIONS`:

```python
def add_foo_column(conn) -> None:
    conn.execute("ALTER TABLE alert_instances ADD COLUMN foo TEXT NOT NULL DEFAULT ''")


MIGRATIONS = (
    (1, "baseline schema", baseline_schema),
    (2, "alert_instances.foo", add_foo_column),
)
```

Each step runs once, in order, inside one transaction with the other pending steps, and is recorded in `schema_migrations`. Never edit or reorder a step that may already have been applied. The container runs `python -m nautobot_maps migrate` before gunicorn starts; `python -m nautobot_maps schema-version` shows where a database is.

### Dependencies

Direct dependencies are listed in `requirements.in` (runtime) and `requirements-dev.in` (tests and linters). `requirements.txt` and `requirements-dev.txt` are **generated lock files**: every package, transitive ones included, pinned with hashes, so the Docker image and CI install exactly what was tested (#186). To add or change a dependency, edit the `.in` file and recompile:

```bash
pip install pip-tools
pip-compile --generate-hashes --strip-extras --allow-unsafe -o requirements.txt requirements.in
pip-compile --generate-hashes --strip-extras --allow-unsafe -o requirements-dev.txt requirements-dev.in
```

CI fails when a lock file doesn't match its `.in` file, or when `pip-audit`, `npm audit` or the Trivy image scan finds a known vulnerability. Dependabot proposes updates weekly.

### Tests

- Add tests for new functionality.
- Ensure all existing tests pass before submitting a pull request.
- Tests that need the database use the `pg_database` fixture (`tests/conftest.py`): each gets
  its own empty PostgreSQL schema in `TEST_DATABASE_URL`, dropped afterwards. Without
  `TEST_DATABASE_URL` those tests are skipped locally; in CI they fail instead.
- Both unit tests (mocked) and integration tests are welcome.

The suite:

- `tests/test_app.py`: unit tests, mock-based; persistence tests run against PostgreSQL.
- `tests/test_integration.py`: starts a local mock Nautobot and exercises the full HTTP stack;
  some tests run the page JavaScript in `node`.
- `tests/test_mcp.py`: the MCP server.
- `tests/test_nautobot_live.py`: skipped unless `NAUTOBOT_LIVE_URL` and `NAUTOBOT_LIVE_TOKEN`
  point at a real Nautobot (see `development/`).

### Screenshots

The README's pictures (`docs/images/`) come from `scripts/screenshots.py`, run against the
scale demo (`MOCK_SCALE=1200`, [demo/README.md](demo/README.md)) and an empty database; its
docstring has the steps. Run it again when a page they show changes.

### How the inventory sync works

Requests never call Nautobot or LibreNMS for the alert board: they read the snapshot in
PostgreSQL, which `/api/locations`, `/api/locations/<id>/detail` and `/api/alerts` prefer
as their source. The sync (`inventory.sync_nautobot()`, `inventory.sync_librenms()`) runs
from the scheduler, or is queued by a request once it is due; `?refresh=1` only queues it.

- Nautobot is pulled incrementally with `last_updated__gte=<watermark>`. Device pages use
  `depth=1` so `primary_ip4`/`primary_ip6` carry their address inline.
- The watermark only advances when an incremental pull sees newer upstream `last_updated`
  values; a full reconcile uses its start time.
- A change in the cached extraction version forces a full reconcile; a daily full reconcile
  removes objects deleted in Nautobot.
- LibreNMS refreshes on its own interval.
- On a fresh database the first `/api/alerts` request starts the initial sync in the
  background and returns `sync_pending: true`; the board re-polls until it clears.

## Releasing

Versions follow [Semantic Versioning](https://semver.org/) (a **Breaking** changelog entry means a new major version; before 1.0, a new minor version).

1. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and add a new empty `## [Unreleased]` above it.
2. Merge that to `main`.
3. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The `Release` workflow then builds the image, pushes `ghcr.io/jackass4life/nautobot-maps:X.Y.Z` (plus `X.Y` and `latest`) with provenance and an SBOM, and creates a GitHub Release with that changelog section. It refuses a tag without a matching changelog section. The first published image is private on GitHub; make the package public (package settings → visibility) or have deployments `docker login ghcr.io`.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
