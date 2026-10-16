"""gzip for large text responses (#349).

At 1,200 sites the board's ``/api/alerts`` is about 1.3 MB per load and the
map's ``/api/locations`` 0.8 MB, every open board and wall reloads on a
timer, and a proxy in front often doesn't compress proxied JSON (nginx:
``gzip_proxied`` is off).  gzip makes them about a tenth.

Compressed: text (HTML, JSON and ``+json``, XML and ``+xml``, CSS, JS, CSV,
SVG) over ``MIN_BYTES``, a 200 answer to GET, when the browser accepts gzip.
Left alone: streamed answers (the exports), files over ``MAX_FILE_BYTES``,
HEAD, partial and not-modified answers, already encoded ones, and those a
view marks ``Cache-Control: no-transform``: an answer with a secret next to
text the client chose must, so its size can't hint at the secret (BREACH;
new API tokens, which are also 201).  ``COMPRESS_RESPONSES=false`` turns it
off, e.g. where the proxy compresses.

It must run after every other after_request hook (web.py registers it
first: Flask runs them in reverse), so they all see the plain answer.
"""

import gzip

from flask import request

from nautobot_maps import settings

MIN_BYTES = 1024
# A file larger than this (none of the static assets) is sent as it is, not read whole.
MAX_FILE_BYTES = 5 * 1024 * 1024
LEVEL = 6
COMPRESSIBLE = ("text/", "application/json", "application/javascript", "application/xml", "image/svg+xml")
COMPRESSIBLE_SUFFIXES = ("+json", "+xml")


def _compressible(mimetype: str) -> bool:
    return mimetype.startswith(COMPRESSIBLE) or mimetype.endswith(COMPRESSIBLE_SUFFIXES)


def compress(response):
    """``after_request``: *response* gzipped when worth it and allowed."""
    if not settings.COMPRESS_RESPONSES or not _compressible(response.mimetype or ""):
        return response
    # Whether or not this one is compressed, the answer depends on the header.
    response.vary.add("Accept-Encoding")
    if (
        response.status_code != 200
        or request.method != "GET"
        or "Content-Encoding" in response.headers
        or "no-transform" in (response.headers.get("Cache-Control") or "")
        or request.accept_encodings["gzip"] <= 0
    ):
        return response
    # A file (static assets) is read here; a generator (the exports) streams as it is.
    if response.direct_passthrough:
        if (response.content_length or 0) > MAX_FILE_BYTES:
            return response
        response.direct_passthrough = False
    elif response.is_streamed:
        return response
    data = response.get_data()
    if len(data) < MIN_BYTES:
        return response
    response.set_data(gzip.compress(data, compresslevel=LEVEL, mtime=0))
    response.headers["Content-Encoding"] = "gzip"
    # Byte ranges of the file are not ranges of this: a range must not be asked for.
    response.headers.pop("Accept-Ranges", None)
    # The bytes differ from the file's: its ETag still says "the same content".
    etag, weak = response.get_etag()
    if etag and not weak:
        response.set_etag(etag, weak=True)
    return response
