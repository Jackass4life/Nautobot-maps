"""API tokens (#297, #592): Bearer sign-in for scripts, LibreNMS and MCP clients.

Ported from the current app's tests/test_tokens.py; its protected route there
(/api/maintenance) comes later, so these use the token routes themselves
(admin, closed with AUTH_MODE=disabled) and the items example (operator, open
with AUTH_MODE=disabled like the board's own changes).
"""

import logging
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import app
from app.services import auth_service, token_service
from app.utils import timeutil
from tests import db as adb
from tests.conftest import TEST_DATABASE_URL

# Rows written and read beside the app's own session (#664: on asyncpg).
engine = adb.SyncEngine(TEST_DATABASE_URL)

NOW = "2026-10-09T12:00:00Z"
UNTRUSTED = ("203.0.113.9", 40000)


@pytest.fixture
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(timeutil, "iso_utc_now", lambda: state["now"])
    return state


def make(name="script", role="operator", **extra) -> dict:
    return adb.run(token_service.create, {"name": name, "role": role, **extra}, "admin-user")


def revoke(token_id: int, by: str = "admin-user") -> dict | None:
    return adb.run(token_service.revoke, token_id, by)


def verify(token: str) -> dict | None:
    return adb.run(token_service.verify, token)


def listed(**kwargs) -> list[dict]:
    return adb.run(token_service.list_tokens, **kwargs)


def acme_in_nautobot() -> None:
    """A tenant the last inventory sync saw (a status token needs one, #528)."""
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO nautobot_tenant_cache (tenant_id, name) VALUES ('t1', 'Acme')")
        )


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def raw_rows() -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM api_tokens ORDER BY id")).mappings()
        return [timeutil.row(row) for row in rows]


def item(client, name="x", **kwargs):
    return client.post("/api/v1/items", json={"name": name}, **kwargs)


class TestTokens:
    def test_only_a_hash_is_stored(self, clock):
        created = make()
        secret = created["token"]
        assert secret.startswith("nmt_") and len(secret) == 47
        assert created["prefix"] == secret[:12] and created["state"] == "active"
        assert created["created_by"] == "admin-user" and created["created_at"] == NOW
        (row,) = raw_rows()
        assert secret not in {str(value) for value in row.values()}
        assert row["token_hash"] == token_service.hash_token(secret)
        assert "token" not in listed()[0] and "token_hash" not in listed()[0]

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ({"role": "operator"}, "name is required"),
            ({"name": "-x", "role": "operator"}, "name: up to"),
            ({"name": "x" * 101, "role": "operator"}, "name: up to"),
            ({"name": "ok", "role": "root"}, "role must be one of"),
            ({"name": "ok", "role": "viewer", "expires_in_days": 0}, "expires_in_days"),
            ({"name": "ok", "role": "viewer", "expires_in_days": True}, "expires_in_days"),
            ({"name": "ok", "role": "viewer", "expires_in_days": 3651}, "expires_in_days"),
            ({"name": "ok", "role": "viewer", "tenant": "Acme"}, "tenant is only for role status"),
            ({"name": "ok", "role": "status"}, "tenant is required for role status"),
            ({"name": "ok", "role": "status", "tenant": "Acme"}, "no tenant 'Acme' in Nautobot"),
        ],
    )
    def test_validation(self, clock, body, message):
        with pytest.raises(token_service.TokenError) as raised:
            adb.run(token_service.create, body, "")
        assert raised.value.message.startswith(message)

    def test_names_are_unique_also_after_revoking(self, clock):
        revoke(make("librenms")["id"])
        with pytest.raises(token_service.TokenError, match="already exists"):
            make("librenms", "viewer")

    def test_the_name_taken_says_how_to_see_it(self, clock):
        make("old")
        with pytest.raises(token_service.TokenError, match="token list --all"):
            make("old")

    def test_two_creates_with_the_same_name_at_once(self, clock):
        """The second create waits on the first's uncommitted row, then is a
        TokenError, not a database error."""
        outcome = {}

        def second():
            try:
                outcome["token"] = make("race")
            except Exception as exc:  # noqa: BLE001 - the test inspects what it was
                outcome["error"] = exc

        with engine.connect() as first:
            first.execute(
                text(
                    "INSERT INTO api_tokens (name, token_hash, prefix, role) "
                    "VALUES ('race', 'h', 'nmt_x', 'viewer')"
                )
            )
            thread = threading.Thread(target=second)
            thread.start()
            thread.join(timeout=1)
            assert thread.is_alive(), "the second create waits for the first to commit"
            first.commit()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert isinstance(outcome.get("error"), token_service.TokenError), outcome
        assert "already exists" in outcome["error"].message

    def test_verify_expiry_revocation_and_last_used(self, clock):
        secret = make(expires_in_days=1)["token"]
        assert verify(secret) == {"name": "script", "role": "operator", "tenant": ""}
        first_use = raw_rows()[0]["last_used_at"]
        assert first_use == NOW
        clock["now"] = "2026-10-09T12:04:00Z"
        verify(secret)
        assert raw_rows()[0]["last_used_at"] == first_use, "written at most every 5 minutes"
        clock["now"] = "2026-10-09T12:06:00Z"
        verify(secret)
        assert raw_rows()[0]["last_used_at"] == "2026-10-09T12:06:00Z"
        assert verify(secret + "x") is None and verify("nmt_") is None and verify("abc") is None
        clock["now"] = "2026-10-10T12:00:01Z"
        assert verify(secret) is None
        assert listed()[0]["state"] == "expired"

    def test_requests_that_all_saw_it_stale_write_once(self, clock, monkeypatch):
        """Another request writes last_used_at between our read and our write."""
        secret = make()["token"]
        real_touch = token_service.token_repository.touch

        async def other_request_first(session, *args):
            with engine.begin() as other:
                other.execute(text("UPDATE api_tokens SET last_used_at = '2026-10-09T11:58:00Z'"))
            await real_touch(session, *args)

        monkeypatch.setattr(token_service.token_repository, "touch", other_request_first)
        assert verify(secret) is not None
        assert raw_rows()[0]["last_used_at"] == "2026-10-09T11:58:00Z"

    def test_revoke(self, clock):
        created = make()
        revoked = revoke(created["id"])
        assert revoked["state"] == "revoked" and revoked["revoked_by"] == "admin-user"
        again = revoke(created["id"], "someone-else")
        assert again["revoked_by"] == "admin-user", "revoking twice changes nothing"
        assert revoke(999, "x") is None
        assert verify(created["token"]) is None

    def test_the_secret_is_never_logged(self, clock, caplog):
        caplog.set_level(logging.DEBUG)
        secret = make()["token"]
        verify(secret)
        assert "script" in caplog.text and secret not in caplog.text


class TestSignIn:
    def test_disabled_mode_admin_token_may_administer(self, client, clock):
        body = {"name": "librenms", "role": "operator"}
        assert client.post("/api/v1/tokens", json=body).status_code == 403
        admin = make("bootstrap", "admin")["token"]
        created = client.post("/api/v1/tokens", json=body, headers=bearer(admin))
        assert created.status_code == 201
        assert created.json()["token"]["created_by"] == "token:bootstrap"
        viewer = make("reader", "viewer")["token"]
        refused = client.post(
            "/api/v1/tokens", json={"name": "y", "role": "viewer"}, headers=bearer(viewer)
        )
        assert refused.status_code == 403 and refused.json()["current_role"] == "viewer"

    def test_header_mode_token_from_any_address_wins_over_headers(self, configure, clock):
        operator = make()["token"]
        configure(AUTH_MODE="header", AUTH_OPERATOR_GROUPS="ops")
        with TestClient(app, client=UNTRUSTED) as untrusted:
            # Not from a trusted proxy: identity headers are ignored, the token is not.
            assert item(untrusted, "a", headers=bearer(operator)).status_code == 201
            headers = {"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"}
            assert item(untrusted, "b", headers=headers).status_code == 401
            me = untrusted.get("/api/v1/me", headers={**headers, **bearer(operator)}).json()
            assert me["username"] == "token:script" and me["role"] == "operator"

    def test_rejected_tokens_get_401_everywhere(self, client, clock):
        created = make()
        expired = make("old", expires_in_days=1)["token"]
        revoke(created["id"])
        clock["now"] = "2026-10-11T00:00:00Z"
        for token in (created["token"], expired, "nmt_wrong"):
            for response in (
                client.get("/api/v1/items", headers=bearer(token)),
                client.get("/api/v1/me", headers=bearer(token)),
            ):
                assert response.status_code == 401, token
                assert response.json() == {"error": "Invalid, expired or revoked API token"}
        assert client.get("/api/healthz", headers=bearer("nmt_wrong")).status_code == 200

    def test_a_bearer_value_that_is_not_ours_is_ignored(self, configure, clock):
        configure(AUTH_MODE="header", AUTH_OPERATOR_GROUPS="ops")
        headers = {
            "Authorization": "Bearer eyJhbGciOi.proxy.token",
            "X-Forwarded-User": "olga",
            "X-Forwarded-Groups": "ops",
        }
        with TestClient(app, client=("127.0.0.1", 1)) as proxied:
            assert item(proxied, headers=headers).status_code == 201

    def test_require_viewer_accepts_a_token(self, configure, clock):
        viewer = make("reader", "viewer")["token"]
        configure(AUTH_MODE="header", AUTH_REQUIRE_VIEWER="true")
        with TestClient(app, client=UNTRUSTED) as untrusted:
            assert untrusted.get("/api/v1/items").status_code == 401
            assert untrusted.get("/api/v1/items", headers=bearer(viewer)).status_code == 200

    def test_database_error_is_a_refusal_without_the_token_in_the_log(
        self, client, monkeypatch, caplog
    ):
        async def broken(session, token):
            raise RuntimeError(f"connection failed {token}")

        monkeypatch.setattr(token_service, "verify", broken)
        response = client.get("/api/v1/items", headers=bearer("nmt_secret123"))
        assert response.status_code == 401
        assert "nmt_secret123" not in caplog.text and "RuntimeError" in caplog.text

    def test_a_status_token_opens_only_its_status_page(self, client, clock):
        acme_in_nautobot()
        status = make("acme-page", "status", tenant="acme")
        assert status["tenant"] == "Acme", "kept as Nautobot spells it"
        response = client.get("/api/v1/items", headers=bearer(status["token"]))
        assert response.status_code == 403
        assert response.json() == {"error": "This token only opens its tenant's status page"}
        user = auth_service.CurrentUser(
            True, "token:acme-page", [], "status", "disabled", "acme-page", "Acme"
        )
        from app.core.settings import get_settings

        assert auth_service.status_access(user, get_settings(), "ACME ") is None
        assert auth_service.status_access(user, get_settings(), "Globex") == (
            {"error": "This token is for another tenant"},
            403,
        )


class TestApi:
    def test_admin_creates_lists_and_revokes(self, client, clock):
        admin = make("bootstrap", "admin")["token"]
        operator = make()["token"]
        refused = client.post(
            "/api/v1/tokens", json={"name": "x", "role": "viewer"}, headers=bearer(operator)
        )
        assert refused.status_code == 403
        response = client.post(
            "/api/v1/tokens",
            json={"name": "librenms", "role": "operator", "expires_in_days": 30},
            headers=bearer(admin),
        )
        assert response.status_code == 201
        assert response.headers["Cache-Control"] == "no-store, no-transform"
        created = response.json()["token"]
        assert created["created_by"] == "token:bootstrap" and created["token"].startswith("nmt_")
        assert created["expires_at"] == "2026-11-08T12:00:00Z" and created["status_link"] is None
        assert client.get("/api/v1/items", headers=bearer(created["token"])).status_code == 200
        names = [
            t["name"] for t in client.get("/api/v1/tokens", headers=bearer(admin)).json()["tokens"]
        ]
        assert names == ["librenms", "script", "bootstrap"]
        revoked = client.post(f"/api/v1/tokens/{created['id']}/revoke", headers=bearer(admin))
        assert revoked.status_code == 200 and revoked.json()["token"]["state"] == "revoked"
        assert client.get("/api/v1/items", headers=bearer(created["token"])).status_code == 401
        missing = client.post("/api/v1/tokens/999/revoke", headers=bearer(admin))
        assert missing.status_code == 404 and missing.json() == {"error": "No such token"}
        bad = client.post(
            "/api/v1/tokens", json={"name": "librenms", "role": "operator"}, headers=bearer(admin)
        )
        assert bad.status_code == 400 and "already exists" in bad.json()["error"]
        not_json = client.post("/api/v1/tokens", content="x", headers=bearer(admin))
        assert not_json.status_code == 400 and not_json.json() == {
            "error": "Expected a JSON object"
        }

    def test_a_status_token_answers_its_status_link(self, client, clock):
        acme_in_nautobot()
        admin = make("bootstrap", "admin")["token"]
        body = {"name": "acme", "role": "status", "tenant": "ACME"}
        created = client.post("/api/v1/tokens", json=body, headers=bearer(admin)).json()["token"]
        assert created["tenant"] == "Acme" and created["status_link"].startswith("/status/")
        with engine.connect() as conn:
            link_id = conn.execute(
                text("SELECT link_id FROM status_links WHERE tenant_key = 'acme'")
            ).scalar()
        assert created["status_link"] == f"/status/{link_id}" and len(link_id) == 24
        tenant_only = client.get("/api/v1/tokens?tenant=acme", headers=bearer(admin)).json()[
            "tokens"
        ]
        assert [t["name"] for t in tenant_only] == ["acme"]


class TestStatusLinkFailure:
    def test_a_failing_link_keeps_the_token(self, client, clock, monkeypatch, caplog):
        """A database error making the link (#592 review): a savepoint, so the token's
        insert stands and its secret is shown, without the link."""
        from app.services import status_link_service

        async def broken(session, tenants):
            await session.execute(text("INSERT INTO status_links (tenant_key) VALUES (NULL)"))

        acme_in_nautobot()
        admin = make("bootstrap", "admin")["token"]
        monkeypatch.setattr(status_link_service, "ensure", broken)
        body = {"name": "acme", "role": "status", "tenant": "Acme"}
        response = client.post("/api/v1/tokens", json=body, headers=bearer(admin))
        assert response.status_code == 201
        created = response.json()["token"]
        assert created["status_link"] is None and created["token"].startswith("nmt_")
        assert "status link could not be read: IntegrityError" in caplog.text
        assert [row["name"] for row in raw_rows()] == ["bootstrap", "acme"]
        assert verify(created["token"])["tenant"] == "Acme"


class TestListAndPurge:
    """#528: the list shows the tokens that work; purge deletes the rest for good."""

    @pytest.fixture
    def mixed(self, clock):
        """Active, revoked 100+ days ago, expired 10 days ago, revoked now."""
        clock["now"] = "2026-06-01T12:00:00Z"
        old = make("old-revoked")
        expiring = make("expired", expires_in_days=120)  # expires 2026-09-29
        revoke(old["id"])
        clock["now"] = NOW
        fresh = make("fresh-revoked")
        revoke(fresh["id"])
        active = make("active")
        return {"active": active, "old": old, "expired": expiring, "fresh": fresh}

    def test_the_api_lists_the_active_ones(self, client, mixed):
        admin = make("admin", "admin")["token"]
        body = client.get("/api/v1/tokens", headers=bearer(admin)).json()
        assert [t["name"] for t in body["tokens"]] == ["admin", "active"] and body["inactive"] == 3
        everything = client.get("/api/v1/tokens?all=true", headers=bearer(admin)).json()
        assert len(everything["tokens"]) == 5 and everything["inactive"] == 3

    def test_purge_never_an_active_one(self, mixed, caplog):
        caplog.set_level(logging.INFO, logger="app.services.token_service")
        assert len(adb.run(token_service.purge, "cli", dry_run=True)) == 3
        assert len(raw_rows()) == 4
        assert len(adb.run(token_service.purge, "cli", older_than_days=30)) == 1
        assert sorted(row["name"] for row in raw_rows()) == ["active", "expired", "fresh-revoked"]
        assert "API token 'old-revoked' purged by cli" in caplog.text
        adb.run(token_service.purge, "cli")
        assert [row["name"] for row in raw_rows()] == ["active"]
        assert verify(mixed["active"]["token"])
        assert make("old-revoked")["name"] == "old-revoked", "the names are free again"
        with pytest.raises(token_service.TokenError, match="whole number"):
            adb.run(token_service.purge, "cli", older_than_days=-1)

    def test_purge_through_the_api(self, client, mixed):
        admin = make("admin", "admin")["token"]
        operator = make("op")["token"]
        assert (
            client.post("/api/v1/tokens/purge", json={}, headers=bearer(operator)).status_code
            == 403
        )
        dry = client.post(
            "/api/v1/tokens/purge", json={"dry_run": True}, headers=bearer(admin)
        ).json()
        assert dry["dry_run"] and dry["purged"] == 0
        assert [t["name"] for t in dry["tokens"]] == ["fresh-revoked", "expired", "old-revoked"]
        assert len(raw_rows()) == 6
        done = client.post(
            "/api/v1/tokens/purge", json={"older_than_days": 30}, headers=bearer(admin)
        ).json()
        assert done["purged"] == 1 and done["tokens"][0]["name"] == "old-revoked"
        assert client.post("/api/v1/tokens/purge", headers=bearer(admin)).json()["purged"] == 2
        for body in ({"older_than_days": "30"}, {"older_than_days": -1}, {"dry_run": "yes"}, [1]):
            response = client.post("/api/v1/tokens/purge", json=body, headers=bearer(admin))
            assert response.status_code == 400, body


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("bearer  nmt_abc ", "nmt_abc"),
        ("Basic nmt_abc", ""),
        ("Bearer other", ""),
        ("Bearer", ""),
        ("nmt_abc", ""),
    ],
)
def test_bearer_parsing(header, expected):
    from starlette.requests import Request

    request = Request({"type": "http", "headers": [(b"authorization", header.encode())]})
    assert auth_service.bearer_token(request) == expected


# --- Cross-site changes (the current app's tests/test_cross_site.py) ------------------------


@pytest.fixture
def writes_allowed(configure):
    # AUTH_MODE=disabled with writes open: only the cross-site check stands in the way.
    configure(ALLOW_UNAUTHENTICATED_WRITES="true")


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
    ],
)
def test_a_bodyless_change_from_another_site_is_refused(client, writes_allowed, clock, headers):
    """A plain cross-site form could send these."""
    response = client.post("/api/v1/tokens/1/revoke", headers=headers)
    assert response.status_code == 403
    assert response.json() == {"error": "Changes from another site are refused"}


def test_the_apps_own_pages_and_scripts_still_can(client, writes_allowed, clock):
    for headers in (
        {"Sec-Fetch-Site": "same-origin"},
        {"Origin": "http://testserver"},
        # A proxy may pass the Host without the browser's port.
        {"Origin": "http://testserver:8443"},
        # curl, scripts, webhooks: no browser headers.
        {},
    ):
        response = client.post("/api/v1/tokens/1/revoke", headers=headers)
        assert response.status_code == 404, headers


def test_api_tokens_are_not_affected(client, clock):
    # A page can't add an Authorization header without CORS consent: a token is not a cookie.
    admin = make("bootstrap", "admin")["token"]
    response = client.post(
        "/api/v1/tokens/1/revoke", headers={"Sec-Fetch-Site": "cross-site", **bearer(admin)}
    )
    assert response.status_code == 200
