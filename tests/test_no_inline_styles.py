"""No inline styles (#370): the CSP's style-src is 'self' only, so a style
attribute or <style> block would be blocked in the browser (and would mean an
HTML injection could restyle the page again).  This keeps it that way."""

import pathlib
import re

import pytest

import app as flask_app
from nautobot_maps import settings
from tests.js_modules import run_js

ROOT = pathlib.Path(__file__).resolve().parent.parent
# A style attribute in markup (quoted, unquoted or interpolated), a <style>
# block, or one set as an attribute from script (element.style.x is fine).
INLINE = re.compile(
    r"""<[a-z][^>]*\sstyle\s*=|<style[\s>]|setAttribute\(\s*["']style["']""",
    re.IGNORECASE,
)


def _sources():
    yield from (ROOT / "templates").rglob("*.html")
    yield from (ROOT / "static" / "js").rglob("*.js")
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
def client(monkeypatch):
    monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as test_client:
        yield test_client


# (page, the status it answers here without a database): an error page must
# not stand in for the page itself.
PAGES = [
    ("/", 200),
    ("/alerts", 200),
    ("/alerts?view=wall", 200),
    ("/maintenance", 200),
    ("/docs", 200),
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
    """map.css level-bg-* (quiet dots and clusters) and LEVEL_COLORS (pins) stay one palette."""
    css = (ROOT / "static" / "css" / "map.css").read_text()
    in_css = dict(re.findall(r"\.level-bg-([a-z_]+)\s*\{\s*background:\s*(#[0-9a-fA-F]{3,6})\s*;", css))
    run_js(
        f"""
import {{ LEVEL_COLORS }} from "@js/map/levels.js";
const css = {in_css!r};
check(JSON.stringify(Object.keys(LEVEL_COLORS).sort()) === JSON.stringify(Object.keys(css).sort()),
  `levels: ${{Object.keys(LEVEL_COLORS)}} vs css ${{Object.keys(css)}}`);
for (const [level, colour] of Object.entries(LEVEL_COLORS)) {{
  check(css[level].toLowerCase() === colour.toLowerCase(), `${{level}}: map.css ${{css[level]}}, levels.js ${{colour}}`);
}}
"""
    )


def test_marker_markup():
    run_js(
        """
import { knownLevel, makeClusterIcon, makeStackedIcon, pinIcon, quietDotIcon } from "@js/map/icons.js";

setGlobal("L", { divIcon: (options) => options });
for (const icon of [pinIcon("critical"), makeClusterIcon(150, "ok"), quietDotIcon("no_data", 3), makeStackedIcon(2, "medium")]) {
  check(!/style=/.test(icon.html), "no style attribute: " + icon.html);
}
const cluster = makeClusterIcon(150, "ok");
check(cluster.html.startsWith('<div class="quiet-cluster level-bg-ok"') && cluster.iconSize.join() === "36,36", cluster.html);
check(makeClusterIcon(5, "nonsense").html.includes("level-bg-no_data"), "an unknown level: No data");
check(quietDotIcon("ok").html === '<div class="quiet-dot level-bg-ok"></div>' && quietDotIcon("ok").iconSize.join() === "12,12",
  quietDotIcon("ok").html);
check(pinIcon("critical").html.startsWith('<div class="pin-icon marker-pulse">'), pinIcon("critical").html);
check(makeStackedIcon(2, "medium").html.startsWith('<div class="pin-icon pin-stacked">'), "stacked");
check(knownLevel("constructor") === "no_data" && knownLevel("toString") === "no_data", "not Object.prototype's keys");
check(pinIcon("constructor").html.includes('fill="#94a3b8"'), "a pin for an unknown level: No data's colour");
"""
    )
