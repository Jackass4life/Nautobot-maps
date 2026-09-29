"""HTTP sessions for the Nautobot and LibreNMS APIs, with retries (#192).

One ``requests.Session`` per thread (sessions aren't documented as
thread-safe; requests and the background scheduler run in different
threads), so connections are reused between pages of a sync.  Transient
failures are retried: a single 502 while Nautobot restarts no longer aborts
a whole inventory sync.
"""

import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RETRY_STATUSES = (429, 502, 503, 504)
MAX_RETRY_AFTER_SECONDS = 30

_local = threading.local()


class CappedRetry(Retry):
    """Honours ``Retry-After``, but never waits longer than MAX_RETRY_AFTER_SECONDS."""

    def get_retry_after(self, response):
        retry_after = super().get_retry_after(response)
        return None if retry_after is None else min(retry_after, MAX_RETRY_AFTER_SECONDS)


def retry_policy() -> Retry:
    return CappedRetry(
        total=3,
        connect=3,
        # Each read may already have waited the full read timeout (30 s).
        read=1,
        status=3,
        backoff_factor=1,  # 1 s, 2 s, 4 s
        status_forcelist=RETRY_STATUSES,
        # POST/DELETE are not retried: they might have been applied.
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        # After the last retry, return the response so raise_for_status()
        # raises the usual HTTPError.
        raise_on_status=False,
    )


def session() -> requests.Session:
    """This thread's session, created on first use."""
    current = getattr(_local, "session", None)
    if current is None:
        current = requests.Session()
        adapter = HTTPAdapter(max_retries=retry_policy())
        current.mount("http://", adapter)
        current.mount("https://", adapter)
        _local.session = current
    return current
