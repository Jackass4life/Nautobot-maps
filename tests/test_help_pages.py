"""The documentation as pages in the app, /help (#433)."""

import re

from nautobot_maps import guide


def test_every_guide_renders_with_a_title(client, frontend_build):
    # The page is on Nuxt (#463): it reads /api/help/<slug>.
    assert [item["slug"] for item in guide.contents()] == [slug for slug, _ in guide.PAGES]
    for item in guide.contents():
        assert client.get(item["href"]).status_code == 200, item
        answer = client.get(f"/api/help/{item['slug']}").get_json()
        assert answer["title"] == item["title"] and answer["html"].startswith("<h1")
        assert answer["contents"] == guide.contents()
    assert client.get("/api/help/nope").status_code == 404


def test_links_between_the_docs_open_their_page():
    html = guide.page("overview")["html"]
    assert 'href="/help/how-it-works#circuit-mapping"' in html
    assert 'src="/help/images/map.png"' in html
    # From docs/: a sibling, and README one level up.
    assert guide.help_href("configuration.md#auth", "docs/authentication.md") == "/help/configuration#auth"
    assert guide.help_href("../README.md", "docs/how-it-works.md") == "/help"
    assert guide.help_href("../CONTRIBUTING.md", "docs/how-it-works.md") == "/help/contributing"
    assert guide.help_href("https://example.com/x", "README.md") == "https://example.com/x"
    assert guide.help_href("#setup", "README.md") == "#setup"
    # A repository file the app doesn't serve: plain text, not a broken link.
    assert guide.help_href(".env.example", "README.md") is None
    assert guide.help_href("../nautobot_maps/settings.py", "docs/configuration.md") is None


def test_every_internal_link_has_its_heading():
    """Each /help link with an anchor lands on a heading of that page."""
    anchors = {}
    for slug, _ in guide.PAGES:
        anchors[slug] = set(re.findall(r'<h[1-6] id="([^"]+)"', guide.page(slug)["html"]))
    broken = []
    for slug, _ in guide.PAGES:
        for target in re.findall(r'href="(/help[^"]*)"', guide.page(slug)["html"]):
            path, _, fragment = target.partition("#")
            page = path.removeprefix("/help").strip("/") or "overview"
            if page.startswith("images/"):
                continue
            assert page in anchors, (slug, target)
            if fragment and fragment not in anchors[page]:
                broken.append((slug, target))
    assert broken == []


def test_github_anchors():
    seen = {}
    assert guide.heading_anchor("Sign-in and roles", seen) == "sign-in-and-roles"
    assert guide.heading_anchor("What's `AUTH_MODE`? (#188)", seen) == "whats-auth_mode-188"
    assert guide.heading_anchor("Setup", seen) == "setup"
    assert guide.heading_anchor("Setup", seen) == "setup-1"


def test_no_markup_or_inline_style_gets_through(monkeypatch, tmp_path):
    doc = tmp_path / "x.md"
    doc.write_text(
        "# T\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n| a | b |\n|--:|:-:|\n| 1 | 2 |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(guide, "ROOT", tmp_path)
    monkeypatch.setattr(guide, "FILE_BY_SLUG", {"x": "x.md"})
    html = guide.page("x")["html"]
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'href="javascript' not in html
    assert not re.search(r"<[^>]+\sstyle=", html)
    assert '<th class="align-right">' in html and '<td class="align-center">' in html
    for slug, _ in guide.PAGES:
        monkeypatch.undo()
        assert not re.search(r"<[^>]+\sstyle=", guide.page(slug)["html"]), slug


def test_images_and_unknown_pages(client, frontend_build):
    image = client.get("/help/images/map.png")
    assert image.status_code == 200 and image.mimetype == "image/png"
    assert client.get("/help/images/../../app.py").status_code == 404
    assert client.get("/help/images/..%2F..%2Fapp.py").status_code == 404
    assert client.get("/help/nope").status_code == 404


def test_the_menu_links_to_it(client):
    pages = client.get("/api/page-config").get_json()["pages"]
    assert {"key": "help", "href": "/help", "label": "Documentation"}.items() <= next(
        p for p in pages if p["key"] == "help"
    ).items()


def test_the_docs_go_into_the_image():
    ignore = (guide.ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "*.md" in ignore and {"!README.md", "!CONTRIBUTING.md", "!docs/*.md"} <= set(ignore)
    assert ignore.index("!docs/*.md") > ignore.index("*.md")
