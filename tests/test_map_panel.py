"""One top bar for every page (#392, #419).  The map's own panel (search,
filters, the folding panel) is on Nuxt since #463: frontend/tests/map.test.ts
and e2e/."""

from tests.js_modules import run_js


def test_one_top_bar_on_every_page(client):
    # The server-rendered pages; the Nuxt ones (the map, /circuits, /maintenance, /availability, /help, #463) have AppBar.vue (e2e).
    for path, active in (
        ("/alerts", "/alerts"),
        ("/docs", "/docs"),
    ):
        html = client.get(path).get_data(as_text=True)
        assert html.count('class="app-bar"') == 1 and html.count('class="app-menu app-popover"') == 1, path
        assert 'id="theme-toggle"' in html and 'href="/static/css/nav.css' in html, path
        assert f'aria-current="page" href="{active}"' in html, path
        assert html.count('aria-current="page"') >= 1, path


def test_the_map_and_board_keep_a_links_scope(client):
    # The board's bar; the map's (Nuxt, #463) is tested end to end.
    html = client.get("/alerts?tenant=Acme%20Corp").get_data(as_text=True)
    assert 'href="/?tenant=Acme+Corp"' in html
    assert 'href="/alerts?view=wall&amp;tenant=Acme+Corp"' in html


def test_the_theme_button_is_an_icon_with_a_label():
    run_js(
        """
import { applyTheme } from "@js/lib/theme.js";
const button = element("theme", { textContent: "keep" });
applyTheme("dark", button);
check(button.attrs["aria-label"] === "Switch to light mode" && button.title === "Light mode", button.title);
check(button.textContent === "keep", "the icon stays");
applyTheme("light", button);
check(button.attrs["aria-label"] === "Switch to dark mode" && button.title === "Dark mode", button.title);
"""
    )
