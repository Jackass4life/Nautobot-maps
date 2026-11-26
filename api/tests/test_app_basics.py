"""The app's own pages and plumbing (#786, from test_app.py): the index page, error
answers, security headers, /healthz, metrics, the API explorer and the auth settings."""

import asyncio
import importlib
import json
import pathlib
import re
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from app.clients import http as upstream_http
from app.clients import nautobot
from app.core import settings as config
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from app.services import navigation_service as navigation
from app.utils import timeutil
from tests import db as adb
from tests import http_client
from tests.fakes import auth_config


# ---------------------------------------------------------------------------
# Tests: index page
# ---------------------------------------------------------------------------
class TestIndex:
    """The map: built with Nuxt (#463); web/tests/ and e2e/ test the page itself."""

    def test_index_is_the_nuxt_page(self, client, frontend_build):
        resp = client.get("/?tenant=Acme%20Corp&location_id=loc-1")
        assert resp.status_code == 200 and b'id="__nuxt"' in resp.content
        # Asked again each time: a new build shows at once.
        assert resp.headers["Cache-Control"] == "no-cache"
        # No inline script: the CSP needs no 'unsafe-inline' (#197, #198).
        assert not re.search(rb"<script>(?!\s*</script>)", resp.content)

    def test_what_the_map_reads(self, client, override_settings):
        """Nautobot's address, the tile server and the first view (#197, #362), from /api/page-config."""
        override_settings(
            NAUTOBOT_URL="https://nautobot.example.com",
            MAP_TILE_URL="https://tiles.internal/{z}/{x}/{y}.png",
            MAP_TILE_ATTRIBUTION='<a href="https://x">"Ours" & co</a>',
            MAP_DEFAULT_VIEW=None,
        )
        config = client.get("/api/v1/page-config").json()
        assert config["nautobot_url"] == "https://nautobot.example.com"
        assert config["map"] == {
            "tile_url": "https://tiles.internal/{z}/{x}/{y}.png",
            "tile_attribution": '<a href="https://x">"Ours" & co</a>',
            "view": None,
        }
        override_settings(NAUTOBOT_URL="", MAP_DEFAULT_VIEW=(55.7, 12.6, 6))
        config = client.get("/api/v1/page-config").json()
        assert config["nautobot_url"] == ""
        assert config["map"]["view"] == {"latitude": 55.7, "longitude": 12.6, "zoom": 6}

    def test_live_locations_have_their_path(self, monkeypatch):
        """Without the cache (a map-only setup), /api/locations' paths still come along (#367)."""
        import mock_nautobot

        monkeypatch.setattr(
            inventory,
            "read_locations",
            adb.async_of(lambda include_without_coordinates=False, conn=None: []),
        )
        monkeypatch.setattr(inventory, "ensure_snapshot", adb.async_of(lambda **kwargs: None))
        monkeypatch.setattr(inventory, "live_tenant_descriptions", lambda: {})
        monkeypatch.setattr(
            nautobot, "fetch_all_pages", lambda path, **kwargs: list(mock_nautobot.LOCATIONS)
        )
        everywhere = {
            loc["id"]: loc
            for loc in asyncio.run(inventory.get_locations(include_without_coordinates=True))
        }
        assert (
            everywhere["loc-aar-bld-a-f2"]["ancestor_path"]
            == "EMEA › DNK › Aarhus Campus › Bygning A"
        )
        assert {loc["id"]: loc["ancestor_path"] for loc in asyncio.run(inventory.get_locations())}[
            "loc-cph"
        ] == ""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", None),
            ("55.7, 12.6, 6", (55.7, 12.6, 6)),
            ("-33.9,151.2,10", (-33.9, 151.2, 10)),
            ("55.7,12.6", None),
            ("55.7,12.6,6,1", None),
            ("91,0,5", None),
            ("0,181,5", None),
            ("55.7,12.6,0", None),
            ("55.7,12.6,20", None),
            ("55.7,12.6,6.5", None),
            ("Copenhagen", None),
            ("nan,0,5", None),
        ],
    )
    def test_default_view_is_checked(self, raw, expected, caplog):
        assert config._map_view(raw) == expected
        assert ("MAP_DEFAULT_VIEW ignored" in caplog.text) == (bool(raw) and expected is None)


# ---------------------------------------------------------------------------
# Tests: Custom error handlers
# ---------------------------------------------------------------------------
class TestErrorHandlers:
    def test_404_json_for_api_path(self, client):
        resp = client.get("/api/v1/nonexistent")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"] == "Not found"

    def test_404_json_when_accept_json(self, client):
        resp = client.get("/nonexistent-page", headers={"Accept": "application/json"})
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"] == "Not found"

    def test_405_json_for_api_path(self, client):
        resp = client.post("/api/v1/locations")
        assert resp.status_code == 405
        data = resp.json()
        assert data["error"] == "Method not allowed"

    def test_http_exception_returns_json_for_api_path(self):
        from fastapi import APIRouter, FastAPI, HTTPException

        from app.v1 import deps

        router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES)

        @router.get("/api/v1/slow")
        def slow():
            raise HTTPException(
                status_code=504, detail="The connection to an upstream server timed out."
            )

        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        deps.install_error_handlers(app)
        app.include_router(router)
        resp = http_client.make(app).get("/api/v1/slow")
        assert resp.status_code == 504
        assert resp.json()["error"] == "The connection to an upstream server timed out."


class TestAuthConfiguration:
    @staticmethod
    def _reload_gunicorn_config():
        import gunicorn_config

        return importlib.reload(gunicorn_config)

    def test_default_trusts_only_localhost(self):
        assert [str(n) for n in config._networks("t", "127.0.0.1/32,::1/128")] == [
            "127.0.0.1/32",
            "::1/128",
        ]
        with pytest.raises(RuntimeError, match="AUTH_TRUSTED_PROXIES"):
            config._networks("AUTH_TRUSTED_PROXIES", "10.0.0.0/33")

    def test_auth_disabled_refuses_admin_writes_by_default(self, client, override_settings):
        """Anyone who can reach the app must not change Nautobot with its token (#188)."""
        override_settings(ALLOW_UNAUTHENTICATED_WRITES=False)
        writes = [
            (
                "post",
                "/api/v1/criticality-overrides",
                {"nautobot_device_id": "dev-abc", "is_critical": False},
            ),
            ("delete", "/api/v1/criticality-overrides/dev-abc", None),
        ]
        with auth_config(mode="disabled"):
            for method, url, body in writes:
                resp = (
                    getattr(client, method)(url, json=body)
                    if body
                    else getattr(client, method)(url)
                )
                assert resp.status_code == 403, (method, url)
                assert "ALLOW_UNAUTHENTICATED_WRITES" in resp.json()["detail"]

    def test_auth_disabled_keeps_reads_and_cases_open(self, client, override_settings):
        override_settings(ALLOW_UNAUTHENTICATED_WRITES=False)
        with auth_config(mode="disabled"):
            assert client.get("/api/v1/criticality-overrides").status_code != 403
            # Adding a case is what the board does; it reaches the handler
            # (400: nothing to add), not the auth check.
            assert client.post("/api/v1/alert-cases", json={}).status_code != 403

    @pytest.mark.parametrize(
        "method, url",
        # /api/v1/roles was one too: since #721 the app's own roles (never Nautobot's).
        [
            ("get", "/api/v1/location-types"),
            ("post", "/api/v1/location-types"),
            ("delete", "/api/v1/location-types/lt-1"),
        ],
    )
    def test_nautobot_proxy_endpoints_are_gone(self, client, method, url, override_settings):
        """Changes to Nautobot data are made in Nautobot, not through this app (#188)."""
        override_settings(ALLOW_UNAUTHENTICATED_WRITES=True)
        assert getattr(client, method)(url).status_code in (404, 405)

    def test_the_nautobot_client_cannot_write(self):
        assert not hasattr(nautobot, "post") and not hasattr(nautobot, "delete")

    def test_require_viewer_protects_every_page_but_probes(
        self, client, frontend_build, override_settings
    ):
        override_settings(AUTH_REQUIRE_VIEWER=True)
        with auth_config(mode="header", viewer_groups={"noc"}):
            assert client.get("/").status_code == 401
            assert client.get("/api/v1/alerts").status_code == 401
            assert client.get("/alerts", headers={"X-Forwarded-User": "bob"}).status_code == 403
            ok = client.get("/", headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc"})
            assert ok.status_code == 200
            assert client.get("/healthz").status_code in (200, 503)
            assert client.get("/metrics").status_code == 200

    def test_require_viewer_is_off_by_default(self, client, frontend_build, override_settings):
        override_settings(AUTH_REQUIRE_VIEWER=False)
        with auth_config(mode="header", viewer_groups={"noc"}):
            assert client.get("/").status_code == 200

    def test_missing_identity_header_returns_401(self, client):
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            resp = client.post(
                "/api/v1/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
            )
        assert resp.status_code == 401

    def test_default_role_allows_authenticated_viewer_only_access(self, client):
        with auth_config(mode="header", default_role="viewer"):
            resp = client.post(
                "/api/v1/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                headers={"X-Forwarded-User": "alice"},
            )
        assert resp.status_code == 403
        assert resp.json()["current_role"] == "viewer"


# ---------------------------------------------------------------------------
class TestHealthz:
    def test_ok_with_reachable_database(self, client, monkeypatch):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["checks"] == {"app": "ok", "database": "ok"}

    def test_makes_no_upstream_calls(self, client, override_settings):
        """A Nautobot/LibreNMS outage must not make the app look unhealthy."""
        override_settings(NAUTOBOT_MAPS_DATABASE_URL="")
        with patch.object(
            upstream_http, "request", side_effect=AssertionError("no upstream calls")
        ):
            resp = client.get("/healthz")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: browser security headers (#198)
# ---------------------------------------------------------------------------
class TestSecurityHeaders:
    @pytest.mark.parametrize("path", ["/", "/alerts", "/healthz", "/api/v1/does-not-exist"])
    def test_headers_on_every_response(self, client, path, override_settings):
        override_settings(NAUTOBOT_MAPS_DATABASE_URL="")
        resp = client.get(path)
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        csp = resp.headers["Content-Security-Policy"]
        assert "script-src 'self';" in csp and "frame-ancestors 'none'" in csp
        # No inline script or style anywhere (#370): an injection can't restyle the page.
        assert "style-src 'self';" in csp and "unsafe-inline" not in csp

    def test_images_allowed_from_the_tile_server(self, client, override_settings):
        override_settings(MAP_TILE_URL="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png")
        assert (
            "img-src 'self' data: https://*.tile.openstreetmap.org;"
            in client.get("/").headers["Content-Security-Policy"]
        )
        override_settings(MAP_TILE_URL="http://tiles.internal:8080/{z}/{x}/{y}.png")
        assert (
            "img-src 'self' data: http://tiles.internal:8080;"
            in client.get("/").headers["Content-Security-Policy"]
        )
        override_settings(MAP_TILE_URL="/tiles/{z}/{x}/{y}.png")
        assert "img-src 'self' data:;" in client.get("/").headers["Content-Security-Policy"]


# ---------------------------------------------------------------------------
# Tests: Prometheus metrics and sync age (#200)
# ---------------------------------------------------------------------------
class TestMetrics:
    def _record(self, status, started, completed):
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at=started,
            last_completed_at=completed,
            last_successful_sync="2026-09-01T00:00:00+00:00",
            status=status,
        )

    @staticmethod
    def _value(body: str, sample: str) -> float:
        line = next(line for line in body.splitlines() if line.startswith(sample + " "))
        return float(line.rsplit(" ", 1)[1])

    def test_last_success_survives_a_failure(self, client):
        self._record("idle", "2026-09-29T10:00:00+00:00", "2026-09-29T10:00:30+00:00")
        self._record("running", "2026-09-29T10:05:00+00:00", None)
        self._record("error", "2026-09-29T10:05:00+00:00", "2026-09-29T10:05:10+00:00")
        body = client.get("/metrics").text
        src = '{source="nautobot_inventory"}'
        success = datetime(2026, 9, 29, 10, 0, 30, tzinfo=UTC).timestamp()
        assert (
            self._value(body, "nautobot_maps_sync_last_success_timestamp_seconds" + src) == success
        )
        assert self._value(body, "nautobot_maps_sync_failing" + src) == 1
        assert self._value(body, "nautobot_maps_sync_last_duration_seconds" + src) == 10

    def test_alert_and_site_counts(self, client):
        asyncio.run(
            alerts.upsert_alert_lifecycle_for_site(
                {"id": "loc-1", "name": "Site One"},
                [
                    {"id": "d1", "name": "sw1", "status": "offline"},
                    {"id": "d2", "name": "sw2", "status": "offline"},
                ],
                {"level": "critical", "reason": "all down"},
                timeutil.iso_utc_now(),
            )
        )
        asyncio.run(
            alerts.record_site_level_changes(
                {"s1": ("One", "ok"), "s2": ("Two", "ok"), "s3": ("Three", "low")}, "now"
            )
        )
        resp = client.get("/metrics")
        assert resp.headers["Content-Type"].startswith("text/plain; version=0.0.4")
        body = resp.text
        assert self._value(body, 'nautobot_maps_open_alerts{level="critical"}') == 2
        assert self._value(body, 'nautobot_maps_sites{level="ok"}') == 2
        assert self._value(body, "nautobot_maps_database_up") == 1
        assert "# TYPE nautobot_maps_sites gauge" in body

    def test_can_be_turned_off(self, client, override_settings):
        override_settings(METRICS_ENABLED=False)
        assert client.get("/metrics").status_code == 404

    def test_label_values_are_escaped(self):
        from app.services import metrics_service as metrics

        out = metrics.Exposition()
        out.gauge("m", "h", [({"level": 'a"b\\c\nd'}, 1)])
        assert 'm{level="a\\"b\\\\c\\nd"} 1' in out.render()

    def test_healthz_reports_sync_age_without_failing(self, client):
        assert client.get("/healthz").json()["inventory_sync_age_seconds"] is None  # never synced
        self._record("idle", "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:10+00:00")
        resp = client.get("/healthz")
        assert resp.status_code == 200  # an old sync is not a liveness failure
        assert resp.json()["inventory_sync_age_seconds"] > 365 * 24 * 3600

    def test_timestamps_keep_full_precision(self):
        from app.services import metrics_service as metrics

        out = metrics.Exposition()
        out.gauge("t", "h", [({}, 1790676030.0), ({"x": "1"}, 0.25)])
        assert "t 1790676030\n" in out.render() and 't{x="1"} 0.25' in out.render()


# ---------------------------------------------------------------------------
# Tests: API explorer (#230)
# ---------------------------------------------------------------------------
class TestApiExplorer:
    def _routes(self):
        """Every route (#436), in Flask's notation."""
        from fastapi.routing import APIRoute

        from app.api import app as whole_app
        from app.services import apidocs_service as apidocs

        full = {
            op["operationId"]: path
            for path, ops in whole_app.openapi()["paths"].items()
            for op in ops.values()
        }

        def every(routes):
            for route in routes:
                if getattr(route, "original_router", None) is not None:
                    yield from every(route.original_router.routes)
                elif isinstance(route, APIRoute):
                    yield route

        return {
            (
                apidocs._flask_style(
                    full.get(route.operation_id or "", route.path), route.endpoint
                ),
                method,
            )
            for route in every(whole_app.routes)
            if route.name not in apidocs.HIDDEN_ENDPOINTS
            for method in route.methods - {"HEAD", "OPTIONS"}
        }

    def test_every_route_is_listed(self, client):
        listed = client.get("/api/v1/endpoints").json()["endpoints"]
        assert {
            (item["path"], method) for item in listed for method in item["methods"]
        } == self._routes()
        # Swagger UI on /api/docs draws /api/openapi.json (#436, #483): every API
        # route in it, with its method. Pages and files aren't API, nor are the
        # two routes under /api that describe it: the reference and the spec.
        # Listed by hand, so an API route left out of the spec by mistake fails.
        spec = client.get("/api/openapi.json").json()
        documented = {
            (re.sub(r"\{(\w+)(?::\w+)?\}", r"<\1>", path), method.upper())
            for path, operations in spec["paths"].items()
            for method in operations
        }
        api_routes = {
            (re.sub(r"<(?:\w+:)?(\w+)>", r"<\1>", path), method) for path, method in self._routes()
        }
        pages = {navigation.API_DOCS_PATH, navigation.API_SPEC_PATH}
        assert {
            item
            for item in api_routes
            if item[0].startswith(("/api/v1/", "/mcp")) and item[0] not in pages
        } <= documented

    def test_every_write_has_an_example_body(self):
        from app.services import apidocs_service as apidocs

        for route in apidocs.routes():
            if set(route["methods"]) & {"POST", "PUT", "PATCH"}:
                endpoint = route["endpoint"]
                assert endpoint in apidocs.EXAMPLE_BODIES, (
                    f"add an example body for {endpoint} to apidocs"
                )

    def test_what_each_endpoint_takes(self, client):
        listed = client.get("/api/v1/endpoints").json()["endpoints"]
        by_key = {(item["path"], item["methods"][0]): item for item in listed}
        detail = by_key[("/api/v1/locations/<location_id>/detail", "GET")]
        assert detail["path_params"] == ["location_id"] and detail["query_params"] == [
            "location_type"
        ]
        assert detail["group"] == "API" and detail["required_role"] is None
        assert by_key[("/api/v1/alert-feed", "GET")]["query_params"] == [
            "limit",
            "since",
            "kinds",
            "tenant",
            "q",
        ]
        assert by_key[("/api/v1/locations/coordinate-check", "GET")]["query_params"] == [
            "tenant",
            "q",
        ]
        assert by_key[("/api/v1/alert-history", "GET")]["required_role"] == "operator"
        assert by_key[("/metrics", "GET")]["group"] == "Monitoring"
        assert by_key[("/alerts", "GET")]["group"] == "Pages"
        feed = by_key[("/api/v1/alert-feed", "GET")]
        assert feed["summary"] == "What changed on the alert board, newest first."  # no "(#180)"
        assert "Query" in feed["description"]
        case = by_key[("/api/v1/alert-cases", "POST")]
        assert json.loads(case["example_body_text"]) == case["example_body"]
        # The page keeps the example's own order (the JSON response sorts keys).
        assert list(json.loads(case["example_body_text"])) == [
            "site_id",
            "device_ids",
            "case_number",
        ]

    def test_the_page_is_the_nuxt_page(self, client, frontend_build):
        """#436, #463: Swagger UI from npm, bundled into the Nuxt build (no CDN, no inline
        script); web/ and e2e/ test the page itself."""
        resp = client.get("/api/docs")
        assert resp.status_code == 200 and b'id="__nuxt"' in resp.content
        package = json.loads(
            (pathlib.Path(__file__).resolve().parents[2] / "web" / "package.json").read_text()
        )
        assert package["dependencies"]["swagger-ui-dist"]
        # Its install-time analytics (Scarf) stay off: the app works offline.
        assert package["scarfSettings"] == {"enabled": False}

    def test_write_bodies_are_in_the_spec(self, client):
        """Each write's JSON body is described (deps.body_schema), e.g. a case."""
        spec = client.get("/api/openapi.json").json()
        case = spec["paths"]["/api/v1/alert-cases"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        assert {"site_id", "case_number"} <= set(case["required"]) and "device_ids" in case[
            "properties"
        ]
        window = spec["paths"]["/api/v1/maintenance"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        assert "reason" in window["required"]
        # Query models are spelled as sent: "from", not "from_".
        names = {
            param["name"] for param in spec["paths"]["/api/v1/availability"]["get"]["parameters"]
        }
        assert {"month", "from", "to", "basis"} <= names

    def test_the_old_paths_move_for_good(self, client):
        """#483: the reference and the spec live under /api; bookmarks and
        tools reading the old paths are sent on (301, which they remember)."""
        for old, new in (("/docs", "/api/docs"), ("/openapi.json", "/api/openapi.json")):
            resp = client.get(old)
            assert resp.status_code == 301 and resp.headers["Location"] == new
            # With its query: a 301 is remembered.
            assert client.get(f"{old}?v=2&x=a%20b").headers["Location"] == f"{new}?v=2&x=a%20b"
        listed = {item["path"] for item in client.get("/api/v1/endpoints").json()["endpoints"]}
        assert "/docs" not in listed and "/openapi.json" not in listed

    def test_needs_the_viewer_role_like_every_page(self, client, frontend_build, override_settings):
        override_settings(AUTH_REQUIRE_VIEWER=True)
        with auth_config(mode="header", viewer_groups={"noc"}):
            assert client.get("/api/docs").status_code == 401
            assert client.get("/api/v1/endpoints").status_code == 401
            ok = client.get(
                "/api/docs", headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc"}
            )
            assert ok.status_code == 200
