"""What the command line can do, the API can do too (#529)."""

import threading

import httpx
import pytest

from app import __main__ as cli
from app.core.legacy_settings import settings
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
    "status-link": ["status.api_status_link_create", "status.api_status_link_replace"],
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
    conn = adb.connect()
    try:
        with conn.transaction():
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


def links() -> int:
    conn = adb.connect()
    try:
        return conn.execute("SELECT count(*) AS n FROM status_links").fetchone()["n"]
    finally:
        conn.close()


class TestStatusLinks:
    def test_the_link_as_the_command_prints_it(self, flask_client, capsys):
        """POST makes it (#687), as ``status-link TENANT``; asking again answers the same."""
        operator = make("op", "operator")["token"]
        answer = flask_client.post("/api/v1/status-links/acme%20corp", headers=bearer(operator))
        assert answer.status_code == 200
        assert answer.get_json() == {
            "tenant": "Acme Corp",
            "status_link": f"/status/{link_of('Acme Corp')}",
        }
        again = flask_client.post("/api/v1/status-links/Acme%20Corp", headers=bearer(operator))
        assert again.get_json() == answer.get_json()
        assert cli.main(["status-link", "Acme Corp"]) == 0
        assert capsys.readouterr().out.strip() == answer.get_json()["status_link"]
        # Read back, and a name with a slash in it.
        read = flask_client.get("/api/v1/status-links/Acme%20Corp", headers=bearer(operator))
        assert read.get_json() == answer.get_json()
        nord = flask_client.post(
            "/api/v1/status-links/Nord%20A%2FS", headers=bearer(operator)
        ).get_json()
        assert nord == {"tenant": "Nord A/S", "status_link": f"/status/{link_of('Nord A/S')}"}

    def test_reading_never_makes_a_link(self, flask_client):
        operator = make("op", "operator")["token"]
        missing = flask_client.get("/api/v1/status-links/Acme%20Corp", headers=bearer(operator))
        assert missing.status_code == 404
        assert missing.get_json() == {"error": "No status link for tenant 'Acme Corp' yet"}
        assert flask_client.get("/api/v1/status-links", headers=bearer(operator)).get_json() == {
            "links": []
        }
        assert links() == 0

    def test_nwo_and_admins_only_and_only_known_tenants(self, flask_client):
        """NWO (operator) and admin keep the links (#687); a viewer may not even read
        them; an unknown tenant gets no link, whoever asks."""
        viewer, operator = make("v", "viewer")["token"], make("op", "operator")["token"]
        paths = (
            ("get", "/api/v1/status-links"),
            ("get", "/api/v1/status-links/Acme%20Corp"),
            ("post", "/api/v1/status-links/Acme%20Corp"),
            ("post", "/api/v1/status-links/Acme%20Corp/replace"),
            ("delete", "/api/v1/status-links/Acme%20Corp"),
        )
        for method, path in paths:
            refused = getattr(flask_client, method)(path, headers=bearer(viewer))
            assert refused.status_code == 403, path
            assert refused.get_json()["required_permission"].startswith("status.links."), path
        for method, path in paths[1:]:
            unknown = getattr(flask_client, method)(
                path.replace("Acme%20Corp", "Acme"), headers=bearer(operator)
            )
            assert unknown.status_code == 404, path
            assert unknown.get_json() == {
                "error": "no tenant 'Acme' in Nautobot (as of the last sync)"
            }
        assert links() == 0, "a typo makes no link"

    def test_the_list(self, flask_client):
        operator = make("op", "operator")["token"]
        made = flask_client.post("/api/v1/status-links/Acme%20Corp", headers=bearer(operator))
        listed = flask_client.get("/api/v1/status-links", headers=bearer(operator)).get_json()
        (link,) = listed["links"]
        assert (link["tenant"], link["status_link"]) == (
            "Acme Corp",
            made.get_json()["status_link"],
        )
        assert link["created_at"].endswith("Z")

    def test_delete_cuts_the_link_off_at_once(self, flask_client):
        operator = make("op", "operator")["token"]
        old = flask_client.post(
            "/api/v1/status-links/Acme%20Corp", headers=bearer(operator)
        ).get_json()["status_link"]
        # Its page opens (no board here: 503, "try again"), never the 404 of no page.
        assert flask_client.get(f"/api/v1{old}", headers=bearer(operator)).status_code != 404
        deleted = flask_client.delete("/api/v1/status-links/Acme%20Corp", headers=bearer(operator))
        assert deleted.status_code == 200 and deleted.get_json()["tenant"] == "Acme Corp"
        assert "no longer works" in deleted.get_json()["message"]
        assert flask_client.get(f"/api/v1{old}", headers=bearer(operator)).status_code == 404
        assert links() == 0
        again = flask_client.delete("/api/v1/status-links/Acme%20Corp", headers=bearer(operator))
        assert again.status_code == 404
        # A new one is another link.
        new = flask_client.post(
            "/api/v1/status-links/Acme%20Corp", headers=bearer(operator)
        ).get_json()["status_link"]
        assert new != old

    def test_replace_a_leaked_link(self, flask_client):
        admin_token = make("admin", "admin")["token"]
        old = flask_client.post(
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
        monkeypatch.setattr(inventory, "read_devices", adb.async_of(lambda: []))
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
        monkeypatch.setattr(inventory, "read_devices", adb.async_of(lambda: []))
        started, finish = threading.Event(), threading.Event()

        def slow(devices):
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
        release = adb.hold_lock(admin.WIRELESS_CHECK_LOCK)
        try:
            assert (
                flask_client.post("/api/v1/wireless/check", headers=bearer(admin_token)).status_code
                == 429
            )
        finally:
            release()


class TestStatusTokensForNwo:
    """#687 (a): NWO sends customers a status link with its key, a status token; every
    other token stays an admin's."""

    def test_nwo_makes_lists_and_revokes_status_tokens_only(self, flask_client):
        operator = make("op", "operator")["token"]
        admins = make("boss", "admin")
        made = flask_client.post(
            "/api/v1/tokens",
            json={"name": "acme-key", "role": "status", "tenant": "Acme Corp"},
            headers=bearer(operator),
        )
        assert made.status_code == 201
        token = made.get_json()["token"]
        assert (
            token["role"] == "status" and token["status_link"] == f"/status/{link_of('Acme Corp')}"
        )
        # Not another role.
        for role in ("viewer", "operator", "admin"):
            refused = flask_client.post(
                "/api/v1/tokens", json={"name": f"x-{role}", "role": role}, headers=bearer(operator)
            )
            assert refused.status_code == 403, role
            assert refused.get_json()["required_permission"] == "admin"
        # The list: status tokens only.
        listed = flask_client.get("/api/v1/tokens", headers=bearer(operator)).get_json()["tokens"]
        assert {t["role"] for t in listed} == {"status"} and {t["name"] for t in listed} == {
            "acme-key"
        }
        # Revoke a status token, not an admin's.
        boss = flask_client.post(f"/api/v1/tokens/{admins['id']}/revoke", headers=bearer(operator))
        assert boss.status_code == 403
        revoked = flask_client.post(
            f"/api/v1/tokens/{token['id']}/revoke", headers=bearer(operator)
        )
        assert revoked.status_code == 200 and revoked.get_json()["token"]["state"] == "revoked"
        assert (
            flask_client.post("/api/v1/tokens/99999/revoke", headers=bearer(operator)).status_code
            == 404
        )
        # Purge stays an admin's.
        assert (
            flask_client.post("/api/v1/tokens/purge", json={}, headers=bearer(operator)).status_code
            == 403
        )

    def test_a_viewer_makes_no_token_and_an_admin_every_one(self, flask_client):
        viewer, admin_token = make("v", "viewer")["token"], make("boss", "admin")["token"]
        body = {"name": "acme-key", "role": "status", "tenant": "Acme Corp"}
        assert (
            flask_client.post("/api/v1/tokens", json=body, headers=bearer(viewer)).status_code
            == 403
        )
        assert flask_client.get("/api/v1/tokens", headers=bearer(viewer)).status_code == 403
        made = flask_client.post(
            "/api/v1/tokens", json={"name": "ops", "role": "operator"}, headers=bearer(admin_token)
        )
        assert made.status_code == 201
        roles = {
            t["role"]
            for t in flask_client.get("/api/v1/tokens", headers=bearer(admin_token)).get_json()[
                "tokens"
            ]
        }
        assert roles == {"admin", "operator", "viewer"}
