"""The tests' client (#778): FastAPI's TestClient on the whole app, as production
serves it (#436), with the defaults the tests are written for."""

from fastapi.testclient import TestClient

ADDRESS = ("127.0.0.1", 50000)
BASE_URL = "http://localhost"


def make(app=None, address=ADDRESS, **kwargs) -> TestClient:
    """From 127.0.0.1 (a trusted proxy by default) or *address*, host ``localhost``;
    redirects not followed, no Accept (an API path answers JSON, a page HTML), no gzip
    unless a test asks for it. *kwargs*: TestClient's (``raise_server_exceptions=False``
    to get a crash's 500 answer instead of its exception)."""
    if app is None:
        from app.api import app
    options = {"base_url": BASE_URL, "client": address, "follow_redirects": False}
    client = TestClient(app, **{**options, **kwargs})
    client.headers.pop("accept", None)
    client.headers.pop("accept-encoding", None)
    return client
