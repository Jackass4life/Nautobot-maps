"""What the command line can do, the API can do too (#529)."""

import threading

import pytest
import requests

from nautobot_maps import __main__ as cli
from nautobot_maps import db, inventory, settings, statuslinks, wireless
from nautobot_maps.api import admin
from tests import http_client
from tests.test_config import _commands
from tests.test_tokens import bearer, clock, make  # noqa: F401 (clock: a fixture)
from tests.test_wireless import DK, SE, ap, controllers  # noqa: F401 (controllers: a fixture)

# Each everyday command, and the API routes that do the same.
API_FOR_COMMAND = {
    "token create": ["admin.api_tokens_create"],
    "token list": ["admin.api_tokens_list"],
    "token revoke": ["admin.api_tokens_revoke"],
    "token purge": ["admin.api_tokens_purge"],
    "status-link": ["admin.api_status_link", "admin.api_status_link_replace"],
    "wireless-check": ["admin.api_wireless_check"],
}
# Installing and developing, not everyday work: no API.
CLI_ONLY = {"migrate", "schema-version", "contract-docs"}


def test_every_everyday_command_has_an_api_route():
    from nautobot_maps import apidocs

    routes = {route["endpoint"] for route in apidocs.routes(None)}
    commands = set(_commands(cli.build_parser()))
    missing = sorted(command for command in commands - CLI_ONLY if command not in API_FOR_COMMAND)
    assert not missing, f"add an API route for these commands (and to API_FOR_COMMAND): {missing}"
    for command, names in API_FOR_COMMAND.items():
        assert command in commands, command
        for name in names:
            assert name in routes, f"{command}: no route {name}"


@pytest.fixture
def client(pg_database, clock):  # noqa: F811
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            inventory.write_tenants(
                conn, [{"tenant_id": "t1", "name": "Acme Corp"}, {"tenant_id": "t2", "name": "Nord A/S"}]
            )
    finally:
        conn.close()
    with http_client.FlaskStyleClient() as test_client:
        yield test_client


def link_of(tenant: str) -> str:
    conn = db.get_conn()
    try:
        return statuslinks.ensure(conn, [tenant])[tenant]
    finally:
        conn.close()


class TestStatusLinks:
    def test_the_link_as_the_command_prints_it(self, client, capsys):
        admin_token = make("admin", "admin")["token"]
        answer = client.get("/api/status-links/acme%20corp", headers=bearer(admin_token))
        assert answer.status_code == 200
        assert answer.get_json() == {"tenant": "Acme Corp", "status_link": f"/status/{link_of('Acme Corp')}"}
        assert cli.main(["status-link", "Acme Corp"]) == 0
        assert capsys.readouterr().out.strip() == answer.get_json()["status_link"]
        # A name with a slash in it.
        nord = client.get("/api/status-links/Nord%20A%2FS", headers=bearer(admin_token)).get_json()
        assert nord == {"tenant": "Nord A/S", "status_link": f"/status/{link_of('Nord A/S')}"}

    def test_only_admins_and_only_known_tenants(self, client):
        admin_token, operator = make("admin", "admin")["token"], make("op", "operator")["token"]
        assert client.get("/api/status-links/Acme%20Corp", headers=bearer(operator)).status_code == 403
        assert client.post("/api/status-links/Acme%20Corp/replace", headers=bearer(operator)).status_code == 403
        for method, path in (("get", "/api/status-links/Acme"), ("post", "/api/status-links/Acme/replace")):
            refused = getattr(client, method)(path, headers=bearer(admin_token))
            assert refused.status_code == 404, path
            assert refused.get_json() == {"error": "no tenant 'Acme' in Nautobot (as of the last sync)"}
        conn = db.get_conn()
        try:
            assert conn.execute("SELECT count(*) AS n FROM status_links").fetchone()["n"] == 0, "a typo makes no link"
        finally:
            conn.close()

    def test_replace_a_leaked_link(self, client):
        admin_token = make("admin", "admin")["token"]
        old = client.get("/api/status-links/Acme%20Corp", headers=bearer(admin_token)).get_json()["status_link"]
        replaced = client.post("/api/status-links/Acme%20Corp/replace", headers=bearer(admin_token)).get_json()
        assert replaced["tenant"] == "Acme Corp" and replaced["status_link"] != old
        assert "old link no longer works" in replaced["message"] and "tokens still work" in replaced["message"]
        assert replaced["status_link"] == f"/status/{link_of('Acme Corp')}"
        assert client.get(f"/api{old}", headers=bearer(admin_token)).status_code == 404

    def test_a_removed_tenants_leaked_link_can_still_be_replaced(self, client):
        admin_token = make("admin", "admin")["token"]
        link_of("Gone Ltd")  # its link was made while Nautobot had it
        replaced = client.post("/api/status-links/Gone%20Ltd/replace", headers=bearer(admin_token))
        assert replaced.status_code == 200 and replaced.get_json()["tenant"] == "Gone Ltd"


class TestWirelessCheck:
    def test_one_result_per_controller_never_the_credentials(self, client, controllers, monkeypatch):  # noqa: F811
        admin_token = make("admin", "admin")["token"]
        monkeypatch.setattr(inventory, "read_devices", lambda: [])
        controllers["dk"] = [ap("dk-ap1", True, "FGL1"), ap("dk-ap2", False, "FGL2")]
        controllers["se"] = requests.ConnectionError("refused by 10.0.0.1")
        answer = client.post("/api/wireless/check", headers=bearer(admin_token))
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
        assert (se["reachable"], se["signed_in"]) == (False, False) and se["error"].startswith("can't connect")
        assert answer.get_json()["duplicates"] == {} and answer.get_json()["duplicate_count"] == 0
        assert DK["password"] not in answer.get_data(as_text=True)
        assert client.post("/api/wireless/check", headers=bearer(make("op", "operator")["token"])).status_code == 403

    @pytest.mark.parametrize(
        ("failure", "reachable", "signed_in", "says"),
        [
            (requests.exceptions.SSLError("bad cert"), False, False, "certificate isn't trusted"),
            (requests.exceptions.ConnectTimeout("slow"), False, False, "no answer in time"),
            ("401", True, False, "the account was refused (HTTP 401)"),
            ("500", True, None, "answered HTTP 500"),
            (RuntimeError("Catalyst Center answered the sign-in without a token"), True, None, "without a token"),
        ],
    )
    def test_why_in_plain_words(self, failure, reachable, signed_in, says):
        if isinstance(failure, str):
            response = requests.Response()
            response.status_code = int(failure)
            failure = requests.HTTPError(f"{failure} Client Error", response=response)
        result = wireless.check_failure(failure)
        assert (result["reachable"], result["signed_in"]) == (reachable, signed_in)
        assert says in result["error"]

    def test_one_check_at_a_time(self, client, controllers, monkeypatch):  # noqa: F811
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
            target=lambda: first.setdefault("status", client.post("/api/wireless/check", headers=bearer(admin_token)))
        )
        running.start()
        assert started.wait(5)
        busy = client.post("/api/wireless/check", headers=bearer(admin_token))
        assert busy.status_code == 429 and "running already" in busy.get_json()["error"]
        assert admin._wireless_check_running.locked()
        finish.set()
        running.join(5)
        assert first["status"].status_code == 200

    def test_another_workers_check_is_running(self, client, monkeypatch):
        admin_token = make("admin", "admin")["token"]
        monkeypatch.setattr(settings, "WIRELESS_CONTROLLERS", (DK, SE))
        release = db.try_advisory_lock(admin.WIRELESS_CHECK_LOCK)
        try:
            assert client.post("/api/wireless/check", headers=bearer(admin_token)).status_code == 429
        finally:
            release()
