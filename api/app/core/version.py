"""The app's version: ``api/pyproject.toml``'s, the one place a release changes it.
Read from the file, not the package's metadata: the image installs only the
dependencies (``uv sync --no-install-project``), so there is no metadata."""

import tomllib
from functools import cache
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


@cache
def version() -> str:
    with PYPROJECT.open("rb") as file:
        return tomllib.load(file)["project"]["version"]
