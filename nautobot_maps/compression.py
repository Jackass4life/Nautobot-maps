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

Applied by nautobot_maps/api/deps.py (``finish``, every route's answer) and
api/files.py (static files), #436.
"""

MIN_BYTES = 1024
# A file larger than this (none of the static assets) is sent as it is, not read whole.
MAX_FILE_BYTES = 5 * 1024 * 1024
LEVEL = 6
COMPRESSIBLE = ("text/", "application/json", "application/javascript", "application/xml", "image/svg+xml")
COMPRESSIBLE_SUFFIXES = ("+json", "+xml")


def compressible(mimetype: str) -> bool:
    return mimetype.startswith(COMPRESSIBLE) or mimetype.endswith(COMPRESSIBLE_SUFFIXES)
