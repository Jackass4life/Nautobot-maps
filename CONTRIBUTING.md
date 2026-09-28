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
5. **Submit** a pull request against `main` with a clear description of the change.

### Development Setup

See the [README](README.md) for detailed setup instructions, including Docker-based development with a local Nautobot instance.

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

You still need a `.env` (see the README) to point the app at a Nautobot instance; for the
alert board add `NAUTOBOT_MAPS_DATABASE_URL=postgresql://nautobot_maps:nautobot_maps@localhost:5432/nautobot_maps`.
Feature versions are pinned in `.devcontainer/devcontainer-lock.json`. After changes to
`.devcontainer/`, run **Dev Containers: Rebuild Container**.

### Project layout

`app.py` is the entry point (`app:app` for gunicorn): it creates the Flask app, sets up
logging, the cache and the database, registers the routes, and logs the startup settings.
Everything else lives in modules under `nautobot_maps/` (#165):

- `nautobot_maps/settings.py`: every setting read from the environment (`.env`). Add new
  settings here, pass them in `docker-compose.yml` (a test checks this), and read them as
  `settings.NAME` at call time; in tests change them with
  `monkeypatch.setattr(settings, "NAME", value)`.
- `nautobot_maps/db.py`: PostgreSQL connections (`db.get_conn()`, `db.transaction()`), the
  schema and its migrations (`db.init_db()`), and small SQL helpers.
- `nautobot_maps/nautobot.py`: the Nautobot REST client (`nautobot.get()`, `nautobot.post()`,
  `nautobot.delete()`, `nautobot.fetch_all_pages()`) and the id → name lookup maps.
- `nautobot_maps/librenms.py`: the LibreNMS REST client (`librenms.get()`,
  `librenms.fetch_inventory()`).
- `nautobot_maps/caching.py`: the shared response cache (Flask-Caching).
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

### Tests

- Add tests for new functionality.
- Ensure all existing tests pass before submitting a pull request.
- Tests that need the database use the `pg_database` fixture (`tests/conftest.py`): each gets
  its own empty PostgreSQL schema in `TEST_DATABASE_URL`, dropped afterwards. Without
  `TEST_DATABASE_URL` those tests are skipped locally; in CI they fail instead.
- Both unit tests (mocked) and integration tests are welcome.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
