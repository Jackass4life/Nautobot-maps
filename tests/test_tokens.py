"""API tokens (#297): Bearer sign-in for scripts, LibreNMS and MCP clients."""

import logging
import threading

import pytest

import app as flask_app
from nautobot_maps import __main__ as cli
from nautobot_maps import auth, caching, db, settings, timeutil, tokens
from tests.test_app import auth_config
from tests.test_mcp import call

NOW = "2026-10-09T12:00:00Z"
CASE = {"site_id": "loc-1", "device_ids": ["d1"], "case_number": "INC-1"}


@pytest.fixture
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(timeutil, "iso_utc_now", lambda: state["now"])
    return state


@pytest.fixture
def client(pg_database, clock):
    flask_app.app.config["TESTING"] = True
    caching.cache.clear()
    with flask_app.app.test_client() as test_client:
        yield test_client


def make(name="script", role="operator", **extra) -> dict:
    conn = db.get_conn()
    try:
        return tokens.create(conn, {"name": name, "role": role, **extra}, "admin-user")
    finally:
        conn.close()


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def raw_rows() -> list[dict]:
    conn = db.get_conn()
    try:
        return [db.row_to_dict(row) for row in conn.execute("SELECT * FROM api_tokens ORDER BY id").fetchall()]
    finally:
        conn.close()


class TestTokens:
    def test_only_a_hash_is_stored(self, pg_database, clock):
        created = make()
        secret = created["token"]
        assert secret.startswith("nmt_") and len(secret) == 47
        assert (
            created["prefix"] == secret[:12] and created["state"] == "active" and created["created_by"] == "admin-user"
        )
        (row,) = raw_rows()
        assert secret not in {str(value) for value in row.values()}
        assert row["token_hash"] == tokens.hash_token(secret)
        conn = db.get_conn()
        try:
            listed = tokens.list_tokens(conn)
        finally:
            conn.close()
        assert "token" not in listed[0] and "token_hash" not in listed[0]

    @pytest.mark.parametrize(
        "body, message",
        [
            ({"role": "operator"}, "name is required"),
            ({"name": "-x", "role": "operator"}, "name: up to"),
            ({"name": "x" * 101, "role": "operator"}, "name: up to"),
            ({"name": "ok", "role": "root"}, "role must be one of"),
            ({"name": "ok", "role": "viewer", "expires_in_days": 0}, "expires_in_days"),
            ({"name": "ok", "role": "viewer", "expires_in_days": True}, "expires_in_days"),
            ({"name": "ok", "role": "viewer", "expires_in_days": 3651}, "expires_in_days"),
        ],
    )
    def test_validation(self, pg_database, clock, body, message):
        conn = db.get_conn()
        try:
            with pytest.raises(tokens.TokenError) as raised:
                tokens.create(conn, body, "")
        finally:
            conn.close()
        assert raised.value.message.startswith(message)

    def test_names_are_unique_also_after_revoking(self, pg_database, clock):
        first = make("librenms")
        conn = db.get_conn()
        try:
            tokens.revoke(conn, first["id"], "admin-user")
            with pytest.raises(tokens.TokenError, match="already exists"):
                tokens.create(conn, {"name": "librenms", "role": "viewer"}, "")
        finally:
            conn.close()

    def test_two_creates_with_the_same_name_at_once(self, pg_database, clock):
        """The second create waits on the first's uncommitted row, then is a TokenError, not a database error."""
        outcome = {}

        def second():
            try:
                outcome["token"] = make("race")
            except Exception as exc:  # noqa: BLE001 - the test inspects what it was
                outcome["error"] = exc

        first = db.get_conn()
        try:
            with db.transaction(first):
                first.execute(
                    "INSERT INTO api_tokens (name, token_hash, prefix, role) VALUES ('race', 'h', 'nmt_x', 'viewer')"
                )
                thread = threading.Thread(target=second)
                thread.start()
                thread.join(timeout=1)
                assert thread.is_alive(), "the second create waits for the first to commit"
        finally:
            first.close()
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert isinstance(outcome.get("error"), tokens.TokenError), outcome
        assert "already exists" in outcome["error"].message

    def test_verify_expiry_revocation_and_last_used(self, pg_database, clock):
        secret = make(expires_in_days=1)["token"]
        assert tokens.verify(secret) == {"name": "script", "role": "operator"}
        assert raw_rows()[0]["last_used_at"] is not None
        first_use = raw_rows()[0]["last_used_at"]
        clock["now"] = "2026-10-09T12:04:00Z"
        tokens.verify(secret)
        assert raw_rows()[0]["last_used_at"] == first_use, "written at most every 5 minutes"
        clock["now"] = "2026-10-09T12:06:00Z"
        tokens.verify(secret)
        assert raw_rows()[0]["last_used_at"] != first_use
        assert tokens.verify(secret + "x") is None and tokens.verify("nmt_") is None and tokens.verify("abc") is None
        clock["now"] = "2026-10-10T12:00:01Z"
        assert tokens.verify(secret) is None
        conn = db.get_conn()
        try:
            assert tokens.list_tokens(conn)[0]["state"] == "expired"
        finally:
            conn.close()

    def test_requests_that_all_saw_it_stale_write_once(self, pg_database, clock, monkeypatch):
        secret = make()["token"]
        real_get_conn = db.get_conn

        class OtherRequestFirst:
            """Another request writes last_used_at between our read and our write."""

            def __init__(self, conn):
                self.conn = conn

            def __getattr__(self, name):
                return getattr(self.conn, name)

            def execute(self, sql, params=()):
                if sql.startswith("UPDATE api_tokens SET last_used_at"):
                    other = real_get_conn()
                    try:
                        with db.transaction(other):
                            other.execute("UPDATE api_tokens SET last_used_at = '2026-10-09T11:58:00Z'")
                    finally:
                        other.close()
                return self.conn.execute(sql, params)

        monkeypatch.setattr(db, "get_conn", lambda *args, **kwargs: OtherRequestFirst(real_get_conn()))
        assert tokens.verify(secret) is not None
        monkeypatch.setattr(db, "get_conn", real_get_conn)
        assert raw_rows()[0]["last_used_at"].startswith("2026-10-09T11:58:00")

    def test_revoke(self, pg_database, clock):
        created = make()
        conn = db.get_conn()
        try:
            revoked = tokens.revoke(conn, created["id"], "admin-user")
            assert revoked["state"] == "revoked" and revoked["revoked_by"] == "admin-user"
            again = tokens.revoke(conn, created["id"], "someone-else")
            assert again["revoked_by"] == "admin-user", "revoking twice changes nothing"
            assert tokens.revoke(conn, 999, "x") is None
        finally:
            conn.close()
        assert tokens.verify(created["token"]) is None

    def test_the_secret_is_never_logged(self, pg_database, clock, caplog):
        caplog.set_level(logging.DEBUG)
        secret = make()["token"]
        tokens.verify(secret)
        assert "script" in caplog.text and secret not in caplog.text


class TestSignIn:
    def test_disabled_mode_operator_token_may_write(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", False)
        body = {"site_id": "loc-lon", "reason": "Upgrade", "duration_minutes": 30}
        assert client.post("/api/maintenance", json=body).status_code == 403
        operator = make()["token"]
        created = client.post("/api/maintenance", json=body, headers=bearer(operator))
        assert created.status_code == 201
        assert created.get_json()["windows"][0]["created_by"] == "token:script"
        viewer = make("reader", "viewer")["token"]
        refused = client.post("/api/maintenance", json=body, headers=bearer(viewer))
        assert refused.status_code == 403 and refused.get_json()["current_role"] == "viewer"

    def test_header_mode_token_from_any_address_wins_over_headers(self, client):
        operator = make()["token"]
        with auth_config(mode="header", viewer_groups={"noc"}, operator_groups={"ops"}):
            # Not from a trusted proxy: identity headers are ignored, the token is not.
            untrusted = {"REMOTE_ADDR": "203.0.113.9"}
            body = {"site_id": "loc-lon", "reason": "Upgrade", "duration_minutes": 30}
            response = client.post("/api/maintenance", json=body, headers=bearer(operator), environ_base=untrusted)
            assert response.status_code == 201
            response = client.post(
                "/api/maintenance",
                json=body,
                headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"},
                environ_base=untrusted,
            )
            assert response.status_code == 401

    def test_rejected_tokens_get_401_everywhere(self, client, clock):
        created = make()
        expired = make("old", expires_in_days=1)["token"]
        conn = db.get_conn()
        try:
            tokens.revoke(conn, created["id"], "admin-user")
        finally:
            conn.close()
        clock["now"] = "2026-10-11T00:00:00Z"
        for token in (created["token"], expired, "nmt_wrong"):
            response = client.get("/api/maintenance", headers=bearer(token))
            assert response.status_code == 401, token
            assert response.get_json() == {"error": "Invalid, expired or revoked API token"}
        assert client.get("/healthz", headers=bearer("nmt_wrong")).status_code in (200, 503)

    def test_a_bearer_value_that_is_not_ours_is_ignored(self, client):
        with auth_config(mode="header", operator_groups={"ops"}):
            headers = {
                "Authorization": "Bearer eyJhbGciOi.proxy.token",
                "X-Forwarded-User": "olga",
                "X-Forwarded-Groups": "ops",
            }
            body = {"site_id": "loc-lon", "reason": "Upgrade", "duration_minutes": 30}
            assert client.post("/api/maintenance", json=body, headers=headers).status_code == 201

    def test_require_viewer_accepts_a_token(self, client, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
        viewer = make("reader", "viewer")["token"]
        with auth_config(mode="header"):
            assert client.get("/api/maintenance", environ_base={"REMOTE_ADDR": "203.0.113.9"}).status_code == 401
            assert client.get("/api/maintenance", headers=bearer(viewer)).status_code == 200

    def test_database_error_is_a_refusal_without_the_token_in_the_log(self, client, monkeypatch, caplog):
        def broken(token):
            raise RuntimeError(f"connection failed {token}")

        monkeypatch.setattr(tokens, "verify", broken)
        response = client.get("/api/maintenance", headers=bearer("nmt_secret123"))
        assert response.status_code == 401
        assert "nmt_secret123" not in caplog.text and "RuntimeError" in caplog.text

    def test_mcp_with_a_token(self, client, monkeypatch):
        monkeypatch.setattr(settings, "MCP_ENABLED", True)
        operator = make()["token"]
        viewer = make("reader", "viewer")["token"]
        with auth_config(mode="header"):
            history = call(client, "get_alert_history", {}, headers=bearer(operator))
            assert not history["isError"], history
            refused = call(client, "get_alert_history", {}, headers=bearer(viewer))
            assert refused["isError"] and "operator" in refused["content"][0]["text"]


class TestApi:
    def test_admin_creates_lists_and_revokes(self, client):
        admin = make("bootstrap", "admin")["token"]
        operator = make()["token"]
        assert (
            client.post("/api/tokens", json={"name": "x", "role": "viewer"}, headers=bearer(operator)).status_code
            == 403
        )
        response = client.post(
            "/api/tokens", json={"name": "librenms", "role": "operator", "expires_in_days": 30}, headers=bearer(admin)
        )
        assert response.status_code == 201 and response.headers["Cache-Control"] == "no-store"
        created = response.get_json()["token"]
        assert created["created_by"] == "token:bootstrap" and created["token"].startswith("nmt_")
        assert client.get("/api/maintenance", headers=bearer(created["token"])).status_code == 200
        listed = client.get("/api/tokens", headers=bearer(admin)).get_json()["tokens"]
        assert [t["name"] for t in listed] == ["librenms", "script", "bootstrap"]
        assert all("token" not in t and "token_hash" not in t for t in listed)
        revoked = client.post(f"/api/tokens/{created['id']}/revoke", headers=bearer(admin))
        assert revoked.status_code == 200 and revoked.get_json()["token"]["state"] == "revoked"
        assert client.get("/api/maintenance", headers=bearer(created["token"])).status_code == 401
        assert client.post("/api/tokens/999/revoke", headers=bearer(admin)).status_code == 404
        bad = client.post("/api/tokens", json={"name": "librenms", "role": "operator"}, headers=bearer(admin))
        assert bad.status_code == 400 and "already exists" in bad.get_json()["error"]
        assert client.post("/api/tokens", data="x", headers=bearer(admin)).status_code == 400

    def test_disabled_mode_needs_allow_unauthenticated_writes_to_create(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", False)
        assert client.post("/api/tokens", json={"name": "x", "role": "viewer"}).status_code == 403


class TestCli:
    def test_create_list_revoke(self, pg_database, clock, capsys):
        assert cli.main(["token", "create", "librenms", "--role", "operator", "--expires-days", "30"]) == 0
        out, err = capsys.readouterr()
        secret = out.strip()
        assert secret.startswith("nmt_") and "Shown only now" in err and secret not in err
        assert tokens.verify(secret) == {"name": "librenms", "role": "operator"}
        assert raw_rows()[0]["created_by"] == "cli"
        assert cli.main(["token", "create", "librenms", "--role", "viewer"]) == 1
        assert "already exists" in capsys.readouterr().err
        assert cli.main(["token", "list"]) == 0
        listing = capsys.readouterr().out
        assert "librenms" in listing and secret[:12] in listing and secret not in listing
        token_id = raw_rows()[0]["id"]
        assert cli.main(["token", "revoke", str(token_id)]) == 0
        assert "is revoked" in capsys.readouterr().out
        assert cli.main(["token", "revoke", "999"]) == 1

    def test_needs_a_database(self, monkeypatch, capsys):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        assert cli.main(["token", "list"]) == 1
        assert cli.main(["schema-version"]) == 0


def test_bearer_parsing():
    with flask_app.app.test_request_context(headers={"Authorization": "bearer  nmt_abc "}):
        assert auth.bearer_token() == "nmt_abc"
    for header in ("Basic nmt_abc", "Bearer other", "Bearer", "nmt_abc"):
        with flask_app.app.test_request_context(headers={"Authorization": header}):
            assert auth.bearer_token() == "", header
