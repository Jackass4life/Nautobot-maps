"""The documentation as pages in the app, /help (#433)."""

import re

from app.services import guide_service as guide


def test_every_guide_renders_with_a_title(flask_client, frontend_build):
    # The page is on Nuxt (#463): it reads /api/help/<slug>.
    assert [item["slug"] for item in guide.contents()] == [slug for slug, _ in guide.PAGES]
    for item in guide.contents():
        assert flask_client.get(item["href"]).status_code == 200, item
        answer = flask_client.get(f"/api/v1/help/{item['slug']}").get_json()
        assert answer["title"] == item["title"] and answer["html"].startswith("<h1")
        assert answer["contents"] == guide.contents()
    assert flask_client.get("/api/v1/help/nope").status_code == 404


def test_the_guides_in_the_order_the_app_is_set_up():
    """Grouped (#733); every guide in docs/ is listed, and the README's list
    follows the same order."""
    groups = [group for group, _slug, _file in guide.GUIDES]
    assert list(dict.fromkeys(groups)) == ["Start", "Set up", "Use", "Connect more", "Reference"]
    # Each group in one run, not split.
    assert groups == sorted(groups, key=list(dict.fromkeys(groups)).index)
    assert [item["group"] for item in guide.contents()] == groups
    docs = {f"docs/{path.name}" for path in (guide.ROOT / "docs").glob("*.md")}
    assert docs <= set(guide.FILE_BY_SLUG.values())
    readme = (guide.ROOT / "README.md").read_text(encoding="utf-8")
    listed = re.findall(r"^\| \[[^]]+\]\((docs/[\w-]+\.md|CONTRIBUTING\.md)\)", readme, re.M)
    in_order = [file for _slug, file in guide.PAGES if file != "README.md"]
    assert listed == in_order


def test_the_code_guide_names_only_files_that_exist():
    """docs/code-guide.md (#735) names files and folders in backticks, from the
    repository root or from api/, api/app/, web/, web/pages/ or reverse-proxy/ (a bare
    file name: anywhere in the code): each must still be there, so the guide can't
    quietly go out of date."""
    text = (guide.ROOT / "docs" / "code-guide.md").read_text(encoding="utf-8")
    bases = [
        guide.ROOT / base
        for base in (
            "",
            "api",
            "api/app",
            "web",
            "web/pages",
            "reverse-proxy",
            "reverse-proxy/templates",
        )
    ]
    named = {
        name
        for name in re.findall(
            r"`([\w.\-\[\]]+(?:/[\w.\-\[\]]*)+|[\w\-]+\.(?:py|vue|ts|sh|yml|template))`", text
        )
        if not name.startswith("/")
    }
    assert len(named) > 60
    # A bare file name (``SitePanel.vue``) is in the folder named next to it.
    files = {
        path.name
        for folder in ("api/app", "api/tests", "web", "reverse-proxy", "demo")
        for path in (guide.ROOT / folder).rglob("*")
        if "node_modules" not in path.parts and ".output" not in path.parts
    }
    missing = sorted(
        name
        for name in named
        if not any((base / name).exists() for base in bases) and ("/" in name or name not in files)
    )
    assert not missing, missing


def test_links_between_the_docs_open_their_page():
    html = guide.page("overview")["html"]
    assert 'href="/help/how-it-works#circuit-mapping"' in html
    assert 'src="/help/images/map.png"' in html
    # From docs/: a sibling, and README one level up.
    assert (
        guide.help_href("configuration.md#auth", "docs/authentication.md")
        == "/help/configuration#auth"
    )
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


def test_images_and_unknown_pages(flask_client, frontend_build):
    image = flask_client.get("/help/images/map.png")
    assert image.status_code == 200 and image.mimetype == "image/png"
    assert flask_client.get("/help/images/../../app.py").status_code == 404
    assert flask_client.get("/help/images/..%2F..%2Fapp.py").status_code == 404
    assert flask_client.get("/help/nope").status_code == 404


def test_the_docs_go_into_the_image():
    """Every file the help pages read reaches the API image (#674): api/Dockerfile takes
    README.md, CONTRIBUTING.md and docs/ from the docs context (the repository root),
    and the root .dockerignore, an allow-list for that context, lets them through."""
    dockerfile = (guide.ROOT / "api" / "Dockerfile").read_text(encoding="utf-8")
    allowed = {
        line.strip().removeprefix("!").rstrip("/")
        for line in (guide.ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("!")
    }
    root_files = re.search(r"for file in ([^;]+); do", dockerfile).group(1).split()
    assert "cp -r /in/docs/docs/. /out/repo/docs/" in dockerfile
    for _slug, file in guide.PAGES:
        top = file.split("/")[0]
        assert top in allowed, file
        assert file in root_files or top == "docs", file


def test_an_image_without_the_docs_says_so(flask_client, monkeypatch, tmp_path):
    """An API image built without the docs context (api/Dockerfile, #672): every guide
    is missing, and the page says how to include them, not "No such guide"."""
    from app.services import guide_service as guide

    monkeypatch.setattr(guide, "ROOT", tmp_path)
    answer = flask_client.get("/api/v1/help/overview")
    assert answer.status_code == 404
    assert answer.get_json() == {"error": guide.NOT_INCLUDED}
    assert "--build-context docs=.." in guide.NOT_INCLUDED
