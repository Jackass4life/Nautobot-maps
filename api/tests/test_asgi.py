"""The app served as ASGI (#436): FastAPI outside, the Flask app mounted behind
it.  Everything a browser, a script or a proxy relied on must be the same."""

from app.auth.permissions import Permission
from tests import db as adb
from tests import http_client


def test_security_headers_and_gzip_once(client):
    # The API as OpenAPI: big enough to compress, served without a frontend build.
    response = client.get("/api/openapi.json", headers={"Accept-Encoding": "gzip"})
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


def test_a_change_from_another_site_is_still_refused(client, override_settings):
    override_settings(ALLOW_UNAUTHENTICATED_WRITES=True)
    response = client.post("/api/v1/notifications/test", headers={"Sec-Fetch-Site": "cross-site"})
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


def test_moved_routes_keep_headers_gzip_head_and_405(client):
    plain = client.get("/api/v1/endpoints", headers={"Accept-Encoding": "identity"})
    assert plain.status_code == 200 and plain.headers.get("content-encoding") is None
    for name in (
        "content-security-policy",
        "x-content-type-options",
        "x-frame-options",
        "referrer-policy",
    ):
        assert len(plain.headers.get_list(name)) == 1, name
    assert "Accept-Encoding" in plain.headers["vary"]
    zipped = client.get("/api/v1/endpoints", headers={"Accept-Encoding": "gzip"})
    assert zipped.headers["content-encoding"] == "gzip" and zipped.json() == plain.json()
    assert client.head("/healthz").status_code == 200
    wrong = client.post("/healthz")
    assert wrong.status_code == 405 and wrong.json() == {"error": "Method not allowed"}
    assert {method.strip() for method in wrong.headers["allow"].split(",")} == {"GET", "HEAD"}
    head = client.head("/api/v1/app-status")
    assert (
        head.status_code == 200
        and head.content == b""
        and head.headers["content-type"] == "application/json"
    )
    metrics = client.get("/metrics")
    assert metrics.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert client.get("/api/v1/app-status").headers["cache-control"] == "no-store"


def test_the_sign_in_check_applies_to_moved_routes(client, override_settings):
    """Through the whole stack from 127.0.0.1, as a proxy would send."""
    from tests.test_app import auth_config

    override_settings(AUTH_REQUIRE_VIEWER=True)
    with auth_config(mode="header", viewer_groups={"noc"}):
        anonymous = client.get("/api/v1/app-status")
        assert anonymous.status_code == 401 and anonymous.json() == {
            "error": "Authentication required"
        }
        nobody = client.get(
            "/api/v1/app-status", headers={"X-Forwarded-User": "x", "X-Forwarded-Groups": "other"}
        )
        assert nobody.status_code == 403
        assert nobody.json() == {"error": "Insufficient permissions", "required_role": "viewer"}
        viewer = client.get(
            "/api/v1/app-status", headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "noc"}
        )
        assert viewer.status_code == 200
        # Probes and Prometheus send no identity.
        assert client.get("/healthz").status_code == 200
        assert client.get("/metrics").status_code == 200
        # Identity headers from an untrusted address count for nothing.
        untrusted = http_client.make(address=("10.9.9.9", 50000)).get(
            "/api/v1/app-status",
            headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "noc"},
        )
        assert untrusted.status_code == 401


def _mini_app():
    """A FastAPI app with the shared pieces and one route of each kind."""
    from fastapi import APIRouter, FastAPI

    from app.v1 import deps

    router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES)

    @router.post("/api/v1/x", dependencies=[deps.require_permission(Permission.ALERTS_ACK)])
    def write(count: int = 0):
        return deps.Json({"count": count})

    @router.get("/api/v1/boom")
    def boom():
        raise RuntimeError("secret detail")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    deps.install_error_handlers(app)
    app.include_router(router)
    return app, router


def test_the_shared_pieces_refuse_like_flask(override_settings):
    from app.v1 import deps
    from tests.test_app import auth_config

    app, router = _mini_app()
    assert deps.required_role(router.routes[0]) == "operator"
    # A crash answers 500 without its detail: the client gets the answer, not the exception.
    client = http_client.make(app, raise_server_exceptions=False)
    # AUTH_MODE=disabled: a write needs ALLOW_UNAUTHENTICATED_WRITES.
    refused = client.post("/api/v1/x")
    assert (
        refused.status_code == 403
        and refused.json()["error"] == "Authentication required for this change"
    )
    override_settings(ALLOW_UNAUTHENTICATED_WRITES=True)
    assert client.post("/api/v1/x?count=3").json() == {"count": 3}
    # From another site: refused before the route.
    cross = client.post("/api/v1/x", headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403 and cross.json() == {
        "error": "Changes from another site are refused"
    }
    # Bad input: 400 with a sentence, not FastAPI's 422 list.
    bad = client.post("/api/v1/x?count=many")
    assert bad.status_code == 400 and bad.json()["error"].startswith("count:")
    # A crash: 500, nothing of it in the answer.
    crash = client.get("/api/v1/boom")
    assert crash.status_code == 500 and crash.json() == {"error": "Internal server error"}
    with auth_config(mode="header", operator_groups={"ops"}):
        anonymous = client.post("/api/v1/x")
        assert anonymous.status_code == 401 and anonymous.json() == {
            "error": "Authentication required"
        }
        no_role = client.post(
            "/api/v1/x", headers={"X-Forwarded-User": "v", "X-Forwarded-Groups": "nope"}
        )
        assert no_role.status_code == 403 and no_role.json()["required_role"] == "operator"
        operator = client.post(
            "/api/v1/x", headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"}
        )
        assert operator.status_code == 200


# ---------------------------------------------------------------------------
# Phase 2 (#436): pages rendered by FastAPI are Flask's pages
# ---------------------------------------------------------------------------


def test_the_status_page_is_fastapis_with_its_own_referrer_policy(client, frontend_build):
    page = client.get("/status/Acme%20Corp")
    assert page.status_code == 200 and page.headers["content-type"] == "text/html; charset=utf-8"
    assert page.headers.get_list("referrer-policy") == ["no-referrer"]
    assert len(page.headers.get_list("content-security-policy")) == 1
    # Its files: the Nuxt build's (#463).
    assert client.get("/_nuxt/entry.abc123.js").status_code == 200


# ---------------------------------------------------------------------------
# Phase 5 (#436): writes on FastAPI read bodies and paths as Flask did
# ---------------------------------------------------------------------------


def test_a_window_id_that_is_not_a_number_is_still_a_404(client, override_settings):
    override_settings(ALLOW_UNAUTHENTICATED_WRITES=True)
    response = client.post("/api/v1/maintenance/abc/end")
    assert response.status_code == 404 and response.json() == {"error": "Not found"}


def test_a_body_is_json_only_when_it_says_so(client, override_settings):
    """As Flask's get_json(silent=True): a JSON body sent as text/plain (what a
    cross-site form could send) is no body at all."""
    override_settings(
        ALLOW_UNAUTHENTICATED_WRITES=True, NAUTOBOT_MAPS_DATABASE_URL="postgresql://unused/x"
    )
    as_text = client.post(
        "/api/v1/maintenance", content='{"site_id": "x"}', headers={"Content-Type": "text/plain"}
    )
    assert as_text.status_code == 400 and as_text.json() == {"error": "Expected a JSON object"}
    broken = client.post(
        "/api/v1/maintenance", content="{not json", headers={"Content-Type": "application/json"}
    )
    assert broken.status_code == 400 and broken.json() == {"error": "Expected a JSON object"}
    ids = client.post(
        "/api/v1/maintenance/end",
        content='{"ids": []}',
        headers={"Content-Type": "application/merge-patch+json"},
    )
    assert ids.status_code == 400 and "ids must be a list" in ids.json()["error"]


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

    @router.post("/api/v1/form", dependencies=[deps.require_permission(Permission.ALERTS_ACK)])
    def echo(form: Annotated[dict, Depends(remember_form)]):
        return deps.Json(form)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    deps.install_error_handlers(app)
    app.include_router(router)
    return app


def test_forms_are_read_as_flask_read_them(override_settings):
    from fastapi.testclient import TestClient

    override_settings(ALLOW_UNAUTHENTICATED_WRITES=True)
    client = TestClient(_form_app([]), base_url="http://localhost", client=("127.0.0.1", 1))
    assert client.post("/api/v1/form", data={"hostname": "sw1"}).json() == {"hostname": "sw1"}
    multipart = client.post(
        "/api/v1/form", data={"device_id": "42"}, files={"note": ("n.txt", b"x")}
    )
    assert multipart.json() == {"device_id": "42"}, "fields, not uploaded files"
    assert client.post("/api/v1/form", json={"hostname": "sw1"}).json() == {}


def test_a_refused_caller_never_gets_its_body_read(monkeypatch):
    """The role check runs before the body or form is read, as Flask's decorator
    ran before the view: no parsing on behalf of someone refused."""
    from fastapi.testclient import TestClient

    seen = []
    client = TestClient(_form_app(seen), base_url="http://localhost", client=("127.0.0.1", 1))
    refused = client.post("/api/v1/form", data={"hostname": "sw1"})
    assert refused.status_code == 403 and seen == []
    cross = client.post(
        "/api/v1/form", data={"hostname": "sw1"}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert cross.status_code == 403 and seen == []


def test_a_new_token_is_never_compressed_on_fastapi(client, monkeypatch):
    from app.frontend import compression
    from app.services import token_service as tokens

    monkeypatch.setattr(compression, "MIN_BYTES", 10)
    admin = adb.run(tokens.create, {"name": "boot", "role": "admin"}, "test")["token"]
    created = client.post(
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
