"""The browser security policy every answer carries (#198, #370): the
Content-Security-Policy, with the map tile server's host.  The answers get it
in nautobot_maps/api/deps.py (``finish``), with the other security headers.
"""

from urllib.parse import urlsplit

from nautobot_maps import settings


def tile_source() -> str:
    """The CSP source for MAP_TILE_URL's host, with ``{s}`` subdomains as ``*``."""
    parsed = urlsplit(settings.MAP_TILE_URL)
    if not parsed.netloc:
        return ""  # a relative URL: same origin, covered by 'self'
    return f"{parsed.scheme}://{parsed.netloc.replace('{s}', '*')}"


def content_security_policy() -> str:
    """Only this app's own scripts and styles; images may also come from the tile server (#198).

    No inline styles (#370): an HTML injection can't restyle the page (an
    overlay with a fake link).  Markers and the error page use classes and
    CSS files; style set from script (Leaflet's own, element.style) is
    allowed by the CSP, as it isn't markup.
    """
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            f"img-src 'self' data: {tile_source()}".strip(),
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )
