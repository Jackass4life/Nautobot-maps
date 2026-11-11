"""Logging through loguru, every line naming its part (#633)."""

import ast
import json
import logging
import string
from pathlib import Path

import pytest
from loguru import logger

from app.core.legacy_settings import settings
from app.logs import parts

APP = Path(__file__).resolve().parent.parent / "app"
LEVELS = {"trace", "debug", "info", "success", "warning", "error", "exception", "critical"}


def _logging_modules() -> list[str]:
    modules = []
    for path in sorted(APP.rglob("*.py")):
        if "from loguru import logger" in path.read_text(encoding="utf-8"):
            relative = path.relative_to(APP.parent).with_suffix("")
            modules.append(".".join(relative.parts).removesuffix(".__init__"))
    return modules


def _logger_calls():
    for path in sorted(APP.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in LEVELS
                and "logger" in ast.unparse(node.func.value)
            ):
                yield path.relative_to(APP), node


@pytest.mark.parametrize(
    ("name", "part"),
    [
        ("app.services.scheduler", "scheduler"),
        ("app.services.inventory_service", "sync"),
        ("app.clients.nautobot", "sync"),
        ("app.v1.incoming_alerts.controller", "alerts-in"),
        ("app.services.webhooks_service", "alerts-in"),
        ("app.services.notify_service", "notify"),
        ("app.services.token_service", "auth"),
        ("app.services.alerts_service", "board"),
        ("app.migrate", "db"),
        ("alembic.runtime.migration", "db"),
        ("app.v1.map.controller", "api"),
        ("uvicorn.error", "server"),
        ("gunicorn.error", "server"),
        ("py.warnings", "server"),
    ],
)
def test_each_logger_has_its_part(name, part):
    assert parts.of(name) == part


@pytest.mark.parametrize(
    ("path", "part"),
    [
        ("/api/v1/alerts", "api"),
        ("/healthz", "api"),
        ("/mcp", "api"),
        ("/metrics", "api"),
        ("/", "web"),
        ("/alerts", "web"),
        ("/_nuxt/entry.js", "web"),
        ("/apiary", "web"),
    ],
)
def test_the_access_log_splits_api_and_web(path, part):
    assert parts.of_request(path) == part


def test_every_part_is_named():
    assert set(parts.PARTS.values()) | {parts.DEFAULT, "web"} == set(parts.NAMES)


def test_every_module_that_logs_has_a_part_of_its_own():
    """Not just the catch-all ``app``: a new module that logs gets a line in PARTS
    (or sits under a prefix that has one)."""

    def key(name: str) -> str:
        while name not in parts.PARTS:
            name = name.rpartition(".")[0]
        return name

    loose = [name for name in _logging_modules() if key(name) == "app"]
    assert not loose, f"add to app/logs/parts.py PARTS: {loose}"


def test_no_percent_placeholders_left():
    """loguru formats with {}: a leftover %s would be printed as it is."""
    left = [
        f"{path}:{node.lineno}"
        for path, node in _logger_calls()
        if len(node.args) > 1
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and any(spec in node.args[0].value for spec in ("%s", "%r", "%d"))
    ]
    assert not left, left


def test_every_message_has_as_many_placeholders_as_arguments():
    """One too few arguments for a {} raises when the line is logged."""
    wrong = []
    for path, node in _logger_calls():
        if len(node.args) < 2 or not isinstance(node.args[0], ast.Constant):
            continue
        fields = [field for _, field, _, _ in string.Formatter().parse(node.args[0].value)]
        if len([field for field in fields if field is not None]) != len(node.args) - 1:
            wrong.append(f"{path}:{node.lineno}")
    assert not wrong, wrong


class TestTheLines:
    def test_text_names_the_part_and_the_logger(self, capsys, configure_logs):
        configure_logs()
        logger.bind(logger="app.services.scheduler").warning("tick failed: {}", "boom")
        logging.getLogger("uvicorn.error").warning("worker died")
        err = capsys.readouterr().err.splitlines()
        assert err[0].endswith(" WARNING [scheduler] app.services.scheduler: tick failed: boom")
        assert err[1].endswith(" WARNING [server] uvicorn.error: worker died")
        # Milliseconds, no time zone: as before, with a dot.
        assert err[0][19] == "." and err[0][23] == " "

    def test_upstream_requests_are_logged_only_at_debug(self, monkeypatch, capsys, configure_logs):
        """httpx writes a line per request at INFO (#645): not at the default level."""
        configure_logs()
        logging.getLogger("httpx").info('HTTP Request: GET https://nautobot/api/ "HTTP/1.1 200 OK"')
        logging.getLogger("httpx").warning("pool closed")
        err = capsys.readouterr().err
        assert "HTTP Request" not in err and " WARNING [sync] httpx: pool closed" in err
        monkeypatch.setattr(settings, "LOG_LEVEL", "DEBUG")
        configure_logs()
        logging.getLogger("httpx").info("HTTP Request: GET https://nautobot/api/")
        assert " INFO [sync] httpx: HTTP Request" in capsys.readouterr().err

    def test_a_modules_own_lines_are_named_after_it(self, monkeypatch, capsys, configure_logs):
        from app import startup

        monkeypatch.setattr(settings, "LEGACY_SQLITE_DB", "/app/data/nautobot_maps.db")
        configure_logs()
        startup.log_alert_board_exclusions()
        assert " ERROR [server] app.startup: NAUTOBOT_MAPS_DB is set" in capsys.readouterr().err

    def test_json_carries_the_part(self, monkeypatch, capsys, configure_logs):
        monkeypatch.setattr(settings, "LOG_FORMAT", "json")
        configure_logs()
        logging.getLogger("alembic.runtime.migration").info("Running upgrade")
        entry = json.loads(capsys.readouterr().err.strip())
        assert set(entry) == {"time", "level", "logger", "part", "message"}
        assert (entry["logger"], entry["part"], entry["message"]) == (
            "alembic.runtime.migration",
            "db",
            "Running upgrade",
        )

    def test_the_access_log_goes_to_stdout_even_at_warning(
        self, monkeypatch, capsys, configure_logs, asgi_client
    ):
        monkeypatch.setattr(settings, "LOG_LEVEL", "WARNING")
        configure_logs()
        asgi_client.get("/alerts")
        asgi_client.get("/api/v1/app-status")
        logger.bind(logger="app.services.scheduler").info("not at WARNING")
        captured = capsys.readouterr()
        lines = captured.out.splitlines()
        assert any("[web] app.access: " in line and '"GET /alerts ' in line for line in lines)
        assert any("[api] app.access: " in line and "/api/v1/app-status" in line for line in lines)
        assert "app.access" not in captured.err and "not at WARNING" not in captured.err

    def test_successful_health_checks_stay_out(self, capsys, configure_logs, asgi_client):
        configure_logs()
        asgi_client.get("/healthz")
        assert "/healthz" not in capsys.readouterr().out
