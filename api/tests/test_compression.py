"""gzip for large text responses (#349), on every route's answer (#436)."""

import gzip
import json
import pathlib

import pytest
from fastapi.responses import Response, StreamingResponse
from starlette.requests import Request

from app import db
from app.frontend import compression
from app.services import token_service as tokens
from app.utils.legacy_settings import settings
from app.v1 import deps
from tests import http_client

GZIP = {"Accept-Encoding": "gzip, deflate, br"}
BIG = {
    "alerts": [{"id": f"s{i}", "name": f"Site {i:04d}", "alert_level": "ok"} for i in range(1200)]
}
STATIC = pathlib.Path(__file__).resolve().parent.parent / "static"


def _request(method: str = "GET", headers: dict | None = None) -> Request:
    raw = [
        (name.lower().encode(), value.encode())
        for name, value in (GZIP if headers is None else headers).items()
    ]
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/v1/alerts",
            "headers": raw,
            "query_string": b"",
        }
    )


def run(response, headers=None, method="GET"):
    return deps.finish(_request(method, headers), response)


def _vary(response) -> str:
    return response.headers.get("vary", "")


class TestRules:
    def _json(self):
        return deps.Json(BIG)

    def test_a_large_json_answer_is_gzipped(self):
        plain = self._json().body
        response = run(self._json())
        assert response.headers["content-encoding"] == "gzip" and "Accept-Encoding" in _vary(
            response
        )
        assert gzip.decompress(response.body) == plain
        assert int(response.headers["content-length"]) == len(response.body) < len(plain) / 5

    @pytest.mark.parametrize(
        ("change", "why"),
        [
            (lambda r: None, "no gzip in Accept-Encoding"),
            (lambda r: r.headers.__setitem__("content-encoding", "br"), "already encoded"),
            (
                lambda r: r.headers.__setitem__("cache-control", "no-store, no-transform"),
                "no-transform",
            ),
            (lambda r: setattr(r, "status_code", 206), "partial content"),
        ],
    )
    def test_left_alone(self, change, why):
        response = self._json()
        change(response)
        headers = {} if why.startswith("no gzip") else GZIP
        out = run(response, headers=headers)
        assert out.headers.get("content-encoding") in (None, "br"), why

    def test_gzip_refused_by_quality_zero(self):
        assert (
            "content-encoding"
            not in run(self._json(), headers={"Accept-Encoding": "gzip;q=0"}).headers
        )

    def test_small_images_streams_and_tokens(self):
        small = run(Response("x" * 100, media_type="application/json"))
        assert "content-encoding" not in small.headers and "Accept-Encoding" in _vary(small)
        image = run(Response(b"\x89PNG" + b"0" * 5000, media_type="image/png"))
        assert "content-encoding" not in image.headers and "Accept-Encoding" not in _vary(image)
        # A streamed export goes out as it is made: never read whole to compress.
        stream = run(
            StreamingResponse((f"{i},row\r\n" for i in range(5000)), media_type="text/csv")
        )
        assert "content-encoding" not in stream.headers
        # +json and +xml are text too.
        geo = run(Response(json.dumps(BIG), media_type="application/geo+json"))
        assert geo.headers["content-encoding"] == "gzip"

    def test_head_and_large_files(self):
        assert "content-encoding" not in run(self._json(), method="HEAD").headers

    def test_the_setting_turns_it_off(self, monkeypatch):
        monkeypatch.setattr(settings, "COMPRESS_RESPONSES", False)
        assert "content-encoding" not in run(self._json()).headers


class TestRealRequests:
    @pytest.fixture
    def flask_client(self):
        """The whole app, as production serves it (#436)."""
        with http_client.FlaskStyleClient() as test_client:
            yield test_client

    def test_a_new_token_is_never_compressed(self, flask_client, monkeypatch, pg_database):
        # The secret is shown once beside the name the flask_client gave it (BREACH): no-transform.
        monkeypatch.setattr(compression, "MIN_BYTES", 10)
        conn = db.get_conn()
        try:
            admin = tokens.create(conn, {"name": "bootstrap", "role": "admin"}, "test")["token"]
        finally:
            conn.close()
        resp = flask_client.post(
            "/api/v1/tokens",
            json={"name": "script", "role": "viewer"},
            headers={**GZIP, "Authorization": f"Bearer {admin}"},
        )
        assert resp.status_code == 201 and "Content-Encoding" not in resp.headers
        assert "no-transform" in resp.headers["Cache-Control"] and "nmt_" in resp.get_data(
            as_text=True
        )

    def test_a_page(self, flask_client, frontend_build):
        # A page is the Nuxt build's shell (#463), about 1.5 KB like the real one.
        shell = frontend_build / "200.html"
        shell.write_text(shell.read_text().replace("</head>", "<!--" + "x" * 1500 + "--></head>"))
        resp = flask_client.get("/api/docs", headers=GZIP)
        assert resp.headers["Content-Encoding"] == "gzip"
        assert b"<!DOCTYPE html>" in gzip.decompress(resp.get_data())


# Left out: the current app's server details (Flask's bind address, its access-log
# format, templates/ and /static/, HTML error pages for browsers); the pages and
# their error page come in step 8 of #587, access logging at the switchover.
#   test_static_files_and_their_cache
#   test_a_compressed_file_offers_no_byte_ranges
