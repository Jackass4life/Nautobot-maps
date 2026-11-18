"""The app's folders (#631), as in the example layout: api.py and lifespan.py, then
core/, db/, context/, logs/, auth/ next to v1/; run.py and scripts/ outside app/."""

import ast
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
        "core/legacy_settings.py",
        "utils/errors.py",
        "utils/requestctx.py",
        "utils/logs.py",
        "utils/access_log.py",
        "utils/auth.py",
    ):
        assert not (APP / path).exists(), path


def _settings_read_outside_a_function(tree: ast.AST) -> list[int]:
    """Lines reading a setting (``settings.NAME``) that run at import: in a module or
    class body, a default value or a decorator."""
    found = []

    def visit(node: ast.AST, in_body: bool) -> None:
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "settings"
        ):
            if not in_body:
                found.append(node.lineno)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for child in ast.iter_child_nodes(node):
                body = node.body if isinstance(node.body, list) else [node.body]
                visit(child, in_body or child in body)
            return
        if isinstance(node, ast.ClassDef):
            in_body = False
        for child in ast.iter_child_nodes(node):
            visit(child, in_body)

    visit(tree, False)
    return found


def test_no_setting_is_read_at_import():
    """settings.NAME inside the function that needs it (#745, #754): read at import, a
    setting would be copied before a test changes the shared object."""
    offenders = [
        f"{path.relative_to(API_ROOT)}:{line}"
        for path in sorted(APP.rglob("*.py"))
        for line in _settings_read_outside_a_function(ast.parse(path.read_text()))
    ]
    assert offenders == []


def test_the_import_guard_finds_a_read_at_import():
    code = (
        "A = settings.X\n"
        "class C:\n    b = settings.Y\n"
        "def f(c=settings.Z):\n    return settings.W\n"
        "g = lambda: settings.V\n"
    )
    assert _settings_read_outside_a_function(ast.parse(code)) == [1, 3, 4]


def test_settings_are_read_by_their_environment_names():
    """settings.NAUTOBOT_URL, never settings.nautobot_url (#760): a field is named like its
    environment variable, so a lowercase read is a mistake (an AttributeError at run time)."""
    from app.core import settings as module
    from app.core.settings import Settings

    # Methods, and the module's own names where a test imports the module as settings.
    methods = {name for name in dir(Settings) if not name.startswith("_")} | set(dir(module))
    offenders = [
        f"{path.relative_to(API_ROOT)}:{node.lineno} settings.{node.attr}"
        for path in sorted([*APP.rglob("*.py"), *(API_ROOT / "tests").rglob("*.py")])
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "settings"
        and node.attr not in Settings.model_fields
        and node.attr not in methods
    ]
    assert offenders == []


def test_the_version_is_pyprojects():
    with (API_ROOT / "pyproject.toml").open("rb") as file:
        expected = tomllib.load(file)["project"]["version"]
    assert version.version() == expected
    assert app.version == expected
