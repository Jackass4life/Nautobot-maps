"""Prints every request it gets (#768): what nginx (or a browser) really sends.

For debugging the proxy: run it where the API or the web container would be (or in
nginx's place), then look at the headers.  Secrets are masked: Authorization, Cookie,
Set-Cookie and X-Auth-Proxy-Secret show only their length.  Start it with
start_listener.sh.
"""

from fastapi import FastAPI, Request

app = FastAPI()

MASKED = {"authorization", "cookie", "set-cookie", "x-auth-proxy-secret"}


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE"],
)
async def read_root(request: Request, path: str):
    client = request.client.host if request.client else "?"
    print(f"{request.method} /{path}{'?' + request.url.query if request.url.query else ''} from {client}")
    for name, value in request.headers.items():
        shown = f"<{len(value)} characters>" if name.lower() in MASKED else value
        print(f"  {name}: {shown}")
    return {}
