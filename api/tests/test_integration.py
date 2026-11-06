"""The mock Nautobot over real HTTP, for the tests ported from the current app (#602).
Its integration tests through the routes move here with them (step 7 of #587)."""

import socket
import threading
import time

import mock_nautobot  # demo/, on the path from tests/conftest.py
import pytest
import uvicorn


@pytest.fixture(scope="module")
def mock_nautobot_server():
    """The mock Nautobot HTTP server on a random port; yields its base URL."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mock_nautobot.app, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)
