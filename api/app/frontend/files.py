"""Files from disk (#436, #594): the current app's api/files.py.

- Only files inside the given folder (no ``..``, absolute paths or symlinks out).
- An ETag and Last-Modified; a browser's still-current copy gets a 304.
- Text (compressible, at most ``MAX_FILE_BYTES``) goes gzipped to a browser that
  accepts it, with a weak ETag (the bytes differ from the file's) and no byte
  ranges; anything else as the file, byte ranges allowed.
- ``Cache-Control``: ``no-cache`` (always ask, cheap with the ETag), or
  ``public, max-age=...``.
"""

import gzip
import mimetypes
import os
import pathlib
from email.utils import formatdate

from fastapi import Request
from fastapi.responses import FileResponse, Response

from app.core.exceptions import ApiError
from app.core.settings import get_settings
from app.frontend import compression


def inside(base: pathlib.Path, relative: str) -> pathlib.Path | None:
    """The file *relative* names inside *base*, or None: symlinks and ``..``
    resolved first, then it must still be below *base* and a file."""
    root = os.path.realpath(base)
    candidate = os.path.realpath(os.path.join(root, relative))
    if not candidate.startswith(root + os.sep) or not os.path.isfile(candidate):
        return None
    return pathlib.Path(candidate)


def _not_modified(request: Request, etag: str) -> bool:
    """If-None-Match names this ETag (weak or strong: a GET compares weakly)."""
    wanted = request.headers.get("if-none-match") or ""
    tags = {tag.strip().removeprefix("W/") for tag in wanted.split(",")}
    return "*" in tags or etag.removeprefix("W/") in tags


def send(
    request: Request, base: pathlib.Path, relative: str, max_age: int | None = None
) -> Response:
    path = inside(base, relative)
    if path is None:
        raise ApiError({"error": "Not found"}, 404)
    stat = path.stat()
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = {
        "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
        "Cache-Control": f"public, max-age={max_age}" if max_age is not None else "no-cache",
    }
    zipped = (
        get_settings().compress_responses
        and compression.compressible(media_type)
        and stat.st_size <= compression.MAX_FILE_BYTES
        and request.method == "GET"
        and compression.accepts_gzip(request.headers.get("accept-encoding", ""))
    )
    if zipped:
        etag = "W/" + etag
    if _not_modified(request, etag):
        return Response(
            status_code=304, headers={**headers, "ETag": etag, "Vary": "Accept-Encoding"}
        )
    if zipped:
        body = gzip.compress(path.read_bytes(), compresslevel=compression.LEVEL, mtime=0)
        return Response(
            body,
            media_type=media_type,
            headers={
                **headers,
                "ETag": etag,
                "Content-Encoding": "gzip",
                "Vary": "Accept-Encoding",
            },
        )
    # As the file: byte ranges and the strong ETag; Vary when it might have been gzipped.
    if compression.compressible(media_type):
        headers["Vary"] = "Accept-Encoding"
    return FileResponse(
        path, media_type=media_type, headers={**headers, "ETag": etag}, stat_result=stat
    )
