"""The app's folders (#631), as in the example layout: api.py and lifespan.py, then
core/, db/, context/, logs/, auth/ next to v1/; run.py and scripts/ outside app/."""

import tomllib
from pathlib import Path

from app.api import app
from app.core import version

API_ROOT = Path(__file__).resolve().parent.parent
APP = API_ROOT / "app"


def test_the_core_folders_hold_what_they_should():
    for path in (
        "api.py",
        "lifespan.py",
        "core/settings.py",
        "core/legacy_settings.py",
        "core/exceptions.py",
        "core/cors.py",
        "core/version.py",
        "db/connection.py",
        "db/session.py",
        "db/middleware.py",
        "context/request.py",
        "logs/config.py",
        "logs/middleware.py",
        "auth/dependencies.py",
    ):
        assert (APP / path).is_file(), path
    assert (API_ROOT / "run.py").is_file()
    assert (API_ROOT / "scripts" / "generate_openapi.py").is_file()


def test_the_old_places_are_gone():
    for path in (
        "main.py",
        "db.py",
        "serve.py",
        "export_openapi.py",
        "session",
        "utils/config.py",
        "utils/legacy_settings.py",
        "utils/errors.py",
        "utils/requestctx.py",
        "utils/logs.py",
        "utils/access_log.py",
        "utils/auth.py",
    ):
        assert not (APP / path).exists(), path


def test_the_version_is_pyprojects():
    with (API_ROOT / "pyproject.toml").open("rb") as file:
        expected = tomllib.load(file)["project"]["version"]
    assert version.version() == expected
    assert app.version == expected
