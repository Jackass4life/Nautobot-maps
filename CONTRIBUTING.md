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
   ```
   Optionally, run the linter automatically on every commit:
   ```bash
   pip install pre-commit && pre-commit install
   ```
3. **Make** your changes in small, focused commits.
4. **Test** your changes:
   ```bash
   python -m pytest tests/ -v
   ```
5. **Update** `CHANGELOG.md`: add a line under `[Unreleased]` (Added / Changed / Fixed / Removed) for anything a user or operator notices — behaviour, settings, API, database tables. Pure refactors and test-only changes don't need one.
6. **Submit** a pull request against `main` with a clear description of the change.

### Development Setup

Install and run the app as in the [README](README.md); every setting is in [docs/configuration.md](docs/configuration.md). Without Docker: `pip install -r requirements-dev.txt`, copy `.env.example` to `.env`, then `python app.py`. For a real Nautobot 3.x to develop against, see [`development/README.md`](development/README.md).

#### Dev container (optional)

The repository ships a [dev container](https://containers.dev/) in `.devcontainer/`. Open the
folder in VS Code and choose **Reopen in Container** (requires Docker and the Dev Containers
extension), or start it in GitHub Codespaces. It provides:

- Python 3.11 (the oldest version CI tests) with `requirements-dev.txt` installed, so
  `pytest` and `ruff check .` work straight away
- PostgreSQL 16 on `localhost:5432` (user and password `nautobot_maps`), with a
  `nautobot_maps` database for the app and a `nautobot_maps_test` database for pytest
  (`TEST_DATABASE_URL` is preset). Data is kept in a Docker volume across rebuilds.
- Node, used by the JavaScript runtime checks in the test suite
- GitHub CLI (`gh`) and Claude Code
- The Python and Ruff VS Code extensions
- Port 5000 forwarded for `python app.py`. If 5000 is already taken on your machine, VS Code
  picks another local port; check the **Ports** view.

You still need a `.env` (see [docs/configuration.md](docs/configuration.md)) to point the app at a Nautobot instance; for the
alert board add `NAUTOBOT_MAPS_DATABASE_URL=postgresql://nautobot_maps:nautobot_maps@localhost:5432/nautobot_maps`.
Feature versions are pinned in `.devcontainer/devcontainer-lock.json`. After changes to
`.devcontainer/`, run **Dev Containers: Rebuild Container**.

### Project layout

`app.py` is the entry point (`app:app` for gunicorn): it creates the Flask app, sets up
logging, the cache and the database, registers the routes, and logs the startup settings.
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
- `nautobot_maps/caching.py`: the shared response cache (Flask-Caching).
- `nautobot_maps/http.py`: the HTTP session both clients use (one per thread, connections reused), with retries and backoff for transient upstream errors.
- `nautobot_maps/inventory.py`: the inventory sync — normalising Nautobot data, the cache tables,
  sync state, and `inventory.ensure_snapshot()` / `inventory.get_locations()`.
- `nautobot_maps/alerts.py`: the alert logic — severity scoring (`alerts.compute_alert_level()`),
  the LibreNMS status merge, alert history, the alert board (`alerts.get_alert_board_data()`) and
  the map's location detail.
- `nautobot_maps/scheduler.py`: the background scheduler (`scheduler.start()`, started by
  gunicorn in each worker).
- `nautobot_maps/web.py`: all routes and error handlers (a Flask blueprint, `web.bp`).
- `nautobot_maps/auth.py`: optional header-based authentication and roles
  (`@auth.require_role("operator")`).
- `nautobot_maps/timeutil.py`: time helpers; tests freeze time with
  `monkeypatch.setattr(timeutil, "iso_utc_now", ...)`.

Importing a module must not touch the database: `app.py` calls `db.init_db()` once at startup,
after logging is configured (a test enforces this).

Modules call each other as `module.function()`, not `from module import function`, so a test
can replace a function where it is defined.

### Code Style

- Python code must pass `ruff check .` (configured in `pyproject.toml`; CI runs it on every PR).
  Many findings can be fixed automatically with `ruff check --fix .`.
- Python code must be formatted with `ruff format .` (CI runs `ruff format --check .`).
  The repo-wide formatting commit is listed in `.git-blame-ignore-revs`; run
  `git config blame.ignoreRevsFile .git-blame-ignore-revs` once so local `git blame` skips it.
- JavaScript in `static/js/` must pass ESLint (`eslint.config.mjs`; CI runs it on every PR):
  `npm ci` once, then `npm run lint:js`. Keep it consistent with the existing style.
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

Versions follow [Semantic Versioning](https://semver.org/) (a **Breaking** changelog entry means a new major version).

1. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and add a new empty `## [Unreleased]` above it.
2. Merge that to `main`.
3. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The `Release` workflow then builds the image, pushes `ghcr.io/jackass4life/nautobot-maps:X.Y.Z` (plus `X.Y` and `latest`) with provenance and an SBOM, and creates a GitHub Release with that changelog section. It refuses a tag without a matching changelog section. The first published image is private on GitHub; make the package public (package settings → visibility) or have deployments `docker login ghcr.io`.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
