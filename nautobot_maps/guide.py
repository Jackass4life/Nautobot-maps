"""The documentation as pages in the app, /help (#433).

README.md, docs/*.md and CONTRIBUTING.md rendered from Markdown with
markdown-it-py: raw HTML off (nothing in a doc can inject markup), headings
with GitHub's anchors so ``docs/x.md#heading`` links keep working, links
between the docs and images rewritten to /help, and no inline style (the
CSP, #370).  Each file is rendered once and cached until it changes.
"""

import pathlib
import posixpath
import re
import threading
import unicodedata
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

ROOT = pathlib.Path(__file__).resolve().parent.parent
IMAGES = ROOT / "docs" / "images"

# (slug, file): the order of the list of guides; the first is /help itself.
PAGES = (
    ("overview", "README.md"),
    ("how-it-works", "docs/how-it-works.md"),
    ("alert-board", "docs/alert-board.md"),
    ("authentication", "docs/authentication.md"),
    ("configuration", "docs/configuration.md"),
    ("operations", "docs/operations.md"),
    ("notifications", "docs/notifications.md"),
    ("availability", "docs/availability.md"),
    ("status-page", "docs/status-page.md"),
    ("mcp", "docs/mcp.md"),
    ("sources", "docs/sources.md"),
    ("data-contract", "docs/data-contract.md"),
    ("contributing", "CONTRIBUTING.md"),
)
SLUG_BY_FILE = {file: slug for slug, file in PAGES}
FILE_BY_SLUG = dict(PAGES)

_ALIGN = re.compile(r"text-align:\s*(left|right|center)")
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def heading_anchor(text: str, seen: dict[str, int]) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces
    as hyphens; a repeated one gets -1, -2."""
    slug = "".join(
        ch for ch in unicodedata.normalize("NFC", text.strip().lower()) if ch.isalnum() or ch in " -_"
    ).replace(" ", "-")
    count = seen.get(slug, 0)
    seen[slug] = count + 1
    return f"{slug}-{count}" if count else slug


def help_href(target: str, from_file: str) -> str | None:
    """Where a link in *from_file* goes in the app: another doc's page, an
    image, an anchor or an outside URL as it is; None for a repository file
    the app doesn't serve (the link becomes plain text)."""
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or target.startswith("#"):
        return target
    path = posixpath.normpath(posixpath.join(posixpath.dirname(from_file), parts.path))
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    if path in SLUG_BY_FILE:
        slug = SLUG_BY_FILE[path]
        return (f"/help/{slug}" if slug != "overview" else "/help") + fragment
    if path.startswith("docs/images/") and "/" not in path[len("docs/images/") :]:
        return f"/help/images/{path[len('docs/images/') :]}"
    return None


def _markdown(from_file: str) -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])

    def anchors_and_links(state):
        seen: dict[str, int] = {}
        tokens = state.tokens
        for index, token in enumerate(tokens):
            if token.type == "heading_open":
                token.attrSet("id", heading_anchor(tokens[index + 1].content, seen))
            elif token.type in ("th_open", "td_open") and token.attrGet("style"):
                # Table alignment as a class: no inline style under the CSP.
                match = _ALIGN.search(token.attrGet("style") or "")
                del token.attrs["style"]
                if match:
                    token.attrSet("class", f"align-{match.group(1)}")
            if token.type != "inline":
                continue
            for child in token.children or []:
                if child.type == "link_open":
                    href = help_href(child.attrGet("href") or "", from_file)
                    if href is None:
                        child.tag = "span"
                        del child.attrs["href"]
                    else:
                        child.attrSet("href", href)
                elif child.type == "image":
                    src = help_href(child.attrGet("src") or "", from_file)
                    child.attrSet("src", src or "")
            # A link that became text closes as text too.
            opened = []
            for child in token.children or []:
                if child.type == "link_open":
                    opened.append(child.tag)
                elif child.type == "link_close" and opened:
                    child.tag = opened.pop()

    md.core.ruler.push("help_anchors_and_links", anchors_and_links)
    return md


def _title(text: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).replace("`", "") if match else fallback


def page(slug: str) -> dict | None:
    """``{slug, title, html}`` of a guide, or None for an unknown one."""
    file = FILE_BY_SLUG.get(slug)
    if file is None:
        return None
    path = ROOT / file
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _lock:
        cached = _cache.get(file)
        if cached and cached[0] == mtime:
            return cached[1]
    text = path.read_text(encoding="utf-8")
    result = {
        "slug": slug,
        "title": "Overview" if slug == "overview" else _title(text, slug),
        "html": _markdown(file).render(text),
    }
    with _lock:
        _cache[file] = (mtime, result)
    return result


def contents() -> list[dict]:
    """The list of guides: ``{slug, title, href}``, those present on disk."""
    items = []
    for slug, _file in PAGES:
        found = page(slug)
        if found:
            items.append(
                {"slug": slug, "title": found["title"], "href": "/help" if slug == "overview" else f"/help/{slug}"}
            )
    return items
