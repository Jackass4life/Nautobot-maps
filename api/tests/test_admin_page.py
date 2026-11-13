"""What the API can do, the web pages can do too (#530): the Admin page,
what page-config tells it, and the tenants for a status token."""

import pytest

from app.core.legacy_settings import settings
from app.services import inventory_service as inventory
from tests import db as adb
from tests.test_app import auth_config
from tests.test_tokens import bearer, clock, make  # noqa: F401 (fixtures)
from tests.test_wireless import DK, SE

HEADERS = {"X-Forwarded-User": "olga"}


def page_admin(flask_client, **headers):
    return flask_client.get("/api/v1/page-config", headers=headers).get_json()["admin"]


class TestWhoSeesIt:
    def test_only_admins_see_the_administration(self, flask_client, monkeypatch):
        monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "https://hooks.example.com/x")
        monkeypatch.setattr(settings, "WIRELESS_CONTROLLERS", (DK, SE))
        with auth_config(
            mode="header", viewer_groups={"noc"}, operator_groups={"ops"}, admin_groups={"admins"}
        ):
            environ = {"REMOTE_ADDR": "127.0.0.1"}
            for groups in ("noc", "ops"):
                answer = flask_client.get(
                    "/api/v1/page-config",
                    headers={**HEADERS, "X-Forwarded-Groups": groups},
                    environ_base=environ,
                ).get_json()
                assert answer["admin"] is None, groups
            admin = flask_client.get(
                "/api/v1/page-config",
                headers={**HEADERS, "X-Forwarded-Groups": "admins"},
                environ_base=environ,
            ).get_json()["admin"]
            assert admin == {"notification_channels": ["webhook"], "wireless_controllers": 2}
            # A token counts as its role.
            assert page_admin(flask_client, **bearer(make("a", "admin")["token"])) is not None
            assert page_admin(flask_client, **bearer(make("o", "operator")["token"])) is None

    @pytest.mark.parametrize("writes", [False, True])
    def test_without_sign_in_only_with_unauthenticated_writes(
        self, flask_client, monkeypatch, writes
    ):
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", writes)
        assert (page_admin(flask_client) is not None) is writes
        # As the routes it would use.
        assert (flask_client.post("/api/v1/tokens/purge", json={}).status_code == 200) is writes


def test_the_tenants_for_a_status_token(flask_client):
    conn = adb.connect()
    try:
        with conn.transaction():
            adb.call(
                inventory.write_tenants,
                [
                    {"tenant_id": "t2", "name": "nordic net", "description": ""},
                    {"tenant_id": "t1", "name": "Acme Corp", "description": "Retail bank"},
                ],
            )
    finally:
        conn.close()
    admin = make("admin", "admin")["token"]
    answer = flask_client.get("/api/v1/tenants", headers=bearer(admin))
    assert answer.get_json() == {
        "tenants": [
            {"name": "Acme Corp", "description": "Retail bank"},
            {"name": "nordic net", "description": ""},
        ]
    }
    # The Customers page's list too (#697): for those who see a tenant's status or
    # links; not for a status token (one tenant only).
    operator = flask_client.get("/api/v1/tenants", headers=bearer(make("op", "operator")["token"]))
    assert operator.get_json() == answer.get_json()
    status = make("st", "status", tenant="Acme Corp")["token"]
    assert flask_client.get("/api/v1/tenants", headers=bearer(status)).status_code == 403


# Left out: the current app's server details (Flask's bind address, its access-log
# format, templates/ and /static/, HTML error pages for browsers); the pages and
# their error page come in step 8 of #587, access logging at the switchover.
#   test_the_admin_page_is_the_nuxt_page
