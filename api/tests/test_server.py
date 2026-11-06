"""The server the image runs (#613): gunicorn with uvicorn workers, the access
log, the logging setup, the startup log, and the current app's /api/<path>
answering 308 to /api/v1/<path>. The current app's tests (tests/test_asgi.py,
tests/test_app.py's TestLogging and startup log tests), on the new stack."""

import importlib
import json
import logging
import sys
from unittest.mock import patch

import pytest

from app import server, startup
from app.utils import logs
from app.utils.legacy_settings import settings

ACCESS = "app.access"


def test_one_access_log_line_with_the_same_fields(asgi_client, caplog):
    with caplog.at_level(logging.INFO, logger=ACCESS):
        asgi_client.get(
            "/api/v1/app-status?x=1",
            headers={settings.AUTH_HEADER_USER: "olga", "User-Agent": "probe/1"},
        )
    lines = [record for record in caplog.records if record.name == ACCESS]
    assert len(lines) == 1
    record = lines[0]
    assert record.args["r"] == "GET /api/v1/app-status?x=1 HTTP/1.1" and record.args["s"] == 200
    assert record.args["user"] == "olga" and record.args["a"] == "probe/1" and record.args["b"] > 0
    message = record.getMessage()
    assert message.startswith('testclient "olga" "GET /api/v1/app-status?x=1 HTTP/1.1" 200 ')
    assert message.endswith('ms "probe/1"')


def test_successful_health_checks_are_still_left_out(asgi_client, caplog):
    with caplog.at_level(logging.INFO, logger=ACCESS):
        asgi_client.get("/healthz")
    (record,) = [record for record in caplog.records if record.name == ACCESS]
    assert not logs.SkipSuccessfulHealthChecks().filter(record)


def test_uvicorn_never_takes_the_client_from_a_header():
    """The app trusts the sign-in headers by the client's address (#187): uvicorn
    must not replace it with X-Forwarded-For, nor name itself."""
    assert server.UVICORN_OPTIONS["proxy_headers"] is False
    assert server.Worker.CONFIG_KWARGS["proxy_headers"] is False
    assert server.Worker.CONFIG_KWARGS["server_header"] is False
    assert server.Worker.CONFIG_KWARGS["access_log"] is False


def _record(**kwargs) -> logging.LogRecord:
    record = logging.LogRecord("app.web", logging.ERROR, __file__, 1, "boom %s", ("x",), None)
    record.__dict__.update(kwargs)
    return record


def test_json_lines_include_the_stack_trace():
    try:
        raise KeyError("site")
    except KeyError:
        record = _record(exc_info=sys.exc_info())
    entry = json.loads(logs.JsonFormatter().format(record))
    assert entry["level"] == "ERROR" and entry["logger"] == "app.web"
    assert entry["message"] == "boom x" and "KeyError: 'site'" in entry["exception"]


def test_successful_health_checks_are_not_access_logged():
    skip = logs.SkipSuccessfulHealthChecks()
    assert not skip.filter(_record(args={"U": "/healthz", "s": "200"}))
    assert skip.filter(_record(args={"U": "/healthz", "s": "503"}))
    assert skip.filter(_record(args={"U": "/api/v1/alerts", "s": "200"}))


def test_gunicorn_config_logs_access_and_keeps_a_root_logger(monkeypatch):
    monkeypatch.setenv("GUNICORN_WORKERS", "")
    monkeypatch.setenv("GUNICORN_TIMEOUT", "30")
    import gunicorn_config

    config = importlib.reload(gunicorn_config)
    # The app writes the access log itself under uvicorn (#436).
    assert config.worker_class == "app.server.Worker" and config.accesslog is None
    assert ACCESS in config.logconfig_dict["loggers"]
    # The current app's defaults: port 5000, 4 workers, never under 120 s.
    assert (config.bind, config.workers, config.timeout) == ("0.0.0.0:5000", 4, 120)
    # gunicorn refuses to start if the root logger names a missing handler.
    root_handlers = config.logconfig_dict["root"]["handlers"]
    assert set(root_handlers) <= set(config.logconfig_dict["handlers"])


class TestStartupLog:
    def test_it_reports_the_resolved_sets(self):
        with (
            patch.object(
                settings, "ALERT_BOARD_EXCLUDED_LOCATION_STATUSES", {"staging", "decommissioning"}
            ),
            patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_NAMES", set()),
            patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_TYPES", {"warehouse"}),
            patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_TAGS", {"non-operational"}),
            patch.object(settings, "ALERT_BOARD_EXCLUDED_DEVICE_STATUSES", {"planned", "null"}),
            patch.object(startup.logger, "info") as info,
        ):
            startup.log_alert_board_exclusions()
        info.assert_called_once_with(
            "Alert board exclusions — statuses=%s, names=%s, types=%s, tags=%s, "
            "device statuses=%s; rows=%s",
            "{decommissioning,staging}",
            "{}",
            "{warehouse}",
            "{non-operational}",
            "{null,planned}",
            "every location",
        )

    def test_a_missing_ca_bundle(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "LIBRENMS_VERIFY_SSL", "/nonexistent/ca.pem")
        with caplog.at_level("ERROR"):
            startup.log_alert_board_exclusions()
        assert "LIBRENMS_VERIFY_SSL='/nonexistent/ca.pem'" in caplog.text

    @pytest.mark.parametrize(
        ("secret", "says"),
        [("s3cr3t-value", "with the proxy secret"), ("", "no AUTH_PROXY_SECRET")],
    )
    def test_header_auth_names_the_proxies_never_the_secret(
        self, monkeypatch, caplog, secret, says
    ):
        monkeypatch.setattr(settings, "AUTH_MODE", "header")
        monkeypatch.setattr(settings, "AUTH_PROXY_SECRET", secret)
        with caplog.at_level("INFO", logger="app"):
            startup.log_alert_board_exclusions()
        assert "identity headers trusted from 127.0.0.1/32" in caplog.text and says in caplog.text
        assert "s3cr3t" not in caplog.text

    def test_a_leftover_sqlite_setting(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "LEGACY_SQLITE_DB", "/app/data/nautobot_maps.db")
        with caplog.at_level("ERROR", logger="app"):
            startup.log_alert_board_exclusions()
        assert "SQLite support was removed" in caplog.text
        assert "NAUTOBOT_MAPS_DATABASE_URL" in caplog.text


class TestTheCurrentAppsPaths:
    """#613: /api/<path> answers 308 to /api/v1/<path>, method and body kept."""

    def test_a_moved_route(self, asgi_client):
        moved = asgi_client.get("/api/app-status?x=1&y=%20", follow_redirects=False)
        assert moved.status_code == 308
        assert moved.headers["location"] == "/api/v1/app-status?x=1&y=%20"
        assert asgi_client.get("/api/app-status").status_code == 200  # followed

    def test_a_webhook_keeps_its_method_and_body(self, asgi_client):
        moved = asgi_client.post("/api/alertmanager", json={"alerts": []}, follow_redirects=False)
        assert moved.status_code == 308
        assert moved.headers["location"] == "/api/v1/alertmanager"

    def test_a_path_with_a_parameter(self, asgi_client):
        moved = asgi_client.get("/api/status/abc%20def", follow_redirects=False)
        assert moved.status_code == 308
        assert moved.headers["location"] == "/api/v1/status/abc%20def"

    @pytest.mark.parametrize("path", ["/api/openapi.json", "/api/healthz"])
    def test_routes_of_their_own_stay(self, asgi_client, path):
        assert asgi_client.get(path, follow_redirects=False).status_code == 200

    def test_no_route_is_still_404(self, asgi_client):
        missing = asgi_client.get("/api/nope", follow_redirects=False)
        assert missing.status_code == 404 and missing.json() == {"error": "Not found"}


class TestGunicornConfig:
    """The current app's gunicorn settings (tests/test_app.py's TestAuthConfiguration)."""

    @staticmethod
    def _reload():
        import gunicorn_config

        return importlib.reload(gunicorn_config)

    @pytest.mark.parametrize("mode", ["header", "disabled"])
    def test_it_listens_on_every_interface(self, monkeypatch, mode):
        """In a container 127.0.0.1 is unreachable; trust is checked per request (#187)."""
        monkeypatch.setenv("AUTH_MODE", mode)
        monkeypatch.delenv("GUNICORN_BIND", raising=False)
        assert self._reload().bind == "0.0.0.0:5000"

    def test_when_ready_logs_the_board_exclusions(self):
        with patch.object(startup, "log_alert_board_exclusions") as log_exclusions:
            self._reload().when_ready(None)
        log_exclusions.assert_called_once_with()

    @pytest.mark.parametrize(("value", "timeout"), [(None, 120), ("30", 120), ("180", 180)])
    def test_the_timeout_is_never_under_120_seconds(self, monkeypatch, value, timeout):
        if value is None:
            monkeypatch.delenv("GUNICORN_TIMEOUT", raising=False)
        else:
            monkeypatch.setenv("GUNICORN_TIMEOUT", value)
        assert self._reload().timeout == timeout
