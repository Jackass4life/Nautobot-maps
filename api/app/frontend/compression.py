"""gzip for large text responses (#349, #594): the current app's compression.py.

Compressed: text (HTML, JSON and ``+json``, XML and ``+xml``, CSS, JS, CSV, SVG)
of at least ``MIN_BYTES``, a 200 answer to GET, when the browser accepts gzip.
Left alone: streamed answers and large files, HEAD, partial and not-modified
answers, already encoded ones, and those marked ``Cache-Control: no-transform``:
an answer with a secret next to text the client chose must, so its size can't
hint at the secret (BREACH; new API tokens). ``COMPRESS_RESPONSES=false`` turns
it off, e.g. where the proxy compresses.
"""

MIN_BYTES = 1024
# A file larger than this is sent as it is, not read whole.
MAX_FILE_BYTES = 5 * 1024 * 1024
LEVEL = 6
COMPRESSIBLE = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "image/svg+xml",
)
COMPRESSIBLE_SUFFIXES = ("+json", "+xml")


def compressible(mimetype: str) -> bool:
    return mimetype.startswith(COMPRESSIBLE) or mimetype.endswith(COMPRESSIBLE_SUFFIXES)


def accepts_gzip(accept_encoding: str) -> bool:
    """Whether the client takes gzip: its own entry decides; ``*`` only when gzip isn't
    named (#601 review: ``*;q=1, gzip;q=0`` refuses it)."""
    qualities: dict[str, bool] = {}
    for part in accept_encoding.split(","):
        name, _, params = part.strip().partition(";")
        name = name.strip().lower()
        if name in ("gzip", "*") and name not in qualities:
            quality = params.strip().removeprefix("q=").strip()
            try:
                qualities[name] = float(quality) > 0 if quality else True
            except ValueError:
                qualities[name] = True
    return qualities.get("gzip", qualities.get("*", False))
