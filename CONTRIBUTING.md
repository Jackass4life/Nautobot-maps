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
- Node, used by the JavaScript runtime checks in the test suite
- GitHub CLI (`gh`) and Claude Code
- The Python and Ruff VS Code extensions
- Port 5000 forwarded for `python app.py`. If 5000 is already taken on your machine, VS Code
  picks another local port; check the **Ports** view.

You still need a `.env` (see the README) to point the app at a Nautobot instance. Feature
versions are pinned in `.devcontainer/devcontainer-lock.json`.

### Code Style

- Python code must pass `ruff check .` (configured in `pyproject.toml`; CI runs it on every PR).
  Many findings can be fixed automatically with `ruff check --fix .`.
- Python code must be formatted with `ruff format .` (CI runs `ruff format --check .`).
  The repo-wide formatting commit is listed in `.git-blame-ignore-revs`; run
  `git config blame.ignoreRevsFile .git-blame-ignore-revs` once so local `git blame` skips it.
- Keep JavaScript consistent with the existing style in `static/js/`.
- Write clear commit messages describing what changed and why.

### Tests

- Add tests for new functionality.
- Ensure all existing tests pass before submitting a pull request.
- Both unit tests (mocked) and integration tests are welcome.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
