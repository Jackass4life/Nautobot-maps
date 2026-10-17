"""No inline styles (#370): the CSP's style-src is 'self' only, so a style
attribute or <style> block would be blocked in the browser (and would mean an
HTML injection could restyle the page again).  This keeps it that way."""

import pathlib
import re

import pytest

import app as flask_app
from nautobot_maps import settings

ROOT = pathlib.Path(__file__).resolve().parent.parent
INLINE = re.compile(r"""\sstyle\s*=\s*["']|<style[\s>]""", re.IGNORECASE)


def _sources():
    yield from (ROOT / "templates").glob("*.html")
    yield from (path for path in (ROOT / "static" / "js").rglob("*.js"))
    yield from (ROOT / "nautobot_maps").glob("*.py")


@pytest.mark.parametrize("path", sorted(_sources()), ids=lambda path: str(path.relative_to(ROOT)))
def test_no_inline_style_in_the_source(path):
    found = [line for line in path.read_text().splitlines() if INLINE.search(line)]
    assert not found, f"{path.relative_to(ROOT)}: use a class in a CSS file instead of {found[0].strip()!r}"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as test_client:
        yield test_client


@pytest.mark.parametrize(
    "path", ["/", "/alerts", "/alerts?view=wall", "/availability", "/maintenance", "/docs", "/no-such-page"]
)
def test_no_inline_style_on_a_page(client, path):
    html = client.get(path).get_data(as_text=True)
    assert "<html" in html and not INLINE.search(html), path
