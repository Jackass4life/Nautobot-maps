"""The pages built with Nuxt (#463): FastAPI serves the build from the same
origin, under the same CSP, and the frontend's API types stay current."""

import gzip
import importlib.util
import pathlib

from nautobot_maps.api import frontend

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _spec_script():
    spec = importlib.util.spec_from_file_location("frontend_openapi", ROOT / "frontend" / "scripts" / "openapi.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_frontends_api_spec_is_current():
    # The frontend's types (app/api/schema.d.ts) are made from this file; CI's
    # frontend job checks they are.  After an API change:
    #   python frontend/scripts/openapi.py && (cd frontend && npm run api-types)
    current = _spec_script().spec_text()
    assert (ROOT / "frontend" / "openapi.json").read_text() == current, "run frontend/scripts/openapi.py"


def test_a_page_is_the_build_and_never_cached(client, frontend_build):
    page = client.get("/status/Acme%20Corp")
    assert page.status_code == 200 and 'id="__nuxt"' in page.text
    assert page.headers["Cache-Control"] == "no-cache"
    csp = page.headers["Content-Security-Policy"]
    assert "script-src 'self';" in csp and "style-src 'self';" in csp and "unsafe-inline" not in csp


def test_its_files_are_kept_a_year_and_gzipped(client, frontend_build):
    plain = client.get("/_nuxt/entry.abc123.js")
    assert plain.status_code == 200 and plain.headers["Cache-Control"] == "public, max-age=31536000"
    assert plain.headers["content-type"].startswith("text/javascript")
    zipped = client.get("/_nuxt/entry.abc123.js", headers={"Accept-Encoding": "gzip"})
    assert zipped.headers["Content-Encoding"] == "gzip" and gzip.decompress(zipped.data) == plain.data
    assert client.get("/_nuxt/../200.html").status_code == 404
    assert client.get("/_nuxt/missing.js").status_code == 404


def test_not_built_says_so(client, tmp_path, monkeypatch):
    monkeypatch.setattr(frontend, "BUILD", tmp_path / "nothing")
    page = client.get("/status/Acme%20Corp")
    assert page.status_code == 503 and "npm run generate" in page.text


def test_the_built_pages_have_no_inline_script_or_style():
    """When the frontend is built here: the CSP allows no inline script
    (frontend/scripts/no-inline-scripts.mjs moves Nuxt's into a file) or style."""
    import re

    build = ROOT / "frontend" / ".output" / "public"
    if not build.is_dir():
        import pytest

        pytest.skip("frontend not built (npm run generate); CI's frontend job checks it")
    for page in build.glob("*.html"):
        html = page.read_text()
        for attrs in re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", html):
            assert 'type="application/json"' in attrs, f"{page.name}: inline script {attrs}"
        # Nor inline style (style-src 'self'): Nuxt UI's colours are a file.
        assert "<style" not in html and " style=" not in html, page.name
