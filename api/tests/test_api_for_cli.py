"""What the command line can do, the API can do too (#529)."""

import threading

import httpx
import pytest

from app import __main__ as cli
from app.core.legacy_settings import settings
from app.db import connection as db
from app.services import inventory_service as inventory
from app.services import status_link_service as statuslinks
from app.services import wireless_service as wireless
from app.v1.administration import controller as admin
from tests import db as adb
from tests import http_client, upstream
from tests.test_cli import _commands
from tests.test_tokens import bearer, clock, make  # noqa: F401 (clock: a fixture)
from tests.test_wireless import DK, SE, ap, controllers  # noqa: F401 (controllers: a fixture)

# Each everyday command, and the API routes that do the same.
API_FOR_COMMAND = {
    # The new stack's own token routes (#592).
    "token create": ["create_token"],
    "token list": ["list_tokens"],
    "token revoke": ["revoke_token"],
    "token purge": ["purge_tokens"],
    "status-link": ["admin.api_status_link", "admin.api_status_link_replace"],
    "wireless-check": ["admin.api_wireless_check"],
}
# Installing and developing, not everyday work: no API.
CLI_ONLY = {"migrate", "schema-version", "contract-docs"}


def test_every_everyday_command_has_an_api_route():
    from app.services import apidocs_service as apidocs

    routes = {route["endpoint"] for route in apidocs.routes(None)}
    commands = set(_commands(cli.build_parser()))
    missing = sorted(command for command in commands - CLI_ONLY if command not in API_FOR_COMMAND)
    assert not missing, f"add an API route for these commands (and to API_FOR_COMMAND): {missing}"
    for command, names in API_FOR_COMMAND.items():
        assert command in commands, command
        for name in names:
            assert name in routes, f"{command}: no route {name}"


@pytest.fixture
def flask_client(pg_database, clock):
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            adb.call(
                inventory.write_tenants,
                [{"tenant_id": "t1", "name": "Acme Corp"}, {"tenant_id": "t2", "name": "Nord A/S"}],
            )
    finally:
        conn.close()
    with http_client.FlaskStyleClient() as test_client:
        yield test_client


def link_of(tenant: str) -> str:
    return adb.run(statuslinks.ensure, [tenant])[tenant]


class TestStatusLinks:
    def test_the_link_as_the_command_prints_it(self, flask_client, capsys):
        admin_token = make("admin", "admin")["token"]
        answer = flask_client.get("/api/v1/status-links/acme%20corp", headers=bearer(admin_token))
        assert answer.status_code == 200
        assert answer.get_json() == {
            "tenant": "Acme Corp",
            "status_link": f"/status/{link_of('Acme Corp')}",
        }
        assert cli.main(["status-link", "Acme Corp"]) == 0
        assert capsys.readouterr().out.strip() == answer.get_json()["status_link"]
        # A name with a slash in it.
        nord = flask_client.get(
            "/api/v1/status-links/Nord%20A%2FS", headers=bearer(admin_token)
        ).get_json()
        assert nord == {"tenant": "Nord A/S", "status_link": f"/status/{link_of('Nord A/S')}"}

    def test_only_admins_and_only_known_tenants(self, flask_client):
        admin_token, operator = make("admin", "admin")["token"], make("op", "operator")["token"]
        assert (
            flask_client.get(
                "/api/v1/status-links/Acme%20Corp", headers=bearer(operator)
            ).status_code
            == 403
        )
        assert (
            flask_client.post(
                "/api/v1/status-links/Acme%20Corp/replace", headers=bearer(operator)
            ).status_code
            == 403
        )
        for method, path in (
            ("get", "/api/v1/status-links/Acme"),
            ("post", "/api/v1/status-links/Acme/replace"),
        ):
            refused = getattr(flask_client, method)(path, headers=bearer(admin_token))
            assert refused.status_code == 404, path
            assert refused.get_json() == {
                "error": "no tenant 'Acme' in Nautobot (as of the last sync)"
            }
        conn = db.get_conn()
        try:
            assert conn.execute("SELECT count(*) AS n FROM status_links").fetchone()["n"] == 0, (
                "a typo makes no link"
            )
        finally:
            conn.close()

    def test_replace_a_leaked_link(self, flask_client):
        admin_token = make("admin", "admin")["token"]
        old = flask_client.get(
            "/api/v1/status-links/Acme%20Corp", headers=bearer(admin_token)
        ).get_json()["status_link"]
        replaced = flask_client.post(
            "/api/v1/status-links/Acme%20Corp/replace", headers=bearer(admin_token)
        ).get_json()
        assert replaced["tenant"] == "Acme Corp" and replaced["status_link"] != old
        assert (
            "old link no longer works" in replaced["message"]
            and "tokens still work" in replaced["message"]
        )
        assert replaced["status_link"] == f"/status/{link_of('Acme Corp')}"
        assert flask_client.get(f"/api/v1{old}", headers=bearer(admin_token)).status_code == 404

    def test_a_removed_tenants_leaked_link_can_still_be_replaced(self, flask_client):
        admin_token = make("admin", "admin")["token"]
        link_of("Gone Ltd")  # its link was made while Nautobot had it
        replaced = flask_client.post(
            "/api/v1/status-links/Gone%20Ltd/replace", headers=bearer(admin_token)
        )
        assert replaced.status_code == 200 and replaced.get_json()["tenant"] == "Gone Ltd"


class TestWirelessCheck:
    def test_one_result_per_controller_never_the_credentials(
        self, flask_client, controllers, monkeypatch
    ):
        admin_token = make("admin", "admin")["token"]
        monkeypatch.setattr(inventory, "read_devices", lambda: [])
        controllers["dk"] = [ap("dk-ap1", True, "FGL1"), ap("dk-ap2", False, "FGL2")]
        controllers["se"] = httpx.ConnectError("refused by 10.0.0.1")
        answer = flask_client.post("/api/v1/wireless/check", headers=bearer(admin_token))
        assert answer.status_code == 200
        dk, se = answer.get_json()["controllers"]
        assert dk == {
            "controller": "Catalyst Center dk",
            "kind": "catalyst_center",
            "url": DK["url"],
            "reachable": True,
            "signed_in": True,
            "access_points": 2,
            "down": 1,
            "matched": 0,
            "unmatched_examples": ["dk-ap1", "dk-ap2"],
        }
        assert (se["reachable"], se["signed_in"]) == (False, False) and se["error"].startswith(
            "can't connect"
        )
        assert answer.get_json()["duplicates"] == {} and answer.get_json()["duplicate_count"] == 0
        assert DK["password"] not in answer.get_data(as_text=True)
        assert (
            flask_client.post(
                "/api/v1/wireless/check", headers=bearer(make("op", "operator")["token"])
            ).status_code
            == 403
        )

    @pytest.mark.parametrize(
        ("failure", "reachable", "signed_in", "says"),
        [
            (upstream.certificate_error(), False, False, "certificate isn't trusted"),
            (httpx.ConnectTimeout("slow"), False, False, "no answer in time"),
            ("401", True, False, "the account was refused (HTTP 401)"),
            ("500", True, None, "answered HTTP 500"),
            (
                RuntimeError("Catalyst Center answered the sign-in without a token"),
                True,
                None,
                "without a token",
            ),
        ],
    )
    def test_why_in_plain_words(self, failure, reachable, signed_in, says):
        if isinstance(failure, str):
            failure = upstream.status_error(int(failure))
        result = wireless.check_failure(failure)
        assert (result["reachable"], result["signed_in"]) == (reachable, signed_in)
        assert says in result["error"]

    def test_one_check_at_a_time(self, flask_client, controllers, monkeypatch):
        admin_token = make("admin", "admin")["token"]
        monkeypatch.setattr(inventory, "read_devices", lambda: [])
        started, finish = threading.Event(), threading.Event()

        def slow():
            started.set()
            finish.wait(5)
            return []

        monkeypatch.setattr(wireless, "check", slow)
        first = {}
        running = threading.Thread(
            target=lambda: first.setdefault(
                "status", flask_client.post("/api/v1/wireless/check", headers=bearer(admin_token))
            )
        )
        running.start()
        assert started.wait(5)
        busy = flask_client.post("/api/v1/wireless/check", headers=bearer(admin_token))
        assert busy.status_code == 429 and "running already" in busy.get_json()["error"]
        assert admin._wireless_check_running.locked()
        finish.set()
        running.join(5)
        assert first["status"].status_code == 200

    def test_another_workers_check_is_running(self, flask_client, monkeypatch):
        admin_token = make("admin", "admin")["token"]
        monkeypatch.setattr(settings, "WIRELESS_CONTROLLERS", (DK, SE))
        release = db.try_advisory_lock(admin.WIRELESS_CHECK_LOCK)
        try:
            assert (
                flask_client.post("/api/v1/wireless/check", headers=bearer(admin_token)).status_code
                == 429
            )
        finally:
            release()
