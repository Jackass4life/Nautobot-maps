"""web/'s pages served from this origin, the security headers and gzip (#594),
as the current app's tests/test_frontend.py, test_compression and the headers."""

import gzip
import json
import os

import pytest

SHELL = (
    '<!DOCTYPE html><html><head><script type="module" src="/_nuxt/entry.js"></script>'
    '</head><body><div id="__nuxt"></div></body></html>'
)
BIG_JS = "console.log('x');\n" * 200  # over 1 KiB


@pytest.fixture
def built(tmp_path, configure):
    """A build of web/ as `npm run generate` leaves it."""
    (tmp_path / "_nuxt").mkdir()
    (tmp_path / "200.html").write_text(SHELL, encoding="utf-8")
    (tmp_path / "_nuxt" / "entry.js").write_text(BIG_JS, encoding="utf-8")
    (tmp_path / "_nuxt" / "logo.png").write_bytes(b"\x89PNG" + b"\0" * 2000)
    (tmp_path / "secret.txt").write_text("outside _nuxt", encoding="utf-8")
    configure(WEB_BUILD_DIR=str(tmp_path))
    return tmp_path


def test_a_page_is_the_shell(client, built):
    response = client.get("/")
    assert response.status_code == 200 and response.text == SHELL
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["content-type"].startswith("text/html")


def test_not_built_says_how(client, configure, tmp_path):
    configure(WEB_BUILD_DIR=str(tmp_path / "missing"))
    response = client.get("/")
    assert response.status_code == 503 and "npm run generate" in response.text


def test_assets_are_cached_a_year_with_etag_and_gzip(client, built):
    response = client.get("/_nuxt/entry.js", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000"
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["etag"].startswith('W/"')
    assert response.text == BIG_JS  # the client unzips it
    again = client.get(
        "/_nuxt/entry.js",
        headers={"Accept-Encoding": "gzip", "If-None-Match": response.headers["etag"]},
    )
    assert again.status_code == 304
    plain = client.get("/_nuxt/entry.js", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers and plain.headers["etag"].startswith('"')
    assert plain.headers["vary"] == "Accept-Encoding"
    image = client.get("/_nuxt/logo.png", headers={"Accept-Encoding": "gzip"})
    assert image.status_code == 200 and "content-encoding" not in image.headers


@pytest.mark.parametrize("path", ["../secret.txt", "..%2Fsecret.txt", "missing.js", ""])
def test_nothing_outside_the_assets(client, built, path):
    assert client.get(f"/_nuxt/{path}").status_code == 404


def test_a_symlink_out_is_not_followed(client, built):
    os.symlink(built / "secret.txt", built / "_nuxt" / "link.txt")
    assert client.get("/_nuxt/link.txt").status_code == 404


def test_assets_are_public_with_require_viewer(client, built, configure):
    configure(AUTH_MODE="header", AUTH_REQUIRE_VIEWER="true")
    assert client.get("/_nuxt/entry.js").status_code == 200
    assert client.get("/").status_code == 401


@pytest.mark.parametrize(
    "path", ["/", "/api/v1/me", "/api/v1/items/999", "/no/such/path", "/_nuxt/entry.js"]
)
def test_every_answer_has_the_security_headers(client, built, path):
    headers = client.get(path).headers
    csp = headers["content-security-policy"]
    assert csp.startswith("default-src 'self'; script-src 'self'; style-src 'self'")
    assert "img-src 'self' data: https://*.tile.openstreetmap.org" in csp
    assert "frame-ancestors 'none'" in csp
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_the_tile_host_is_in_the_csp(client, configure):
    configure(MAP_TILE_URL="https://tiles.example.com/{z}/{x}/{y}.png")
    csp = client.get("/api/v1/me").headers["content-security-policy"]
    assert "img-src 'self' data: https://tiles.example.com;" in csp
    configure(MAP_TILE_URL="/tiles/{z}/{x}/{y}.png")
    assert "img-src 'self' data:;" in client.get("/api/v1/me").headers["content-security-policy"]


class TestGzip:
    """The current app's rules (compression.py, #349)."""

    def big(self, client, **headers):
        # The spec is well over 1 KiB of JSON.
        return client.get("/api/openapi.json", headers=headers)

    def test_large_json_is_gzipped(self, client):
        response = self.big(client, **{"Accept-Encoding": "gzip"})
        assert response.headers["content-encoding"] == "gzip"
        assert "Accept-Encoding" in response.headers["vary"]
        assert int(response.headers["content-length"]) < len(response.content)
        assert json.loads(response.content)["openapi"]

    def test_not_without_accept_encoding(self, client):
        response = self.big(client, **{"Accept-Encoding": "identity"})
        assert "content-encoding" not in response.headers
        assert "Accept-Encoding" in response.headers["vary"]
        response = self.big(client, **{"Accept-Encoding": "gzip;q=0"})
        assert "content-encoding" not in response.headers

    def test_small_answers_stay_plain(self, client):
        response = client.get("/api/v1/me", headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in response.headers

    def test_head_is_the_gets_headers_without_the_body(self, client):
        """As the current app (#604): HEAD answers as GET, its headers included."""
        response = client.head("/api/openapi.json", headers={"Accept-Encoding": "gzip"})
        assert response.headers["content-encoding"] == "gzip" and response.content == b""

    def test_switched_off(self, client, configure):
        configure(COMPRESS_RESPONSES="false")
        response = self.big(client, **{"Accept-Encoding": "gzip"})
        assert "content-encoding" not in response.headers and "vary" not in response.headers

    def test_no_transform_is_never_gzipped(self):
        """A new API token's answer (BREACH): checked on the middleware itself."""
        import asyncio

        from app.frontend.middleware import FinishMiddleware

        body = b'{"token": "' + b"x" * 4000 + b'"}'

        async def app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"cache-control", b"no-store, no-transform"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})

        sent = []

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "method": "GET",
            "headers": [(b"accept-encoding", b"gzip")],
        }
        asyncio.run(FinishMiddleware(app)(scope, None, send))
        headers = dict(sent[0]["headers"])
        assert b"content-encoding" not in headers and sent[1]["body"] == body

    def test_streamed_answers_stay_plain(self):
        import asyncio

        from app.frontend.middleware import FinishMiddleware

        async def app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/csv")],
                }
            )
            await send({"type": "http.response.body", "body": b"a" * 2000, "more_body": True})
            await send({"type": "http.response.body", "body": b"b" * 2000})

        sent = []

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "GET", "headers": [(b"accept-encoding", b"gzip")]}
        asyncio.run(FinishMiddleware(app)(scope, None, send))
        assert b"content-encoding" not in dict(sent[0]["headers"])
        assert b"".join(m.get("body", b"") for m in sent[1:]) == b"a" * 2000 + b"b" * 2000
        assert gzip  # imported for the readers of these rules


def test_an_unhandled_error_has_the_security_headers_too():
    """Starlette answers it outside the app's middleware (#594 review)."""
    from fastapi import APIRouter
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    broken = APIRouter()

    @broken.get("/api/v1/boom")
    def boom():
        raise RuntimeError("database read failed")

    app.include_router(broken)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/boom")
    assert response.status_code == 500 and response.json() == {"error": "Internal server error"}
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert response.headers["x-frame-options"] == "DENY"
    assert "database read failed" not in response.text


def test_gzip_refused_by_name_wins_over_the_wildcard():
    """#601 review: an explicit gzip;q=0 refuses it, whatever * says."""
    from app.frontend.compression import accepts_gzip

    assert accepts_gzip("*;q=1, gzip;q=0") is False
    assert accepts_gzip("gzip;q=0, *;q=1") is False
    assert accepts_gzip("br, *") is True
    assert accepts_gzip("gzip") is True
    assert accepts_gzip("identity") is False
