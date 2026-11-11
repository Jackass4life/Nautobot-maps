"""HTTP for the Nautobot, LibreNMS and wireless controller APIs, with retries (#192,
#596), on httpx (#645).

One ``httpx.Client`` per certificate setting (httpx fixes ``verify`` per client), shared
by every thread: httpx's client is thread-safe, so connections are reused between
pages of a sync. Transient failures are retried, as before with requests: a single
502 while Nautobot restarts doesn't abort a whole inventory sync.
"""

import email.utils
import ssl
import threading
import time

import httpx

RETRY_STATUSES = (429, 502, 503, 504)
MAX_RETRY_AFTER_SECONDS = 30
# At most this many retries of one request, of any kind.
MAX_RETRIES = 3
CONNECT_RETRIES = 3
# Each read may already have waited the full read timeout (30 s).
READ_RETRIES = 1
STATUS_RETRIES = 3
BACKOFF_SECONDS = 1  # 1 s, 2 s, 4 s
# POST/DELETE are not retried once sent: they might have been applied.
RETRIED_METHODS = frozenset({"GET"})
# Not sent: safe to retry for any method.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_READ_FAILED = (httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError)

_clients: dict[bool | str, httpx.Client] = {}
_clients_lock = threading.Lock()


def _ssl(verify: bool | str) -> bool | ssl.SSLContext:
    """httpx's ``verify``: a CA bundle's path (as requests took it) as an SSL context."""
    if isinstance(verify, str):
        return ssl.create_default_context(cafile=verify)
    return verify


def client(verify: bool | str = True) -> httpx.Client:
    """The shared client for this certificate setting, created on first use."""
    with _clients_lock:
        current = _clients.get(verify)
        if current is None:
            # Redirects followed, as requests did.
            current = httpx.Client(verify=_ssl(verify), follow_redirects=True)
            _clients[verify] = current
        return current


def close() -> None:
    """Close every client (tests; a new one is made on next use)."""
    with _clients_lock:
        for current in _clients.values():
            current.close()
        _clients.clear()


def as_timeout(value: float | tuple[float, float]) -> httpx.Timeout:
    """requests' ``(connect, read)`` or one number for both, as httpx's."""
    if isinstance(value, tuple):
        connect, read = value
        return httpx.Timeout(read, connect=connect)
    return httpx.Timeout(value)


def retry_after(response: httpx.Response) -> float | None:
    """The answer's ``Retry-After`` in seconds (a number or a date), capped at
    MAX_RETRY_AFTER_SECONDS; None without one."""
    value = response.headers.get("retry-after", "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        seconds = when.timestamp() - time.time()
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


def sleep(seconds: float) -> None:
    """Called as ``http.sleep()`` so tests can skip the waits."""
    time.sleep(seconds)


def _backoff(retry: int) -> float:
    return BACKOFF_SECONDS * 2 ** (retry - 1)


def request(
    method: str,
    url: str,
    *,
    verify: bool | str = True,
    timeout: float | tuple[float, float] = (10, 30),
    params: dict | None = None,
    **kwargs,
) -> httpx.Response:
    """One request that raises ``httpx.HTTPStatusError`` on an error status, retried
    as requests' Retry did: connecting up to 3 times, a failed read once, a 429, 502,
    503 or 504 up to 3 times (honouring Retry-After); a sent POST never."""
    method = method.upper()
    if params is not None:
        # requests left out None values; httpx would send them empty.
        params = {key: value for key, value in params.items() if value is not None}
    retries = {"connect": 0, "read": 0, "status": 0}
    total = 0
    while True:
        try:
            response = client(verify).request(
                method, url, params=params, timeout=as_timeout(timeout), **kwargs
            )
        except _NOT_SENT:
            if total >= MAX_RETRIES or retries["connect"] >= CONNECT_RETRIES:
                raise
            retries["connect"] += 1
        except _READ_FAILED:
            if (
                method not in RETRIED_METHODS
                or total >= MAX_RETRIES
                or retries["read"] >= READ_RETRIES
            ):
                raise
            retries["read"] += 1
        else:
            if (
                response.status_code not in RETRY_STATUSES
                or method not in RETRIED_METHODS
                or total >= MAX_RETRIES
                or retries["status"] >= STATUS_RETRIES
            ):
                # After the last retry, the usual error for its status.
                response.raise_for_status()
                return response
            retries["status"] += 1
            total += 1
            waited = retry_after(response)
            response.close()
            sleep(_backoff(total) if waited is None else waited)
            continue
        total += 1
        sleep(_backoff(total))
