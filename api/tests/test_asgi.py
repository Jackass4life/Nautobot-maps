"""The app served as ASGI (#436): FastAPI outside, the Flask app mounted behind
it.  Everything a browser, a script or a proxy relied on must be the same."""

from app.utils.legacy_settings import settings


def test_security_headers_and_gzip_once(asgi_client):
    # The API as OpenAPI: big enough to compress, served without a frontend build.
    response = asgi_client.get("/api/openapi.json", headers={"Accept-Encoding": "gzip"})
    for name in (
        "content-security-policy",
        "x-content-type-options",
        "x-frame-options",
        "referrer-policy",
    ):
        assert len(response.headers.get_list(name)) == 1, name
    assert response.headers.get_list("content-encoding") == ["gzip"]
    # No "server: uvicorn" announcing the stack.
    assert "uvicorn" not in response.headers.get("server", "")


def test_a_change_from_another_site_is_still_refused(asgi_client, monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)
    response = asgi_client.post(
        "/api/v1/notifications/test", headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert response.status_code == 403 and response.json() == {
        "error": "Changes from another site are refused"
    }


# ---------------------------------------------------------------------------
# Phase 1 (#436): routes on FastAPI answer as they did on Flask
# ---------------------------------------------------------------------------

MOVED = ("/healthz", "/metrics", "/api/v1/app-status", "/api/v1/endpoints")


def test_json_exactly_as_flask_wrote_it():
    """Keys sorted, non-ASCII escaped, compact, a newline at the end; dates as
    Flask's HTTP dates, decimals and UUIDs as text."""
    import datetime
    import decimal
    import uuid

    from app.v1 import deps

    content = {"b": 1, "a": "Århus ø", "nested": {"z": None, "y": [1, 2]}}
    assert (
        deps.Json(content).body
        == b'{"a":"\\u00c5rhus \\u00f8","b":1,"nested":{"y":[1,2],"z":null}}\n'
    )
    when = datetime.datetime(
        2026, 10, 22, 14, 5, tzinfo=datetime.timezone(datetime.timedelta(hours=2))
    )
    odd = {
        "at": when,
        "naive": datetime.datetime(2026, 10, 22, 12, 5),
        "day": datetime.date(2026, 10, 22),
        "n": decimal.Decimal("1.50"),
        "id": uuid.UUID(int=1),
    }
    assert deps.Json(odd).body == (
        b'{"at":"Thu, 22 Oct 2026 12:05:00 GMT","day":"Thu, 22 Oct 2026 00:00:00 GMT",'
        b'"id":"00000000-0000-0000-0000-000000000001","n":"1.50","naive":"Thu, 22 Oct 2026 12:05:00 GMT"}\n'
    )


def test_moved_routes_keep_headers_gzip_head_and_405(asgi_client):
    plain = asgi_client.get("/api/v1/endpoints", headers={"Accept-Encoding": "identity"})
    assert plain.status_code == 200 and plain.headers.get("content-encoding") is None
    for name in (
        "content-security-policy",
        "x-content-type-options",
        "x-frame-options",
        "referrer-policy",
    ):
        assert len(plain.headers.get_list(name)) == 1, name
    assert "Accept-Encoding" in plain.headers["vary"]
    zipped = asgi_client.get("/api/v1/endpoints", headers={"Accept-Encoding": "gzip"})
    assert zipped.headers["content-encoding"] == "gzip" and zipped.json() == plain.json()
    assert asgi_client.head("/healthz").status_code == 200
    wrong = asgi_client.post("/healthz")
    assert wrong.status_code == 405 and wrong.json() == {"error": "Method not allowed"}
    assert {method.strip() for method in wrong.headers["allow"].split(",")} == {"GET", "HEAD"}
    head = asgi_client.head("/api/v1/app-status")
    assert (
        head.status_code == 200
        and head.content == b""
        and head.headers["content-type"] == "application/json"
    )
    metrics = asgi_client.get("/metrics")
    assert metrics.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert asgi_client.get("/api/v1/app-status").headers["cache-control"] == "no-store"


def test_the_sign_in_check_applies_to_moved_routes(flask_client, monkeypatch):
    """Through the whole stack from 127.0.0.1, as a proxy would send."""
    from tests.test_app import auth_config

    monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
    with auth_config(mode="header", viewer_groups={"noc"}):
        anonymous = flask_client.get("/api/v1/app-status")
        assert anonymous.status_code == 401 and anonymous.get_json() == {
            "error": "Authentication required"
        }
        nobody = flask_client.get(
            "/api/v1/app-status", headers={"X-Forwarded-User": "x", "X-Forwarded-Groups": "other"}
        )
        assert nobody.status_code == 403
        assert nobody.get_json() == {"error": "Insufficient permissions", "required_role": "viewer"}
        viewer = flask_client.get(
            "/api/v1/app-status", headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "noc"}
        )
        assert viewer.status_code == 200
        # Probes and Prometheus send no identity.
        assert flask_client.get("/healthz").status_code == 200
        assert flask_client.get("/metrics").status_code == 200
        # Identity headers from an untrusted address count for nothing.
        untrusted = flask_client.get(
            "/api/v1/app-status",
            headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "noc"},
            environ_base={"REMOTE_ADDR": "10.9.9.9"},
        )
        assert untrusted.status_code == 401


def _mini_app():
    """A FastAPI app with the shared pieces and one route of each kind."""
    from fastapi import APIRouter, FastAPI

    from app.v1 import deps

    router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES)

    @router.post("/api/v1/x", dependencies=[deps.require_role("operator")])
    def write(count: int = 0):
        return deps.Json({"count": count})

    @router.get("/api/v1/boom")
    def boom():
        raise RuntimeError("secret detail")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    deps.install_error_handlers(app)
    app.include_router(router)
    return app, router


def test_the_shared_pieces_refuse_like_flask(monkeypatch):
    from app.v1 import deps
    from tests.http_client import FlaskStyleClient
    from tests.test_app import auth_config

    app, router = _mini_app()
    assert deps.required_role(router.routes[0]) == "operator"
    flask_client = FlaskStyleClient(app)
    # AUTH_MODE=disabled: a write needs ALLOW_UNAUTHENTICATED_WRITES.
    refused = flask_client.post("/api/v1/x")
    assert (
        refused.status_code == 403
        and refused.get_json()["error"] == "Authentication required for this change"
    )
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)
    assert flask_client.post("/api/v1/x?count=3").get_json() == {"count": 3}
    # From another site: refused before the route.
    cross = flask_client.post("/api/v1/x", headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403 and cross.get_json() == {
        "error": "Changes from another site are refused"
    }
    # Bad input: 400 with a sentence, not FastAPI's 422 list.
    bad = flask_client.post("/api/v1/x?count=many")
    assert bad.status_code == 400 and bad.get_json()["error"].startswith("count:")
    # A crash: 500, nothing of it in the answer.
    crash = flask_client.get("/api/v1/boom")
    assert crash.status_code == 500 and crash.get_json() == {"error": "Internal server error"}
    with auth_config(mode="header", operator_groups={"ops"}):
        anonymous = flask_client.post("/api/v1/x")
        assert anonymous.status_code == 401 and anonymous.get_json() == {
            "error": "Authentication required"
        }
        no_role = flask_client.post(
            "/api/v1/x", headers={"X-Forwarded-User": "v", "X-Forwarded-Groups": "nope"}
        )
        assert no_role.status_code == 403 and no_role.get_json()["required_role"] == "operator"
        operator = flask_client.post(
            "/api/v1/x", headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"}
        )
        assert operator.status_code == 200


# ---------------------------------------------------------------------------
# Phase 2 (#436): pages rendered by FastAPI are Flask's pages
# ---------------------------------------------------------------------------


def test_the_status_page_is_fastapis_with_its_own_referrer_policy(flask_client, frontend_build):
    page = flask_client.get("/status/Acme%20Corp")
    assert page.status_code == 200 and page.headers["content-type"] == "text/html; charset=utf-8"
    assert page.headers.get_list("referrer-policy") == ["no-referrer"]
    assert len(page.headers.get_list("content-security-policy")) == 1
    # Its files: the Nuxt build's (#463).
    assert flask_client.get("/_nuxt/entry.abc123.js").status_code == 200


# ---------------------------------------------------------------------------
# Phase 5 (#436): writes on FastAPI read bodies and paths as Flask did
# ---------------------------------------------------------------------------


def test_a_window_id_that_is_not_a_number_is_still_a_404(flask_client, monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)
    response = flask_client.post("/api/v1/maintenance/abc/end")
    assert response.status_code == 404 and response.get_json() == {"error": "Not found"}


def test_a_body_is_json_only_when_it_says_so(flask_client, monkeypatch):
    """As Flask's get_json(silent=True): a JSON body sent as text/plain (what a
    cross-site form could send) is no body at all."""
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)
    monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "postgresql://unused/x")
    from app import db

    monkeypatch.setattr(
        db, "get_conn", lambda **kwargs: type("C", (), {"close": lambda self: None})()
    )
    as_text = flask_client.post(
        "/api/v1/maintenance", data='{"site_id": "x"}', content_type="text/plain"
    )
    assert as_text.status_code == 400 and as_text.get_json() == {"error": "Expected a JSON object"}
    broken = flask_client.post(
        "/api/v1/maintenance", data="{not json", content_type="application/json"
    )
    assert broken.status_code == 400 and broken.get_json() == {"error": "Expected a JSON object"}
    ids = flask_client.post(
        "/api/v1/maintenance/end", data='{"ids": []}', content_type="application/merge-patch+json"
    )
    assert ids.status_code == 400 and "ids must be a list" in ids.get_json()["error"]


# ---------------------------------------------------------------------------
# Phase 8 (#436): pushes and admin read forms and refuse as Flask did
# ---------------------------------------------------------------------------


def _form_app(seen: list):
    from typing import Annotated

    from fastapi import APIRouter, Depends, FastAPI

    from app.v1 import deps

    router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES)

    async def remember_form(form: Annotated[dict, Depends(deps.form_values)]):
        seen.append(form)
        return form

    @router.post("/api/v1/form", dependencies=[deps.require_role("operator")])
    def echo(form: Annotated[dict, Depends(remember_form)]):
        return deps.Json(form)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    deps.install_error_handlers(app)
    app.include_router(router)
    return app


def test_forms_are_read_as_flask_read_them(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)
    flask_client = TestClient(_form_app([]), base_url="http://localhost", client=("127.0.0.1", 1))
    assert flask_client.post("/api/v1/form", data={"hostname": "sw1"}).json() == {"hostname": "sw1"}
    multipart = flask_client.post(
        "/api/v1/form", data={"device_id": "42"}, files={"note": ("n.txt", b"x")}
    )
    assert multipart.json() == {"device_id": "42"}, "fields, not uploaded files"
    assert flask_client.post("/api/v1/form", json={"hostname": "sw1"}).json() == {}


def test_a_refused_caller_never_gets_its_body_read(monkeypatch):
    """The role check runs before the body or form is read, as Flask's decorator
    ran before the view: no parsing on behalf of someone refused."""
    from fastapi.testclient import TestClient

    seen = []
    flask_client = TestClient(_form_app(seen), base_url="http://localhost", client=("127.0.0.1", 1))
    refused = flask_client.post("/api/v1/form", data={"hostname": "sw1"})
    assert refused.status_code == 403 and seen == []
    cross = flask_client.post(
        "/api/v1/form", data={"hostname": "sw1"}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert cross.status_code == 403 and seen == []


def test_a_new_token_is_never_compressed_on_fastapi(flask_client, monkeypatch, pg_database):
    from app import db
    from app.frontend import compression
    from app.services import token_service as tokens

    monkeypatch.setattr(compression, "MIN_BYTES", 10)
    conn = db.get_conn()
    try:
        admin = tokens.create(conn, {"name": "boot", "role": "admin"}, "test")["token"]
    finally:
        conn.close()
    created = flask_client.post(
        "/api/v1/tokens",
        json={"name": "s", "role": "viewer"},
        headers={"Accept-Encoding": "gzip", "Authorization": f"Bearer {admin}"},
    )
    assert created.status_code == 201 and "content-encoding" not in created.headers
    assert created.headers["cache-control"] == "no-store, no-transform"


# The access log and server tests are in tests/test_server.py (#613).

# Left out: the current app's server details (Flask's bind address, its access-log
# format, templates/ and /static/, HTML error pages for browsers); the pages and
# their error page come in step 8 of #587, access logging at the switchover.
#   test_one_access_log_line_with_the_same_fields
#   test_no_flask_is_left
#   test_templates_escape_and_link_as_flask_did
#   test_pages_and_apis_are_served_through_fastapi
