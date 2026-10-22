"""The uvicorn worker gunicorn runs (#436).

Proxy headers off: uvicorn would otherwise take the client address from
X-Forwarded-For when a request comes from 127.0.0.1, and the app decides
from that address whether to trust the sign-in headers (AUTH_TRUSTED_PROXIES,
#187).  The proxy's own address is what counts.  No "server: uvicorn" header.
"""

from uvicorn_worker import UvicornWorker

# What uvicorn is told, in production and in development alike.
UVICORN_OPTIONS = {"proxy_headers": False, "server_header": False, "access_log": False, "log_config": None}


class Worker(UvicornWorker):
    CONFIG_KWARGS = {**UvicornWorker.CONFIG_KWARGS, **UVICORN_OPTIONS}
