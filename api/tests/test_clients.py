"""The Nautobot and LibreNMS clients and the response cache (#596), ported from the
current app's tests/test_app.py (TestFetchAllPages, TestSyncBypassesResponseCache,
TestCaching, TestApiVersionHeader, TestSSLVerification, TestUpstreamRetries)."""

import http.server
import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

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

        def fake_requests_get(url, **kwargs):
            calls.append(dict(kwargs.get("params") or {}))
            return ok_response([{"id": "d1"}])

        params = {"last_updated__gte": "2026-09-28T12:00:00Z", "depth": 1}
        with patch.object(requests.Session, "get", side_effect=fake_requests_get):
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
        with patch.object(requests.Session, "get", return_value=ok_response()) as mock_get:
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
        with patch.object(requests.Session, "get", return_value=ok_response()) as mock_get:
            nautobot.get("/dcim/locations/", {"limit": 1})
        (url,), kwargs = mock_get.call_args
        assert url == "https://nautobot.example.com/api/dcim/locations/"
        assert kwargs["headers"]["Authorization"] == "Token token"
        assert kwargs["headers"]["Accept"] == "application/json"
        assert kwargs["timeout"] == (5, 30) and kwargs["verify"] is True

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("false", False), ("true", True), ("/etc/ssl/certs/ca.pem", "/etc/ssl/certs/ca.pem")],
    )
    def test_verify_ssl(self, nautobot_set, configure, value, expected):
        configure(NAUTOBOT_VERIFY_SSL=value)
        with patch.object(requests.Session, "get", return_value=ok_response()) as mock_get:
            nautobot.get("dcim/locations/", {"limit": 1})
        assert mock_get.call_args.kwargs["verify"] == expected

    def test_accept_header_includes_version_when_set(self, nautobot_set, configure):
        configure(NAUTOBOT_API_VERSION="3.0")
        with patch.object(requests.Session, "get", return_value=ok_response()) as mock_get:
            nautobot.get("dcim/locations/")
        assert mock_get.call_args.kwargs["headers"]["Accept"] == "application/json; version=3.0"

    def test_insecure_request_warning(self, configure):
        configure(NAUTOBOT_VERIFY_SSL="false")
        with patch.object(nautobot.urllib3, "disable_warnings") as mock_disable:
            nautobot.configure_ssl_warnings()
        mock_disable.assert_called_once_with(nautobot.InsecureRequestWarning)
        configure(NAUTOBOT_VERIFY_SSL="true", LIBRENMS_VERIFY_SSL="false")
        with patch.object(nautobot.urllib3, "disable_warnings") as mock_disable:
            nautobot.configure_ssl_warnings()
        mock_disable.assert_not_called()

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
        with patch.object(requests.Session, "get") as mock_get:
            assert librenms.fetch_inventory() == [] and librenms.get("devices") == {}
        mock_get.assert_not_called()

    def test_request_shape_and_device(self, configure):
        configure(LIBRENMS_URL="https://librenms.example.com/", LIBRENMS_API_TOKEN="tok")
        response = MagicMock()
        response.json.return_value = {"devices": [{"device_id": 7, "hostname": "sw1"}]}
        with patch.object(requests.Session, "get", return_value=response) as mock_get:
            assert librenms.fetch_device("sw 1/a") == {"device_id": 7, "hostname": "sw1"}
        (url,), kwargs = mock_get.call_args
        assert url == "https://librenms.example.com/api/v0/devices/sw%201%2Fa"
        assert kwargs["headers"] == {"X-Auth-Token": "tok"} and kwargs["timeout"] == 15

    def test_an_unknown_device_is_none(self, configure):
        configure(LIBRENMS_URL="https://librenms.example.com", LIBRENMS_API_TOKEN="tok")
        missing = requests.Response()
        missing.status_code = 404
        response = MagicMock()
        response.raise_for_status.side_effect = requests.HTTPError(response=missing)
        with patch.object(requests.Session, "get", return_value=response):
            assert librenms.fetch_device("nope") is None


class TestUpstreamRetries:
    @pytest.fixture
    def flaky_server(self):
        """A local HTTP server answering from a script of (status, headers) per request."""
        script, requests_seen = [], []

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                requests_seen.append((self.command, self.path))
                status, headers = script.pop(0) if script else (200, {})
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
    def _fast_backoff(self, monkeypatch):
        monkeypatch.setattr(upstream_http, "_local", threading.local())  # a fresh session
        production = _retry_kwargs(upstream_http.retry_policy())
        monkeypatch.setattr(
            upstream_http,
            "retry_policy",
            lambda: upstream_http.CappedRetry(**{**production, "backoff_factor": 0}),
        )

    def test_transient_503_is_retried(self, flaky_server, configure):
        url, script, seen = flaky_server
        configure(NAUTOBOT_URL=url, NAUTOBOT_TOKEN="token")
        script.append((503, {}))
        assert nautobot.fetch_all_pages("dcim/devices/", use_cache=False) == [{"id": "d1"}]
        assert len(seen) == 2

    def test_gives_up_after_three_retries(self, flaky_server, configure):
        url, script, seen = flaky_server
        configure(NAUTOBOT_URL=url, NAUTOBOT_TOKEN="token")
        script.extend([(502, {})] * 10)
        with pytest.raises(requests.HTTPError):
            nautobot.fetch_all_pages("dcim/devices/", use_cache=False)
        assert len(seen) == 4  # the request and three retries

    def test_client_errors_and_writes_are_not_retried(self, flaky_server, configure):
        url, script, seen = flaky_server
        configure(NAUTOBOT_URL=url, NAUTOBOT_TOKEN="token")
        script.append((404, {}))
        with pytest.raises(requests.HTTPError):
            nautobot.get("dcim/devices/x/", use_cache=False)
        # The app never writes to Nautobot (#188), but the session must never retry a
        # write: it might already have been applied.
        script.append((503, {}))
        response = upstream_http.session().post(f"{url}/api/extras/roles/", json={}, timeout=5)
        assert response.status_code == 503
        assert [method for method, _ in seen] == ["GET", "POST"]

    def test_librenms_is_retried_too(self, flaky_server, configure):
        url, script, seen = flaky_server
        configure(LIBRENMS_URL=url, LIBRENMS_API_TOKEN="tok")
        script.append((429, {"Retry-After": "0"}))
        assert librenms.fetch_inventory() == []
        assert len(seen) == 2

    def test_retry_after_is_capped(self):
        response = MagicMock()
        response.headers = {"Retry-After": "3600"}
        response.getheader = lambda name, default=None: response.headers.get(name, default)
        policy = upstream_http.retry_policy()
        assert policy.get_retry_after(response) == upstream_http.MAX_RETRY_AFTER_SECONDS

    def test_one_session_per_thread(self):
        other = []
        thread = threading.Thread(target=lambda: other.append(upstream_http.session()))
        thread.start()
        thread.join()
        assert upstream_http.session() is upstream_http.session()
        assert other[0] is not upstream_http.session()


def _retry_kwargs(policy) -> dict:
    """The production retry settings, so tests only change the backoff."""
    return {
        "total": policy.total,
        "connect": policy.connect,
        "read": policy.read,
        "status": policy.status,
        "status_forcelist": policy.status_forcelist,
        "allowed_methods": policy.allowed_methods,
        "respect_retry_after_header": policy.respect_retry_after_header,
        "raise_on_status": policy.raise_on_status,
    }
