"""What the API can do, the web pages can do too (#530): the Admin page,
what page-config tells it, and the tenants for a status token."""

import pytest

from app.services import inventory_service as inventory
from tests import db as adb
from tests.test_app import auth_config
from tests.test_tokens import bearer, clock, make  # noqa: F401 (fixtures)
from tests.test_wireless import DK, SE

HEADERS = {"X-Forwarded-User": "olga"}


def page_admin(client, **headers):
    return client.get("/api/v1/page-config", headers=headers).json()["admin"]


class TestWhoSeesIt:
    def test_only_admins_see_the_administration(self, client, override_settings):
        override_settings(
            NOTIFY_WEBHOOK_URL="https://hooks.example.com/x", WIRELESS_CONTROLLERS=(DK, SE)
        )
        with auth_config(
            mode="header", viewer_groups={"noc"}, operator_groups={"ops"}, admin_groups={"admins"}
        ):
            for groups in ("noc", "ops"):
                answer = client.get(
                    "/api/v1/page-config",
                    headers={**HEADERS, "X-Forwarded-Groups": groups},
                ).json()
                assert answer["admin"] is None, groups
            admin = client.get(
                "/api/v1/page-config",
                headers={**HEADERS, "X-Forwarded-Groups": "admins"},
            ).json()["admin"]
            assert admin == {"notification_channels": ["webhook"], "wireless_controllers": 2}
            # A token counts as its role.
            assert page_admin(client, **bearer(make("a", "admin")["token"])) is not None
            assert page_admin(client, **bearer(make("o", "operator")["token"])) is None

    @pytest.mark.parametrize("writes", [False, True])
    def test_without_sign_in_only_with_unauthenticated_writes(
        self, client, writes, override_settings
    ):
        override_settings(ALLOW_UNAUTHENTICATED_WRITES=writes)
        assert (page_admin(client) is not None) is writes
        # As the routes it would use.
        assert (client.post("/api/v1/tokens/purge", json={}).status_code == 200) is writes


def test_the_tenants_for_a_status_token(client):
    adb.call(
        inventory.write_tenants,
        [
            {"tenant_id": "t2", "name": "nordic net", "description": ""},
            {"tenant_id": "t1", "name": "Acme Corp", "description": "Retail bank"},
        ],
    )
    admin = make("admin", "admin")["token"]
    answer = client.get("/api/v1/tenants", headers=bearer(admin))
    assert answer.json() == {
        "tenants": [
            {"name": "Acme Corp", "description": "Retail bank"},
            {"name": "nordic net", "description": ""},
        ]
    }
    # The Customers page's list too (#697): for those who see a tenant's status or
    # links; not for a status token (one tenant only).
    operator = client.get("/api/v1/tenants", headers=bearer(make("op", "operator")["token"]))
    assert operator.json() == answer.json()
    status = make("st", "status", tenant="Acme Corp")["token"]
    assert client.get("/api/v1/tenants", headers=bearer(status)).status_code == 403


# Left out: the current app's server details (Flask's bind address, its access-log
# format, templates/ and /static/, HTML error pages for browsers); the pages and
# their error page come in step 8 of #587, access logging at the switchover.
#   test_the_admin_page_is_the_nuxt_page
