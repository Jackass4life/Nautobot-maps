"""Run one of the app's own routes in-process (#436), whether it is on FastAPI
or still on Flask behind it: what the MCP tools call (#250).

The call carries the caller's headers and address, so the route's own
sign-in and role checks apply as for the caller.  It goes to the FastAPI app
(``asgi.api``) directly: no access-log line, as before.  It runs on a new
event loop in the calling thread, which is a worker thread (Flask's, or
FastAPI's for a sync route), never the server's loop.
"""

import asyncio
import json
from urllib.parse import urlencode


async def _call(app, scope: dict, body: bytes) -> tuple[int, bytes]:
    sent = False
    status = 500
    chunks: list[bytes] = []

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body") or b"")

    await app(scope, receive, send)
    return status, b"".join(chunks)


def call(
    method: str,
    path: str,
    *,
    query: dict | None = None,
    body: dict | None = None,
    headers: list[tuple[str, str]] = (),
    remote_addr: str = "",
    host: str = "localhost",
) -> tuple[int, dict]:
    """``(status, JSON body or {})`` of *method* *path* as the caller."""
    import asgi

    payload = json.dumps(body).encode() if body is not None else b""
    raw_headers = [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers]
    if body is not None:
        raw_headers += [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    if not any(name == b"host" for name, _ in raw_headers):
        raw_headers.append((b"host", host.encode("latin-1")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": urlencode({k: v for k, v in (query or {}).items() if v not in (None, "")}).encode(),
        "headers": raw_headers,
        "client": (remote_addr or "127.0.0.1", 0),
        "server": (host, 80),
    }
    status, data = asyncio.run(_call(asgi.api, scope, payload))
    try:
        parsed = json.loads(data) if data else {}
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}
