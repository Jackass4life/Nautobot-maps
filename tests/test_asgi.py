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
