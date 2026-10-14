"""The browser's ES modules hold together (#294).

The entry modules (each page's main.js) run only in a browser, so nothing
else would notice an import of a file that doesn't exist, or a module that
touches the page or Leaflet when imported.
"""

import re

import pytest

from nautobot_maps import auth
from tests.js_modules import JS_ROOT, REPO_ROOT, run_js

IMPORT = re.compile(r'(?:from|import)\s+"(\.{1,2}/[^"]+)"')


def page_scripts():
    """Every script the templates load from static/js, by its path there."""
    scripts = set()
    for template in (REPO_ROOT / "templates").glob("*.html"):
        # Any case: a <SCRIPT> tag must not slip past the check (CodeQL).
        for tag in re.findall(r"<script\b[^>]*>", template.read_text(encoding="utf-8"), re.IGNORECASE):
            match = re.search(r"filename='js/([^']+)'", tag)
            if match:
                assert 'type="module"' in tag, f"{template.name}: {tag}"
                scripts.add(match.group(1))
    return scripts


def imported_files(entry):
    """*entry* and every file it imports, directly or not (paths in static/js)."""
    seen, todo = set(), [JS_ROOT / entry]
    while todo:
        path = todo.pop().resolve()
        if path in seen:
            continue
        assert path.is_file(), f"{path.relative_to(JS_ROOT)} is imported but doesn't exist"
        seen.add(path)
        todo.extend(path.parent / spec for spec in IMPORT.findall(path.read_text(encoding="utf-8")))
    return {path.relative_to(JS_ROOT).as_posix() for path in seen}


def test_every_page_loads_a_module_and_its_imports_exist():
    scripts = page_scripts()
    assert scripts == {
        "alerts/main.js",
        "availability.js",
        "docs/main.js",
        "maintenance/main.js",
        "map/main.js",
        "status/main.js",
    }
    used = set().union(*(imported_files(entry) for entry in scripts))
    every = {path.relative_to(JS_ROOT).as_posix() for path in JS_ROOT.rglob("*.js")}
    assert every - used == set(), "modules no page loads"


def test_the_status_page_assets_are_what_it_loads():
    """A customer's browser loads these without signing in (#291): no more, no less."""
    expected = {f"/static/js/{path}" for path in imported_files("status/main.js")} | {"/static/css/alerts.css"}
    assert auth.STATUS_PAGE_ASSETS == expected


@pytest.mark.parametrize(
    "module",
    sorted(
        path.relative_to(JS_ROOT).as_posix()
        for path in JS_ROOT.rglob("*.js")
        if path.name != "main.js" and path.name != "availability.js"
    ),
)
def test_a_module_imports_without_a_page(module):
    """Only the entry modules touch the page (or Leaflet) when loaded."""
    run_js(f"""
document.getElementById = () => {{ throw new Error("touched the page when imported"); }};
document.querySelector = document.getElementById;
document.querySelectorAll = document.getElementById;
await import("@js/{module}");
""")
