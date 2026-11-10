"""One top bar on every staff page (#419): a site search, how fresh the data
is, who is signed in, the theme and the pages."""

import os
import pathlib
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from app.core.legacy_settings import settings
from app.services import metrics_service as metrics
from tests.test_app import auth_config

NOW = datetime(2026, 10, 22, 14, 0, tzinfo=UTC).timestamp()
INTERVALS = {"INVENTORY_SYNC_INTERVAL_SECONDS": 300, "LIBRENMS_SYNC_INTERVAL_SECONDS": 60}


def row(minutes_ago, status="idle"):
    return {
        "status": status,
        "last_succeeded_at": datetime.fromtimestamp(NOW - minutes_ago * 60, UTC),
    }


def test_fresh_data_is_ok_and_says_when():
    status = metrics.app_status(
        {"nautobot_inventory": row(4), "librenms_inventory": row(1)},
        librenms=True,
        intervals=INTERVALS,
        now=NOW,
    )
    assert status["state"] == "ok"
    # Only as fresh as its oldest source.
    assert status["updated_at"] == "2026-10-22T13:56:00Z"
    assert [source["name"] for source in status["sources"]] == ["Nautobot", "LibreNMS"]


def test_old_data_is_stale_and_says_how_old():
    # LibreNMS every minute: stale after the 15-minute floor, not 3 minutes.
    status = metrics.app_status(
        {"nautobot_inventory": row(4), "librenms_inventory": row(14)},
        librenms=True,
        intervals=INTERVALS,
        now=NOW,
    )
    assert status["state"] == "ok"
    status = metrics.app_status(
        {"nautobot_inventory": row(4), "librenms_inventory": row(45)},
        librenms=True,
        intervals=INTERVALS,
        now=NOW,
    )
    assert status["state"] == "stale"
    assert status["sources"][1]["message"] == "LibreNMS data is 45 min old"


def test_a_failing_sync_is_down_and_wins():
    status = metrics.app_status(
        {"nautobot_inventory": row(90), "librenms_inventory": row(2, status="error")},
        librenms=True,
        intervals=INTERVALS,
        now=NOW,
    )
    assert status["state"] == "down"
    assert status["sources"][1]["message"] == "Can't reach LibreNMS"
    assert status["sources"][1]["last_success"] == "2026-10-22T13:58:00Z"


def test_without_librenms_only_nautobot_counts():
    status = metrics.app_status(
        {"nautobot_inventory": row(4)}, librenms=False, intervals=INTERVALS, now=NOW
    )
    assert status["state"] == "ok" and [source["name"] for source in status["sources"]] == [
        "Nautobot"
    ]
    never = metrics.app_status({}, librenms=False, intervals=INTERVALS, now=NOW)
    assert never["state"] == "stale" and never["updated_at"] is None
    assert never["sources"][0]["message"] == "No data from Nautobot yet"


def test_no_database_answer_is_down():
    status = metrics.app_status(None, librenms=True, intervals=INTERVALS, now=NOW)
    assert (
        status["state"] == "down" and status["sources"][0]["message"] == "Can't reach the database"
    )


def test_the_endpoint_never_calls_upstream(flask_client, monkeypatch):
    called = []
    monkeypatch.setattr("app.clients.nautobot.fetch_all_pages", lambda *a, **k: called.append(a))
    monkeypatch.setattr("app.clients.librenms.get", lambda *a, **k: called.append(a))
    monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
    response = flask_client.get("/api/v1/app-status")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["state"] in {"ok", "stale"}
    assert called == []


class TestPageConfig:
    """GET /api/page-config (#463): what the Nuxt pages' top bar shows."""

    def test_the_menu_is_the_server_pages_menu(self, flask_client):
        from app.services import navigation_service as navigation

        config = flask_client.get("/api/v1/page-config").get_json()
        assert config["pages"] == navigation.pages()
        assert [page["key"] for page in config["pages"]] == [key for key, *_ in navigation.PAGES]
        assert flask_client.get("/api/v1/page-config").headers["Cache-Control"] == "no-store"

    def test_no_one_signed_in_without_sign_in(self, flask_client, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_MODE", "disabled")
        assert flask_client.get("/api/v1/page-config").get_json()["user"] is None

    def test_who_is_signed_in(self, flask_client):
        with auth_config(mode="header", viewer_groups={"noc"}, operator_groups={"ops"}):
            config = flask_client.get(
                "/api/v1/page-config",
                headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"},
            ).get_json()
        assert config["user"] == {"name": "olga", "role": "operator", "sign_out_url": ""}

    def test_sign_out_for_someone_signed_in_through_the_proxy(self, flask_client, monkeypatch):
        """#546: AUTH_SIGN_OUT_URL is where the top bar's Sign out goes."""
        monkeypatch.setattr(settings, "AUTH_SIGN_OUT_URL", "/oauth2/sign_out")
        with auth_config(mode="header", viewer_groups={"noc"}):
            config = flask_client.get(
                "/api/v1/page-config",
                headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "noc"},
            ).get_json()
            anonymous = flask_client.get("/api/v1/page-config").get_json()
        assert config["user"]["sign_out_url"] == "/oauth2/sign_out"
        assert anonymous["user"] is None

    def test_no_sign_out_for_a_token_or_without_sign_in(self, monkeypatch):
        """A token has no session to end; with AUTH_MODE=disabled nobody is signed in."""
        from app.services import templating_service as templating

        monkeypatch.setattr(settings, "AUTH_SIGN_OUT_URL", "/oauth2/sign_out")
        monkeypatch.setattr(settings, "AUTH_MODE", "header")
        assert templating.sign_out_url({"username": "olga"}) == "/oauth2/sign_out"
        assert templating.sign_out_url({"username": "token:librenms", "token": "librenms"}) == ""
        monkeypatch.setattr(settings, "AUTH_MODE", "disabled")
        assert templating.sign_out_url({"username": "token:librenms", "token": "librenms"}) == ""

    def test_the_boards_case_links_and_levels(self, flask_client, monkeypatch):
        from app.services import alerts_service as alerts

        monkeypatch.setattr(settings, "CASE_URL_TEMPLATE", "https://itsm.example/t/{case}")
        board = flask_client.get("/api/v1/page-config").get_json()["board"]
        assert board == {
            "case_url_template": "https://itsm.example/t/{case}",
            "levels": alerts.alert_level_definitions(),
        }

    def test_the_maps_tiles_and_first_view(self, flask_client, monkeypatch):
        monkeypatch.setattr(settings, "MAP_TILE_URL", "https://tiles.example/{z}/{x}/{y}.png")
        monkeypatch.setattr(settings, "MAP_TILE_ATTRIBUTION", "Tiles")
        monkeypatch.setattr(settings, "MAP_DEFAULT_VIEW", None)
        assert flask_client.get("/api/v1/page-config").get_json()["map"] == {
            "tile_url": "https://tiles.example/{z}/{x}/{y}.png",
            "tile_attribution": "Tiles",
            "view": None,
        }
        # MAP_DEFAULT_VIEW (#362): the map opens there instead of framing the sites.
        monkeypatch.setattr(settings, "MAP_DEFAULT_VIEW", (55.7, 12.6, 6))
        view = flask_client.get("/api/v1/page-config").get_json()["map"]["view"]
        assert view == {"latitude": 55.7, "longitude": 12.6, "zoom": 6}

    def test_the_scope_is_kept_on_the_map_board_and_wall(self):
        from app.services import navigation_service as navigation

        pages = {page["key"]: page["href"] for page in navigation.pages("tenant=Acme%20Corp&q=DNK")}
        assert pages["map"] == "/?tenant=Acme%20Corp&q=DNK"
        assert pages["wall"] == "/alerts?view=wall&tenant=Acme%20Corp&q=DNK"
        assert pages["circuits"] == "/circuits"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("/oauth2/sign_out", "/oauth2/sign_out"),
        ("https://sso.example.com/logout", "https://sso.example.com/logout"),
        ("//evil.example.com/", ""),
        ("javascript:alert(1)", ""),
        ("", ""),
    ],
)
def test_the_sign_out_url_is_a_path_or_http(value, expected):
    """#546: anything else is ignored with a warning, never put in a link."""
    code = "from app.core.legacy_settings import settings; print(settings.AUTH_SIGN_OUT_URL)"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=pathlib.Path(__file__).resolve().parent.parent,
        env={**os.environ, "AUTH_SIGN_OUT_URL": value},
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == expected
    if value and not expected:
        assert "AUTH_SIGN_OUT_URL ignored" in completed.stderr
