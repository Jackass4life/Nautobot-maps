"""The app served as ASGI (#436): FastAPI outside, the Flask app mounted behind
it.  Everything a browser, a script or a proxy relied on must be the same."""

import logging

from nautobot_maps import server, settings


def test_pages_and_apis_are_served_through_fastapi(asgi_client):
    assert asgi_client.get("/healthz").status_code == 200
    page = asgi_client.get("/alerts")
    assert page.status_code == 200 and 'class="app-bar"' in page.text
    assert asgi_client.get("/api/app-status").json()["state"] in {"ok", "stale", "down"}
    assert asgi_client.get("/static/js/lib/nav.js").status_code == 200
    # Flask's own 404, as JSON for the API.
    missing = asgi_client.get("/api/nope")
    assert missing.status_code == 404 and missing.json() == {"error": "Not found"}


def test_security_headers_and_gzip_once(asgi_client):
    response = asgi_client.get("/alerts", headers={"Accept-Encoding": "gzip"})
    for name in ("content-security-policy", "x-content-type-options", "x-frame-options", "referrer-policy"):
        assert len(response.headers.get_list(name)) == 1, name
    assert response.headers.get_list("content-encoding") == ["gzip"]
    # No "server: uvicorn" announcing the stack.
    assert "uvicorn" not in response.headers.get("server", "")


def test_a_change_from_another_site_is_still_refused(asgi_client, monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)
    response = asgi_client.post("/api/notifications/test", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403 and response.json() == {"error": "Changes from another site are refused"}


def test_one_access_log_line_with_the_same_fields(asgi_client, caplog):
    with caplog.at_level(logging.INFO, logger="nautobot_maps.access"):
        asgi_client.get("/api/app-status?x=1", headers={settings.AUTH_HEADER_USER: "olga", "User-Agent": "probe/1"})
    lines = [record for record in caplog.records if record.name == "nautobot_maps.access"]
    assert len(lines) == 1
    record = lines[0]
    assert record.args["r"] == "GET /api/app-status?x=1 HTTP/1.1" and record.args["s"] == 200
    assert record.args["user"] == "olga" and record.args["a"] == "probe/1" and record.args["b"] > 0
    message = record.getMessage()
    assert message.startswith('testclient "olga" "GET /api/app-status?x=1 HTTP/1.1" 200 ') and message.endswith(
        'ms "probe/1"'
    )


def test_successful_health_checks_are_still_left_out(asgi_client, caplog):
    from nautobot_maps import logs

    with caplog.at_level(logging.INFO, logger="nautobot_maps.access"):
        asgi_client.get("/healthz")
    (record,) = [record for record in caplog.records if record.name == "nautobot_maps.access"]
    assert not logs.SkipSuccessfulHealthChecks().filter(record)


def test_uvicorn_never_takes_the_client_from_a_header():
    """The app trusts the sign-in headers by the client's address (#187): uvicorn
    must not replace it with X-Forwarded-For, nor name itself."""
    assert server.UVICORN_OPTIONS["proxy_headers"] is False
    assert server.Worker.CONFIG_KWARGS["proxy_headers"] is False
    assert server.Worker.CONFIG_KWARGS["server_header"] is False
    assert server.Worker.CONFIG_KWARGS["access_log"] is False


# ---------------------------------------------------------------------------
# Phase 1 (#436): routes on FastAPI answer as they did on Flask
# ---------------------------------------------------------------------------

MOVED = ("/healthz", "/metrics", "/api/app-status", "/api/endpoints")


def test_the_moved_routes_are_fastapis():
    import app as flask_app

    flask_paths = {rule.rule for rule in flask_app.app.url_map.iter_rules()}
    assert not set(MOVED) & flask_paths


def test_json_exactly_as_flask_writes_it():
    import app as flask_app
    from nautobot_maps.api import deps

    content = {"b": 1, "a": "Århus ø", "nested": {"z": None, "y": [1, 2]}}
    with flask_app.app.app_context():
        flask_body = flask_app.app.json.response(content).get_data()
    assert deps.Json(content).body == flask_body
    assert flask_body.endswith(b"\n") and b"\\u00c5rhus" in flask_body


def test_moved_routes_keep_headers_gzip_head_and_405(asgi_client):
    plain = asgi_client.get("/api/endpoints", headers={"Accept-Encoding": "identity"})
    assert plain.status_code == 200 and plain.headers.get("content-encoding") is None
    for name in ("content-security-policy", "x-content-type-options", "x-frame-options", "referrer-policy"):
        assert len(plain.headers.get_list(name)) == 1, name
    assert "Accept-Encoding" in plain.headers["vary"]
    zipped = asgi_client.get("/api/endpoints", headers={"Accept-Encoding": "gzip"})
    assert zipped.headers["content-encoding"] == "gzip" and zipped.json() == plain.json()
    assert asgi_client.head("/healthz").status_code == 200
    wrong = asgi_client.post("/healthz")
    assert wrong.status_code == 405 and wrong.json() == {"error": "Method not allowed"}
    assert {method.strip() for method in wrong.headers["allow"].split(",")} == {"GET", "HEAD"}
    metrics = asgi_client.get("/metrics")
    assert metrics.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert asgi_client.get("/api/app-status").headers["cache-control"] == "no-store"


def test_the_sign_in_check_applies_to_moved_routes(client, monkeypatch):
    """Through the whole stack from 127.0.0.1, as a proxy would send."""
    from tests.test_app import auth_config

    monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
    with auth_config(mode="header", viewer_groups={"noc"}):
        anonymous = client.get("/api/app-status")
        assert anonymous.status_code == 401 and anonymous.get_json() == {"error": "Authentication required"}
        nobody = client.get("/api/app-status", headers={"X-Forwarded-User": "x", "X-Forwarded-Groups": "other"})
        assert nobody.status_code == 403
        assert nobody.get_json() == {"error": "Insufficient permissions", "required_role": "viewer"}
        viewer = client.get("/api/app-status", headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "noc"})
        assert viewer.status_code == 200
        # Probes and Prometheus send no identity.
        assert client.get("/healthz").status_code == 200
        assert client.get("/metrics").status_code == 200
        # Identity headers from an untrusted address count for nothing.
        untrusted = client.get(
            "/api/app-status",
            headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "noc"},
            environ_base={"REMOTE_ADDR": "10.9.9.9"},
        )
        assert untrusted.status_code == 401


def _mini_app():
    """A FastAPI app with the shared pieces and one route of each kind."""
    from fastapi import APIRouter, FastAPI

    from nautobot_maps.api import deps

    router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES)

    @router.post("/api/x", dependencies=[deps.require_role("operator")])
    def write(count: int = 0):
        return deps.Json({"count": count})

    @router.get("/api/boom")
    def boom():
        raise RuntimeError("secret detail")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    deps.install_error_handlers(app)
    app.include_router(router)
    return app, router


def test_the_shared_pieces_refuse_like_flask(monkeypatch):
    from nautobot_maps.api import deps
    from tests.http_client import FlaskStyleClient
    from tests.test_app import auth_config

    app, router = _mini_app()
    assert deps.required_role(router.routes[0]) == "operator"
    client = FlaskStyleClient(app)
    # AUTH_MODE=disabled: a write needs ALLOW_UNAUTHENTICATED_WRITES.
    refused = client.post("/api/x")
    assert refused.status_code == 403 and refused.get_json()["error"] == "Authentication required for this change"
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)
    assert client.post("/api/x?count=3").get_json() == {"count": 3}
    # From another site: refused before the route.
    cross = client.post("/api/x", headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403 and cross.get_json() == {"error": "Changes from another site are refused"}
    # Bad input: 400 with a sentence, not FastAPI's 422 list.
    bad = client.post("/api/x?count=many")
    assert bad.status_code == 400 and bad.get_json()["error"].startswith("count:")
    # A crash: 500, nothing of it in the answer.
    crash = client.get("/api/boom")
    assert crash.status_code == 500 and crash.get_json() == {"error": "Internal server error"}
    with auth_config(mode="header", operator_groups={"ops"}):
        anonymous = client.post("/api/x")
        assert anonymous.status_code == 401 and anonymous.get_json() == {"error": "Authentication required"}
        no_role = client.post("/api/x", headers={"X-Forwarded-User": "v", "X-Forwarded-Groups": "nope"})
        assert no_role.status_code == 403 and no_role.get_json()["required_role"] == "operator"
        operator = client.post("/api/x", headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"})
        assert operator.status_code == 200
