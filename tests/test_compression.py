"""gzip for large text responses (#349)."""

import gzip
import json
import pathlib

import pytest
from flask import Response, jsonify

import app as flask_app
from nautobot_maps import compression, db, settings, tokens

GZIP = {"Accept-Encoding": "gzip, deflate, br"}
BIG = {"alerts": [{"id": f"s{i}", "name": f"Site {i:04d}", "alert_level": "ok"} for i in range(1200)]}


def run(response, path="/api/alerts", headers=GZIP):
    with flask_app.app.test_request_context(path, headers=headers):
        return compression.compress(response)


class TestRules:
    def _json(self):
        with flask_app.app.test_request_context("/"):
            return jsonify(BIG)

    def test_a_large_json_answer_is_gzipped(self):
        plain = self._json().get_data()
        response = run(self._json())
        assert response.headers["Content-Encoding"] == "gzip" and "Accept-Encoding" in response.vary
        assert gzip.decompress(response.get_data()) == plain
        assert int(response.headers["Content-Length"]) == len(response.get_data()) < len(plain) / 5

    @pytest.mark.parametrize(
        ("change", "why"),
        [
            (lambda r: None, "no gzip in Accept-Encoding"),
            (lambda r: r.headers.__setitem__("Content-Encoding", "br"), "already encoded"),
            (lambda r: r.headers.__setitem__("Cache-Control", "no-store, no-transform"), "no-transform"),
            (lambda r: setattr(r, "status_code", 206), "partial content"),
        ],
    )
    def test_left_alone(self, change, why):
        response = self._json()
        change(response)
        headers = {} if why.startswith("no gzip") else GZIP
        out = run(response, headers=headers)
        assert out.headers.get("Content-Encoding") in (None, "br"), why

    def test_gzip_refused_by_quality_zero(self):
        assert "Content-Encoding" not in run(self._json(), headers={"Accept-Encoding": "gzip;q=0"}).headers

    def test_small_images_streams_and_tokens(self):
        small = run(Response("x" * 100, mimetype="application/json"))
        assert "Content-Encoding" not in small.headers and "Accept-Encoding" in small.vary
        image = run(Response(b"\x89PNG" + b"0" * 5000, mimetype="image/png"))
        assert "Content-Encoding" not in image.headers and "Accept-Encoding" not in image.vary
        # A streamed export goes out as it is made: never read whole to compress.
        stream = run(Response((f"{i},row\r\n" for i in range(5000)), mimetype="text/csv"))
        assert "Content-Encoding" not in stream.headers and stream.is_streamed
        # +json and +xml are text too.
        geo = run(Response(json.dumps(BIG), mimetype="application/geo+json"))
        assert geo.headers["Content-Encoding"] == "gzip"

    def test_head_and_large_files(self):
        with flask_app.app.test_request_context("/", method="HEAD", headers=GZIP):
            assert "Content-Encoding" not in compression.compress(self._json()).headers

    def test_the_setting_turns_it_off(self, monkeypatch):
        monkeypatch.setattr(settings, "COMPRESS_RESPONSES", False)
        assert "Content-Encoding" not in run(self._json()).headers


class TestRealRequests:
    @pytest.fixture
    def client(self):
        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as test_client:
            yield test_client

    def test_static_files_and_their_cache(self, client):
        path = "/static/js/lib/calendar.js"
        source = (pathlib.Path(flask_app.app.static_folder) / "js/lib/calendar.js").read_bytes()
        resp = client.get(path, headers=GZIP)
        assert resp.headers["Content-Encoding"] == "gzip" and gzip.decompress(resp.get_data()) == source
        etag = resp.headers["ETag"]
        assert etag.startswith('W/"'), "the bytes differ from the file's: a weak ETag"
        # The browser's cached copy is still current: 304, nothing sent again.
        again = client.get(path, headers={**GZIP, "If-None-Match": etag})
        assert again.status_code == 304 and again.get_data() == b""
        plain = client.get(path)
        assert "Content-Encoding" not in plain.headers and plain.get_data() == source

    def test_a_compressed_file_offers_no_byte_ranges(self, client):
        resp = client.get("/static/js/lib/calendar.js", headers=GZIP)
        assert resp.headers["Content-Encoding"] == "gzip" and "Accept-Ranges" not in resp.headers
        assert client.get("/static/js/lib/calendar.js").headers.get("Accept-Ranges") == "bytes", "plain: as before"

    def test_a_new_token_is_never_compressed(self, client, monkeypatch, pg_database):
        # The secret is shown once beside the name the client gave it (BREACH): no-transform.
        monkeypatch.setattr(compression, "MIN_BYTES", 10)
        conn = db.get_conn()
        try:
            admin = tokens.create(conn, {"name": "bootstrap", "role": "admin"}, "test")["token"]
        finally:
            conn.close()
        resp = client.post(
            "/api/tokens",
            json={"name": "script", "role": "viewer"},
            headers={**GZIP, "Authorization": f"Bearer {admin}"},
        )
        assert resp.status_code == 201 and "Content-Encoding" not in resp.headers
        assert "no-transform" in resp.headers["Cache-Control"] and "nmt_" in resp.get_data(as_text=True)

    def test_a_page(self, client):
        resp = client.get("/docs", headers=GZIP)
        assert resp.headers["Content-Encoding"] == "gzip"
        assert b"<!DOCTYPE html>" in gzip.decompress(resp.get_data())
