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
    for part in accept_encoding.split(","):
        name, _, params = part.strip().partition(";")
        if name.strip().lower() in ("gzip", "*"):
            quality = params.strip().removeprefix("q=").strip()
            try:
                return float(quality) > 0 if quality else True
            except ValueError:
                return True
    return False
