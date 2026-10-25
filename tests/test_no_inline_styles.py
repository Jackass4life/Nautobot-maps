"""No inline styles (#370): the CSP's style-src is 'self' only, so a style
attribute or <style> block would be blocked in the browser (and would mean an
HTML injection could restyle the page again).  This keeps it that way."""

import pathlib
import re

import pytest

from nautobot_maps import settings
from tests import http_client

ROOT = pathlib.Path(__file__).resolve().parent.parent
# A style attribute in markup (quoted, unquoted or interpolated), a <style>
# block, or one set as an attribute from script (element.style.x is fine).
INLINE = re.compile(
    r"""<[a-z][^>]*\sstyle\s*=|<style[\s>]|setAttribute\(\s*["']style["']""",
    re.IGNORECASE,
)


def _sources():
    yield from (ROOT / "templates").rglob("*.html")
    yield from (ROOT / "nautobot_maps").rglob("*.py")


@pytest.mark.parametrize("path", sorted(_sources()), ids=lambda path: str(path.relative_to(ROOT)))
def test_no_inline_style_in_the_source(path):
    found = [line for line in path.read_text().splitlines() if INLINE.search(line)]
    assert not found, f"{path.relative_to(ROOT)}: use a class in a CSS file instead of {found[0].strip()!r}"


def test_the_pattern_catches_what_the_csp_blocks():
    for markup in (
        '<div style="x">',
        "<li style='x'>",
        "<div class=a style=${css}>",
        "<style>",
        'el.setAttribute("style", s)',
    ):
        assert INLINE.search(markup), markup
    for fine in ('el.style.left = "4px"', "const style = 1", '<div class="style-x">', "style-src 'self'"):
        assert not INLINE.search(fine), fine


@pytest.fixture
def client(monkeypatch, frontend_build):
    # The status page is the Nuxt build's (#463): tests/test_frontend.py checks the real one.
    monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
    with http_client.FlaskStyleClient() as test_client:
        yield test_client


# (page, the status it answers here without a database): an error page must
# not stand in for the page itself.
PAGES = [
    ("/", 200),
    ("/alerts", 200),
    ("/alerts?view=wall", 200),
    ("/maintenance", 200),
    ("/api/docs", 200),
    ("/status/Acme%20Corp", 200),
    ("/no-such-page", 404),
]


@pytest.mark.parametrize(("path", "status"), PAGES)
def test_no_inline_style_on_a_page(client, path, status):
    resp = client.get(path)
    html = resp.get_data(as_text=True)
    assert resp.status_code == status, (path, resp.status_code)
    assert "<html" in html and not INLINE.search(html), path


def test_the_marker_colours_are_the_levels_colours():
    """main.css level-bg-* (quiet dots and clusters) and LEVEL_COLORS (pins) stay one palette (#463)."""
    css = (ROOT / "frontend" / "app" / "assets" / "css" / "main.css").read_text()
    in_css = dict(re.findall(r"\.level-bg-([a-z_]+)\s*\{\s*background:\s*(#[0-9a-fA-F]{3,6})\s*;", css))
    # The legend's dots share each rule ("dot-no-data" as the templates spell it).
    for level in in_css:
        assert re.search(rf"\.dot-{level.replace('_', '-')},[^{{]*\.level-bg-{level}\s*\{{", css), level
    source = (ROOT / "frontend" / "app" / "utils" / "map.ts").read_text()
    block = source.split("export const LEVEL_COLORS", 1)[1].split("};", 1)[0]
    in_ts = dict(re.findall(r"(\w+):\s*\"(#[0-9a-fA-F]{3,6})\"", block))
    assert in_ts and {level: colour.lower() for level, colour in in_ts.items()} == {
        level: colour.lower() for level, colour in in_css.items()
    }


def test_the_error_pages_styles_load_without_signing_in(client, monkeypatch):
    """The error page links error.css: public, as it carries no data (#370); other paths still need sign-in."""
    monkeypatch.setattr(settings, "AUTH_MODE", "header")
    monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
    assert client.get("/static/css/error.css").status_code == 200
    assert client.get("/static/css/other.css").status_code == 401
