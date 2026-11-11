"""The Nautobot and LibreNMS clients and the response cache (#596), ported from the
current app's tests/test_app.py (TestFetchAllPages, TestSyncBypassesResponseCache,
TestCaching, TestApiVersionHeader, TestSSLVerification, TestUpstreamRetries)."""

import http.server
import json
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.clients import caching, librenms, nautobot
from app.clients import http as upstream_http


@pytest.fixture(autouse=True)
def empty_cache():
    caching.cache.clear()
    yield
    caching.cache.clear()


@pytest.fixture
def nautobot_set(configure):
    configure(NAUTOBOT_URL="https://nautobot.example.com", NAUTOBOT_TOKEN="token")


def status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://upstream.example.com/x")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


def ok_response(results=None):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"count": 0, "next": None, "results": results or []}
    return response


class TestFetchAllPages:
    def test_sets_large_limit_and_depth_defaults(self):
        calls = []

        def fake_get(endpoint, params=None, **kwargs):
            calls.append((endpoint, dict(params or {})))
            return {"count": 1, "next": None, "results": [{"id": "dev-1"}]}

        with patch.object(nautobot, "get", side_effect=fake_get):
            results = nautobot.fetch_all_pages("dcim/devices/")
        assert results == [{"id": "dev-1"}]
        assert calls == [("dcim/devices/", {"limit": 1000, "depth": 0, "offset": 0})]

    def test_respects_explicit_limit_and_depth(self):
        calls = []

        def fake_get(endpoint, params=None, **kwargs):
            calls.append((endpoint, dict(params or {})))
            return {"count": 0, "next": None, "results": []}

        with patch.object(nautobot, "get", side_effect=fake_get):
            nautobot.fetch_all_pages("dcim/devices/", {"limit": 25, "depth": 2})
        assert calls == [("dcim/devices/", {"limit": 25, "depth": 2, "offset": 0})]

    def test_follows_next_pages(self):
        pages = iter(
            [
                {"next": "page2", "results": [{"id": "a"}]},
                {"next": None, "results": [{"id": "b"}]},
            ]
        )
        offsets = []

        def fake_get(endpoint, params=None, **kwargs):
            offsets.append(params["offset"])
            return next(pages)

        with patch.object(nautobot, "get", side_effect=fake_get):
            assert nautobot.fetch_all_pages("dcim/devices/") == [{"id": "a"}, {"id": "b"}]
        assert offsets == [0, 1000]


class TestResponseCache:
    """The inventory sync reads Nautobot directly, never cached pages (#185)."""

    def test_uncached_fetches_always_reach_nautobot(self, nautobot_set):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append(dict(kwargs.get("params") or {}))
            return ok_response([{"id": "d1"}])

        params = {"last_updated__gte": "2026-09-28T12:00:00Z", "depth": 1}
        with patch.object(upstream_http, "request", side_effect=fake_request):
            nautobot.fetch_all_pages("dcim/devices/", params, use_cache=False)
            nautobot.fetch_all_pages("dcim/devices/", params, use_cache=False)
            assert len(calls) == 2  # same parameters, still two requests
            # Nor do they fill the cache for a cached reader.
            nautobot.fetch_all_pages("dcim/devices/", params)
            assert len(calls) == 3
            # Cached reads still use the cache.
            nautobot.fetch_all_pages("dcim/devices/", params)
            assert len(calls) == 3

    def test_cache_reduces_api_calls(self, nautobot_set):
        with patch.object(upstream_http, "request", return_value=ok_response()) as mock_get:
            nautobot.get("dcim/locations/", {"limit": 1})
            nautobot.get("dcim/locations/", {"limit": 1})
        assert mock_get.call_count == 1

    def test_cache_set_and_get(self):
        caching.set("test-key", {"data": 42})
        assert caching.get("test-key") == {"data": 42}

    def test_cache_expires(self):
        caching.cache.set("expiring-key", "value", timeout=1)
        time.sleep(1.1)
        assert caching.get("expiring-key") is None

    def test_backends(self, configure):
        assert caching.backend_for("SimpleCache", "", 42).default_timeout == 42
        assert type(caching.backend_for("NullCache", "", 1)).__name__ == "NullCache"
        redis_cache = caching.backend_for("RedisCache", "redis://127.0.0.1:1/0", 7)
        assert type(redis_cache).__name__ == "RedisCache"
        assert redis_cache.key_prefix == "flask_cache_", "shared with the current app"
        with pytest.raises(ValueError, match="CACHE_REDIS_URL"):
            caching.backend_for("RedisCache", "", 1)
        with pytest.raises(ValueError, match="SimpleCache, RedisCache or NullCache"):
            caching.backend_for("MemcachedCache", "", 1)
        configure(CACHE_TTL="42")
        caching.init()
        try:
            assert caching.cache.backend.default_timeout == 42
        finally:
            configure(CACHE_TTL="300")
            caching.init()

    def test_invalidating_the_board_drops_its_keys(self):
        for key in ("alert-board-data:v3", "location-alert-levels:v1", "other"):
            caching.set(key, 1)
        caching.invalidate_alert_board()
        assert caching.get("alert-board-data:v3") is None
        assert caching.get("location-alert-levels:v1") is None
        assert caching.get("other") == 1


class TestNautobotRequests:
    def test_needs_url_and_token(self, configure):
        configure(NAUTOBOT_URL="", NAUTOBOT_TOKEN="")
        with pytest.raises(RuntimeError, match="NAUTOBOT_URL and NAUTOBOT_TOKEN must be set"):
            nautobot.get("dcim/locations/")

    def test_request_shape(self, nautobot_set):
        with patch.object(upstream_http, "request", return_value=ok_response()) as mock_get:
            nautobot.get("/dcim/locations/", {"limit": 1})
        (method, url), kwargs = mock_get.call_args
        assert (method, url) == ("GET", "https://nautobot.example.com/api/dcim/locations/")
        assert kwargs["headers"]["Authorization"] == "Token token"
        assert kwargs["headers"]["Accept"] == "application/json"
        assert kwargs["timeout"] == (5, 30) and kwargs["verify"] is True

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("false", False), ("true", True), ("/etc/ssl/certs/ca.pem", "/etc/ssl/certs/ca.pem")],
    )
    def test_verify_ssl(self, nautobot_set, configure, value, expected):
        configure(NAUTOBOT_VERIFY_SSL=value)
        with patch.object(upstream_http, "request", return_value=ok_response()) as mock_get:
            nautobot.get("dcim/locations/", {"limit": 1})
        assert mock_get.call_args.kwargs["verify"] == expected

    def test_accept_header_includes_version_when_set(self, nautobot_set, configure):
        configure(NAUTOBOT_API_VERSION="3.0")
        with patch.object(upstream_http, "request", return_value=ok_response()) as mock_get:
            nautobot.get("dcim/locations/")
        assert mock_get.call_args.kwargs["headers"]["Accept"] == "application/json; version=3.0"

    def test_names_of_brief_nested_objects(self):
        def fake_pages(endpoint, params=None, **kwargs):
            return {
                "dcim/manufacturers/": [{"id": "m1", "name": "Cisco"}],
                "dcim/device-types/": [
                    {"id": "t1", "model": "C9300", "manufacturer": {"id": "m1"}},
                    {"id": "t2", "display": "AP 9130", "manufacturer": {"name": "Cisco"}},
                ],
            }[endpoint]

        with patch.object(nautobot, "fetch_all_pages", side_effect=fake_pages):
            assert nautobot.device_type_maps() == (
                {"t1": "Cisco", "t2": "Cisco"},
                {"t1": "C9300", "t2": "AP 9130"},
            )
        with patch.object(nautobot, "fetch_all_pages", side_effect=RuntimeError("down")):
            assert nautobot.id_name_map("extras/roles/") == {}
            assert nautobot.device_type_maps() == ({}, {})

    @pytest.mark.parametrize(
        ("obj", "expected"),
        [
            (None, ""),
            ("Site", "Site"),
            ({"display": "Site A"}, "Site A"),
            ({"name": "", "display": "Shown"}, "Shown"),
            (3, "3"),
        ],
    )
    def test_nested_str(self, obj, expected):
        assert nautobot.nested_str(obj, "name", "display") == expected


class TestLibreNMS:
    def test_not_configured_reads_nothing(self, configure):
        configure(LIBRENMS_URL="", LIBRENMS_API_TOKEN="")
        with patch.object(upstream_http, "request") as mock_get:
            assert librenms.fetch_inventory() == [] and librenms.get("devices") == {}
        mock_get.assert_not_called()

    def test_request_shape_and_device(self, configure):
        configure(LIBRENMS_URL="https://librenms.example.com/", LIBRENMS_API_TOKEN="tok")
        response = MagicMock()
        response.json.return_value = {"devices": [{"device_id": 7, "hostname": "sw1"}]}
        with patch.object(upstream_http, "request", return_value=response) as mock_get:
            assert librenms.fetch_device("sw 1/a") == {"device_id": 7, "hostname": "sw1"}
        (method, url), kwargs = mock_get.call_args
        assert (method, url) == ("GET", "https://librenms.example.com/api/v0/devices/sw%201%2Fa")
        assert kwargs["headers"] == {"X-Auth-Token": "tok"} and kwargs["timeout"] == 15

    def test_an_unknown_device_is_none(self, configure):
        configure(LIBRENMS_URL="https://librenms.example.com", LIBRENMS_API_TOKEN="tok")
        with patch.object(upstream_http, "request", side_effect=status_error(404)):
            assert librenms.fetch_device("nope") is None


class TestUpstreamRetries:
    """app/clients/http.py's retries on httpx (#645): requests' Retry policy as it was."""

    @pytest.fixture
    def flaky_server(self):
        """A local HTTP server answering from a script of (status, headers) per request;
        ``"slow"`` answers after 0.5 s."""
        script, requests_seen = [], []

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                requests_seen.append((self.command, self.path))
                status, headers = script.pop(0) if script else (200, {})
                if status == "slow":
                    time.sleep(0.5)
                    status = 200
                body = json.dumps({"results": [{"id": "d1"}], "next": None, "devices": []})
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body.encode())))
                self.end_headers()
                self.wfile.write(body.encode())

            do_GET = do_POST = do_DELETE = _answer

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_address[1]}", script, requests_seen
        server.shutdown()

    @pytest.fixture(autouse=True)
    def waits(self, monkeypatch):
        """The waits between attempts, recorded instead of slept."""
        waited = []
        monkeypatch.setattr(upstream_http, "sleep", waited.append)
        upstream_http.close()  # fresh clients
        yield waited
        upstream_http.close()

    def test_transient_503_is_retried(self, flaky_server, configure, waits):
        url, script, seen = flaky_server
        configure(NAUTOBOT_URL=url, NAUTOBOT_TOKEN="token")
        script.append((503, {}))
        assert nautobot.fetch_all_pages("dcim/devices/", use_cache=False) == [{"id": "d1"}]
        assert len(seen) == 2 and waits == [1]

    def test_gives_up_after_three_retries(self, flaky_server, configure, waits):
        url, script, seen = flaky_server
        configure(NAUTOBOT_URL=url, NAUTOBOT_TOKEN="token")
        script.extend([(502, {})] * 10)
        with pytest.raises(httpx.HTTPStatusError) as raised:
            nautobot.fetch_all_pages("dcim/devices/", use_cache=False)
        assert raised.value.response.status_code == 502
        assert len(seen) == 4  # the request and three retries
        assert waits == [1, 2, 4]

    def test_client_errors_and_writes_are_not_retried(self, flaky_server, configure):
        url, script, seen = flaky_server
        configure(NAUTOBOT_URL=url, NAUTOBOT_TOKEN="token")
        script.append((404, {}))
        with pytest.raises(httpx.HTTPStatusError):
            nautobot.get("dcim/devices/x/", use_cache=False)
        # The app never writes to Nautobot (#188), but a write must never be retried:
        # it might already have been applied.
        script.append((503, {}))
        with pytest.raises(httpx.HTTPStatusError):
            upstream_http.request("POST", f"{url}/api/extras/roles/", json={}, timeout=5)
        assert [method for method, _ in seen] == ["GET", "POST"]

    def test_librenms_is_retried_too(self, flaky_server, configure, waits):
        url, script, seen = flaky_server
        configure(LIBRENMS_URL=url, LIBRENMS_API_TOKEN="tok")
        script.append((429, {"Retry-After": "0"}))
        assert librenms.fetch_inventory() == []
        assert len(seen) == 2 and waits == [0]

    def test_retry_after_is_honoured_and_capped(self, flaky_server, waits):
        url, script, _ = flaky_server
        script.extend([(429, {"Retry-After": "3600"}), (503, {"Retry-After": "2"})])
        assert upstream_http.request("GET", f"{url}/x").status_code == 200
        assert waits == [upstream_http.MAX_RETRY_AFTER_SECONDS, 2]
        dated = httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
        assert upstream_http.retry_after(dated) == 0, "a date in the past: at once"
        assert upstream_http.retry_after(httpx.Response(429)) is None

    def test_a_failed_connect_is_retried_three_times(self, waits):
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            port = unused.getsockname()[1]
        with pytest.raises(httpx.ConnectError):
            upstream_http.request("POST", f"http://127.0.0.1:{port}/x", timeout=2)
        assert waits == [1, 2, 4], "not sent, so a POST too"

    def test_a_read_timeout_is_retried_once_and_only_for_get(self, flaky_server, waits):
        url, script, seen = flaky_server
        script[:] = [("slow", {}), ("slow", {})]
        with pytest.raises(httpx.ReadTimeout):
            upstream_http.request("GET", f"{url}/x", timeout=0.2)
        assert len(seen) == 2 and waits == [1]
        script[:] = [("slow", {})]
        with pytest.raises(httpx.ReadTimeout):
            upstream_http.request("POST", f"{url}/x", timeout=0.2)
        assert len(seen) == 3 and waits == [1]

    def test_none_parameters_are_left_out_and_redirects_followed(self, flaky_server):
        url, script, seen = flaky_server
        script.append((302, {"Location": "/moved"}))
        upstream_http.request("GET", f"{url}/x", params={"a": "1", "b": None, "c": ["x", "y"]})
        assert seen == [("GET", "/x?a=1&c=x&c=y"), ("GET", "/moved")]

    def test_one_client_per_certificate_setting_shared_by_threads(self, tmp_path):
        other = []
        thread = threading.Thread(target=lambda: other.append(upstream_http.client(True)))
        thread.start()
        thread.join()
        assert other[0] is upstream_http.client(True)
        assert upstream_http.client(False) is not upstream_http.client(True)

    def test_a_ca_bundle_path_is_an_ssl_context(self, monkeypatch):
        loaded = []
        monkeypatch.setattr(
            upstream_http.ssl, "create_default_context", lambda cafile: loaded.append(cafile)
        )
        upstream_http._ssl("/etc/ssl/company-ca.pem")
        assert loaded == ["/etc/ssl/company-ca.pem"]
        assert upstream_http._ssl(False) is False
