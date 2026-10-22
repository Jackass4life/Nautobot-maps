"""A test client for the whole app as production serves it (#436): FastAPI's
TestClient on ``asgi.app``, the Flask app mounted behind it, with the calls and
answers of Flask's test client, so the tests written for Flask run unchanged
while the routes move to FastAPI area by area.

Like Flask's: the client is 127.0.0.1 (a trusted proxy by default), the host
``localhost``, redirects are not followed, and ``environ_base={"REMOTE_ADDR":
...}`` sends from another address.
"""

import json as jsonlib
from email.message import Message

from fastapi.testclient import TestClient

ADDRESS = ("127.0.0.1", 50000)
BASE_URL = "http://localhost"


class Response:
    """An httpx response with Flask's test response names."""

    def __init__(self, response):
        self._response = response
        self.status_code = response.status_code
        self.headers = response.headers

    @property
    def data(self) -> bytes:
        return self._response.content

    @property
    def text(self) -> str:
        return self._response.text

    @property
    def status(self) -> str:
        return f"{self.status_code} {self._response.reason_phrase}"

    def get_data(self, as_text: bool = False):
        return self._response.text if as_text else self._response.content

    def get_json(self, silent: bool = False):
        try:
            return jsonlib.loads(self._response.content)
        except ValueError:
            if silent:
                return None
            raise

    @property
    def json(self):
        return self.get_json(silent=True)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    @property
    def mimetype(self) -> str:
        message = Message()
        message["content-type"] = self.content_type
        return message.get_content_type() if self.content_type else ""

    @property
    def location(self):
        return self.headers.get("location")

    @property
    def is_streamed(self) -> bool:
        """Flask's flag for a body sent as it is made: no Content-Length."""
        return "content-length" not in self.headers


class FlaskStyleClient:
    def __init__(self, app=None):
        if app is None:
            import asgi

            app = asgi.app
        self._app = app
        self._clients: dict[tuple, TestClient] = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    def _client(self, address) -> TestClient:
        if address not in self._clients:
            client = TestClient(
                self._app, base_url=BASE_URL, client=address, follow_redirects=False, raise_server_exceptions=False
            )
            # Flask's test client sends no Accept: the app answers an API path
            # with JSON and a page with HTML, as it does for a browser.
            client.headers.pop("accept", None)
            self._clients[address] = client
        return self._clients[address]

    def open(
        self,
        method,
        path,
        *,
        json=None,
        data=None,
        headers=None,
        content_type=None,
        query_string=None,
        environ_base=None,
        follow_redirects=False,
        input_stream=None,
        environ_overrides=None,
    ):
        """*input_stream*: the body sent chunked, without a Content-Length (what
        *environ_overrides* asked of Flask's client)."""
        headers = dict(headers or {})
        if content_type:
            headers["Content-Type"] = content_type
        address = ADDRESS
        if environ_base and environ_base.get("REMOTE_ADDR") is not None:
            address = (environ_base["REMOTE_ADDR"], ADDRESS[1])
        kwargs = {"headers": headers, "params": query_string, "follow_redirects": follow_redirects}
        if json is not None:
            kwargs["json"] = json
        elif isinstance(data, dict):
            kwargs["data"] = data
        elif data is not None:
            kwargs["content"] = data.encode() if isinstance(data, str) else data
        elif input_stream is not None:
            # A generator body: httpx sends it chunked, with no Content-Length.
            kwargs["content"] = iter([input_stream.read()])
        return Response(self._client(address).request(method, path, **kwargs))

    def get(self, path, **kwargs):
        return self.open("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.open("POST", path, **kwargs)

    def put(self, path, **kwargs):
        return self.open("PUT", path, **kwargs)

    def patch(self, path, **kwargs):
        return self.open("PATCH", path, **kwargs)

    def delete(self, path, **kwargs):
        return self.open("DELETE", path, **kwargs)

    def options(self, path, **kwargs):
        return self.open("OPTIONS", path, **kwargs)

    def head(self, path, **kwargs):
        return self.open("HEAD", path, **kwargs)
