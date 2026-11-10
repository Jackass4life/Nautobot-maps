"""The browser security policy every answer carries (#198, #370, #594): the
current app's nautobot_maps/web.py and the headers of its deps.finish."""

from urllib.parse import urlsplit

from app.core.settings import Settings


def tile_source(settings: Settings) -> str:
    """The CSP source for MAP_TILE_URL's host, with ``{s}`` subdomains as ``*``."""
    parsed = urlsplit(settings.map_tile_url)
    if not parsed.netloc:
        return ""  # a relative URL: same origin, covered by 'self'
    return f"{parsed.scheme}://{parsed.netloc.replace('{s}', '*')}"


def content_security_policy(settings: Settings) -> str:
    """Only this app's own scripts and styles; images may also come from the tile server.

    No inline scripts or styles (#370): an HTML injection can't run code or restyle
    the page. Style set from script (element.style) is allowed, as it isn't markup.
    """
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            f"img-src 'self' data: {tile_source(settings)}".strip(),
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def security_headers(settings: Settings) -> list[tuple[str, str]]:
    return [
        ("content-security-policy", content_security_policy(settings)),
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("referrer-policy", "strict-origin-when-cross-origin"),
    ]
