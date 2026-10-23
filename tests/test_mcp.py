"""The MCP server at /mcp (#250)."""

import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app as flask_app
from nautobot_maps import alerts, caching, mcp, settings
from nautobot_maps.repositories import board as board_store
from tests import http_client
from tests.test_app import auth_config

MODERN = "2026-07-28"

BOARD = {
    "checked_at": "2026-10-05T10:00:00Z",
    "stale": False,
    "summary": {"critical": 1, "medium": 0, "low": 1, "no_data": 0, "ok": 1, "total": 3, "non_ok": 2},
    "alerts": [
        {
            "id": "loc-1",
            "name": "Aarhus HQ",
            "ancestor_path": "EMEA › DNK",
            "status": "Active",
            "location_type": "Site",
            "tenants": ["Acme"],
            "alert_level": "critical",
            "alert_reason": "Core device(s) offline: core01",
            "device_count": 10,
            "down_device_count": 2,
            "current_downtime_seconds": 600,
            "latest_down_at": "2026-10-05T09:50:00Z",
            "active_cases": [],
            "down_devices": [
                {
                    "device_id": "d1",
                    "device_name": "core01",
                    "device_ip": "10.0.0.1",
                    "role": "Core",
                    "case_numbers": [],
                },
                {"device_id": "d2", "device_name": "acc01", "device_ip": "", "role": "Access", "case_numbers": []},
            ],
        },
        {"id": "loc-2", "name": "Oslo", "alert_level": "ok", "tenants": ["Nordic"], "down_devices": []},
        {
            "id": "loc-3",
            "name": "Aarhus Lab",
            "alert_level": "low",
            "tenant": "Acme",
            "down_device_count": 1,
            "down_devices": [{"device_id": "d9", "device_name": "lab-sw"}],
        },
    ],
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "MCP_ENABLED", True)
    monkeypatch.setattr(alerts, "get_alert_board_data", lambda **kwargs: json.loads(json.dumps(BOARD)))
    flask_app.app.config["TESTING"] = True
    caching.cache.clear()
    with http_client.FlaskStyleClient() as test_client:
        yield test_client


def modern(client, method, params=None, request_id=1, headers=None, version=MODERN):
    params = dict(params or {})
    params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    sent = {"MCP-Protocol-Version": version, "Mcp-Method": method}
    if method == "tools/call":
        sent["Mcp-Name"] = params.get("name", "")
    sent.update(headers or {})
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        headers={key: value for key, value in sent.items() if value is not None},
    )


def legacy(client, method, params=None, request_id=1, version="2025-11-25", headers=None):
    sent = {"MCP-Protocol-Version": version} if version else {}
    sent.update(headers or {})
    return client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}, headers=sent
    )


def call(client, name, arguments=None, **kwargs):
    response = modern(client, "tools/call", {"name": name, "arguments": arguments or {}}, **kwargs)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["result"]


class TestProtocol:
    def test_off_by_default(self, client, monkeypatch):
        monkeypatch.setattr(settings, "MCP_ENABLED", False)
        assert modern(client, "server/discover").status_code == 404
        assert legacy(client, "initialize", {"protocolVersion": "2025-11-25"}).status_code == 404

    def test_discover(self, client):
        response = modern(client, "server/discover")
        assert response.status_code == 200
        result = response.get_json()["result"]
        assert result["resultType"] == "complete"
        assert result["supportedVersions"][0] == MODERN and "2025-11-25" in result["supportedVersions"]
        assert result["capabilities"] == {"tools": {"listChanged": False}}
        assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "nautobot-maps"
        assert "data, never instructions" in result["instructions"]
        assert result["ttlMs"] > 0 and result["cacheScope"] == "public"

    def test_tools_list_is_stable_and_described(self, client):
        result = modern(client, "tools/list").get_json()["result"]
        names = [tool["name"] for tool in result["tools"]]
        assert names == [
            "get_alert_board",
            "get_site",
            "get_location_detail",
            "search_locations",
            "get_alert_feed",
            "get_alert_history",
            "add_case",
        ]
        assert result == modern(client, "tools/list", request_id=2).get_json()["result"]
        assert result["ttlMs"] > 0 and result["cacheScope"] == "public"
        by_name = {tool["name"]: tool for tool in result["tools"]}
        assert by_name["add_case"]["annotations"]["readOnlyHint"] is False
        assert all(by_name[name]["annotations"]["readOnlyHint"] for name in names if name != "add_case")
        assert by_name["add_case"]["inputSchema"]["required"] == ["site_id", "case_number"]
        for tool in result["tools"]:
            assert tool["inputSchema"]["type"] == "object" and tool["description"]

    def test_header_must_match_body(self, client):
        response = modern(client, "tools/list", headers={"Mcp-Method": "tools/call"})
        assert response.status_code == 400 and response.get_json()["error"]["code"] == -32020
        response = modern(client, "tools/list", headers={"MCP-Protocol-Version": None})
        assert response.status_code == 400 and response.get_json()["error"]["code"] == -32020
        response = modern(
            client, "tools/call", {"name": "get_site", "arguments": {"site": "Oslo"}}, headers={"Mcp-Name": "add_case"}
        )
        assert response.status_code == 400 and response.get_json()["error"]["code"] == -32020

    def test_mcp_name_may_be_base64(self, client):
        encoded = "=?base64?" + base64.b64encode(b"get_site").decode() + "?="
        result = call(client, "get_site", {"site": "Oslo"}, headers={"Mcp-Name": encoded})
        assert result["isError"] is False

    def test_unsupported_version_lists_the_supported_ones(self, client):
        response = modern(client, "tools/list", version="2099-01-01")
        assert response.status_code == 400
        error = response.get_json()["error"]
        assert error["code"] == -32022 and error["data"]["requested"] == "2099-01-01"
        assert MODERN in error["data"]["supported"]

    def test_missing_client_capabilities_is_invalid_params(self, client):
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": MODERN}},
        }
        response = client.post("/mcp", json=body, headers={"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/list"})
        assert response.status_code == 400 and response.get_json()["error"]["code"] == -32602

    def test_unknown_method(self, client):
        response = modern(client, "resources/list")
        assert response.status_code == 404 and response.get_json()["error"]["code"] == -32601
        response = legacy(client, "resources/list")
        assert response.status_code == 200 and response.get_json()["error"]["code"] == -32601

    def test_legacy_handshake_and_calls(self, client):
        result = legacy(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}, version=None)
        result = result.get_json()["result"]
        assert result["protocolVersion"] == "2025-06-18" and "tools" in result["capabilities"]
        assert "resultType" not in result
        newer = legacy(client, "initialize", {"protocolVersion": "2099-01-01"}, version=None).get_json()["result"]
        assert newer["protocolVersion"] == "2025-11-25"
        notified = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert notified.status_code == 202 and notified.data == b""
        assert "Mcp-Session-Id" not in notified.headers
        tools = legacy(client, "tools/list").get_json()["result"]
        assert "resultType" not in tools and "ttlMs" not in tools and len(tools["tools"]) == 7
        # Before 2025-06-18 there was no version header.
        called = legacy(client, "tools/call", {"name": "get_site", "arguments": {"site": "Oslo"}}, version=None)
        assert called.get_json()["result"]["structuredContent"]["id"] == "loc-2"
        assert legacy(client, "ping").get_json()["result"] == {}
        assert legacy(client, "server/discover").get_json()["error"]["code"] == -32601

    def test_modern_version_header_without_meta(self, client):
        response = legacy(client, "tools/list", version=MODERN)
        assert response.status_code == 400 and response.get_json()["error"]["code"] == -32602

    def test_malformed_requests(self, client):
        assert client.post("/mcp", data="not json", content_type="application/json").status_code == 400
        batch = client.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        assert batch.status_code == 400 and batch.get_json()["error"]["code"] == -32600
        bad_id = client.post("/mcp", json={"jsonrpc": "2.0", "id": None, "method": "ping"})
        assert bad_id.status_code == 400
        assert (
            client.post("/mcp", data="x" * (mcp.MAX_REQUEST_BYTES + 1), content_type="application/json").status_code
            == 413
        )

    def test_get_is_not_allowed(self, client):
        assert client.get("/mcp").status_code == 405

    def test_browser_origins_are_refused_unless_listed(self, client, monkeypatch):
        response = modern(client, "tools/list", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403
        # Matching the Host header is no defence against DNS rebinding:
        # after rebinding, Origin and Host both name the attacker's domain.
        assert modern(client, "tools/list", headers={"Origin": "http://localhost"}).status_code == 403
        assert modern(client, "tools/list", headers={"Origin": "null"}).status_code == 403
        monkeypatch.setattr(settings, "MCP_ALLOWED_ORIGINS", ["https://Assistant.example.com"])
        assert modern(client, "tools/list", headers={"Origin": "https://assistant.example.com"}).status_code == 200
        assert modern(client, "tools/list", headers={"Origin": "http://assistant.example.com"}).status_code == 403
        # MCP clients send no Origin at all.
        assert modern(client, "tools/list").status_code == 200

    def test_size_limit_also_without_content_length(self, client):
        import io

        big = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "pad": "x" * mcp.MAX_REQUEST_BYTES}).encode()
        chunked = client.post(
            "/mcp",
            input_stream=io.BytesIO(big),
            content_type="application/json",
            # A chunked upload: no Content-Length, the server reads to the end.
            environ_overrides={
                "wsgi.input_terminated": True,
                "CONTENT_LENGTH": "",
                "HTTP_TRANSFER_ENCODING": "chunked",
            },
        )
        assert chunked.status_code == 413
        small = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
        ok = client.post(
            "/mcp",
            input_stream=io.BytesIO(small),
            content_type="application/json",
            # A chunked upload: no Content-Length, the server reads to the end.
            environ_overrides={
                "wsgi.input_terminated": True,
                "CONTENT_LENGTH": "",
                "HTTP_TRANSFER_ENCODING": "chunked",
            },
        )
        assert ok.status_code == 200 and ok.get_json()["result"] == {}

    def test_unknown_tool_and_bad_arguments(self, client):
        response = modern(client, "tools/call", {"name": "drop_tables", "arguments": {}})
        assert response.get_json()["error"]["code"] == -32602
        result = call(client, "get_site", {"site": "Oslo", "evil": 1})
        assert result["isError"] and "Unknown arguments: evil" in result["content"][0]["text"]
        result = call(client, "get_alert_board", {"limit": "ten"})
        assert result["isError"] and "whole number" in result["content"][0]["text"]

    def test_rate_limit(self, client, monkeypatch):
        monkeypatch.setattr(mcp, "RATE_LIMIT_PER_MINUTE", 2)
        # One clock minute, so the test can't straddle two windows.
        monkeypatch.setattr(mcp, "time", SimpleNamespace(time=lambda: 6000.0))
        assert not call(client, "get_site", {"site": "Oslo"})["isError"]
        assert not call(client, "get_site", {"site": "Oslo"})["isError"]
        limited = call(client, "get_site", {"site": "Oslo"})
        assert limited["isError"] and "Too many calls" in limited["content"][0]["text"]


class TestTools:
    def test_board_defaults_to_alarms(self, client):
        result = call(client, "get_alert_board")
        board = result["structuredContent"]
        assert [site["name"] for site in board["sites"]] == ["Aarhus HQ", "Aarhus Lab"]
        assert board["summary"]["critical"] == 1 and board["matching_sites"] == 2
        aarhus = board["sites"][0]
        assert aarhus["path"] == "EMEA › DNK" and aarhus["down_devices"][0]["device_ip"] == "10.0.0.1"
        # The text block is the same JSON, for clients that ignore structuredContent.
        assert json.loads(result["content"][0]["text"]) == board

    def test_board_filters(self, client):
        names = lambda args: [s["name"] for s in call(client, "get_alert_board", args)["structuredContent"]["sites"]]  # noqa: E731
        assert names({"filter": "all"}) == ["Aarhus HQ", "Oslo", "Aarhus Lab"]
        assert names({"filter": "ok"}) == ["Oslo"]
        assert names({"tenant": "Acme"}) == ["Aarhus HQ", "Aarhus Lab"]
        # As the board's ?tenant= link (#375): case ignored, the whole name.
        assert names({"tenant": "acme"}) == ["Aarhus HQ", "Aarhus Lab"]
        assert names({"tenant": "Acm"}) == []
        assert names({"tenant": "acme", "search": "lab"}) == ["Aarhus Lab"]
        assert names({"search": "dnk"}) == ["Aarhus HQ"]
        limited = call(client, "get_alert_board", {"filter": "all", "limit": 1})["structuredContent"]
        assert len(limited["sites"]) == 1 and "Showing 1 of 3" in limited["truncated"]
        bad = call(client, "get_alert_board", {"filter": "red"})
        assert bad["isError"] and "filter must be one of" in bad["content"][0]["text"]

    def test_get_site_by_id_name_or_part(self, client):
        assert call(client, "get_site", {"site": "loc-2"})["structuredContent"]["name"] == "Oslo"
        assert call(client, "get_site", {"site": "aarhus hq"})["structuredContent"]["id"] == "loc-1"
        assert call(client, "get_site", {"site": "lab"})["structuredContent"]["id"] == "loc-3"
        ambiguous = call(client, "get_site", {"site": "Aarhus"})
        assert ambiguous["isError"] and "2 sites match" in ambiguous["content"][0]["text"]
        missing = call(client, "get_site", {"site": "Bergen"})
        assert missing["isError"] and "No site matches" in missing["content"][0]["text"]

    def test_add_case_rejects_malformed_device_ids_before_anything_else(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(mcp, "call_api", lambda *args, **kwargs: calls.append(args) or (200, {}))
        for bad in ("", 0, False, "d1", [1], {}):
            result = call(client, "add_case", {"site_id": "loc-1", "case_number": "INC-9", "device_ids": bad})
            assert result["isError"] and "device_ids must be a list of strings" in result["content"][0]["text"], bad
        assert calls == [], "nothing may be looked up or written"

    def test_feed_validation_comes_from_the_route(self, client):
        result = call(client, "get_alert_feed", {"since": "yesterday"})
        assert result["isError"] and "since must be an ISO-8601 timestamp (HTTP 400)" in result["content"][0]["text"]

    def test_feed_for_one_tenant_or_search(self, client, monkeypatch):
        """tenant / search reach the route as ?tenant= / ?q= (#375); the route's filtering is tested in test_app."""
        calls, answers = [], [{"events": [], "matching_sites": 2}, {"events": []}, {"events": [], "matching_sites": 0}]
        monkeypatch.setattr(mcp, "call_api", lambda *args, **kwargs: calls.append(args) or (200, answers.pop(0)))
        scoped = call(client, "get_alert_feed", {"tenant": " Acme Corp ", "search": "DNK", "limit": 5})
        every = call(client, "get_alert_feed", {})
        (_, path, scoped_query, *_), (_, _, every_query, *_) = calls[:2]
        assert path == "/api/alert-feed"
        assert scoped_query["tenant"] == "Acme Corp" and scoped_query["q"] == "DNK" and scoped_query["limit"] == "5"
        assert every_query["tenant"] == "" and every_query["q"] == ""
        assert "note" not in scoped["structuredContent"] and "note" not in every["structuredContent"]
        # No site matches: said so, not an empty feed that reads as "nothing changed".
        typo = call(client, "get_alert_feed", {"tenant": "Acme Corp."})["structuredContent"]
        assert typo["events"] == [] and "No site matches" in typo["note"]
        assert call(client, "get_alert_feed", {"tenant": 5})["isError"]

    def test_feed_scope_reaches_the_route(self, client, monkeypatch):
        """Through the real route: the scope picks the sites, and no scope sends none (#375)."""
        from nautobot_maps import db, inventory

        class Conn:
            def close(self):
                pass

        seen = []
        monkeypatch.setattr(db, "get_conn", Conn)
        monkeypatch.setattr(
            inventory,
            "read_locations",
            lambda **kwargs: [
                {"id": "loc-1", "name": "Aarhus HQ", "tenants": ["Acme Corp"]},
                {"id": "loc-2", "name": "Oslo", "tenants": ["Nordic"]},
            ],
        )
        monkeypatch.setattr(board_store, "read_alert_feed", lambda *args: seen.append(args[-1]) or [])
        assert call(client, "get_alert_feed", {"tenant": "acme corp"})["structuredContent"]["matching_sites"] == 1
        assert "note" in call(client, "get_alert_feed", {"search": "bergen"})["structuredContent"]
        assert "matching_sites" not in call(client, "get_alert_feed")["structuredContent"]
        assert seen == [["loc-1"], [], None]

    def test_search_uses_the_route(self, client, monkeypatch):
        from nautobot_maps import inventory

        monkeypatch.setattr(
            inventory,
            "get_locations",
            lambda: [{"id": "loc-1", "name": "Aarhus HQ", "latitude": 56.15, "longitude": 10.2}],
        )
        found = call(client, "search_locations", {"query": "56.15,10.2"})["structuredContent"]
        assert found["count"] == 1 and found["locations"][0]["id"] == "loc-1"


class TestRoles:
    def test_viewer_cannot_read_history_or_add_cases(self, client):
        viewer = {"X-Forwarded-User": "vera", "X-Forwarded-Groups": "noc"}
        with auth_config(mode="header", viewer_groups={"noc"}, operator_groups={"ops"}):
            history = call(client, "get_alert_history", {}, headers=viewer)
            assert history["isError"]
            assert "needs the operator role (yours: viewer)" in history["content"][0]["text"]
            case = call(client, "add_case", {"site_id": "loc-1", "case_number": "INC-1"}, headers=viewer)
            assert case["isError"] and "operator" in case["content"][0]["text"]
            # Reads open to everyone stay open.
            assert not call(client, "get_site", {"site": "Oslo"}, headers=viewer)["isError"]

    def test_require_viewer_protects_the_endpoint(self, client, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
        with auth_config(mode="header", viewer_groups={"noc"}):
            assert modern(client, "tools/list").status_code == 401
            signed_in = {"X-Forwarded-User": "vera", "X-Forwarded-Groups": "noc"}
            assert modern(client, "tools/list", headers=signed_in).status_code == 200


class TestAddCase:
    @pytest.fixture(autouse=True)
    def _database(self, pg_database):
        self.db = pg_database

    def _open_alerts(self):
        site = {"id": "loc-1", "name": "Aarhus HQ"}
        down = [{"id": "d1", "name": "core01", "status": "offline"}, {"id": "d2", "name": "acc01", "status": "offline"}]
        started = datetime(2026, 10, 5, 9, 50, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        alerts.upsert_alert_lifecycle_for_site(site, down, {"level": "critical", "reason": "Core down"}, started)

    def test_defaults_to_every_down_device_and_shows_in_history(self, client):
        self._open_alerts()
        operator = {"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"}
        with auth_config(mode="header", operator_groups={"ops"}):
            result = call(client, "add_case", {"site_id": "loc-1", "case_number": "INC-42"}, headers=operator)
            assert result["isError"] is False, result
            linked = {item["device_id"] for item in result["structuredContent"]["linked"]}
            assert linked == {"d1", "d2"}
            history = call(client, "get_alert_history", {"site_id": "loc-1"}, headers=operator)["structuredContent"]
            cases = {case["case_number"] for instance in history["instances"] for case in instance["cases"]}
            assert cases == {"INC-42"}
            assert {case["created_by"] for i in history["instances"] for case in i["cases"]} == {"olga"}
            assert all("snapshot" not in event for i in history["instances"] for event in i["events"])

    def test_a_client_asking_for_gzip_still_gets_the_history(self, client):
        """The tools read the API's answer themselves: the caller's Accept-Encoding
        must not make it a gzip body they can't read (most MCP clients send it)."""
        self._open_alerts()
        call(client, "add_case", {"site_id": "loc-1", "case_number": "INC-77"})
        history = call(client, "get_alert_history", {"site_id": "loc-1"}, headers={"Accept-Encoding": "gzip"})
        cases = {case["case_number"] for i in history["structuredContent"]["instances"] for case in i["cases"]}
        assert cases == {"INC-77"}

    def test_an_allowed_browser_client_can_add_a_case(self, client, monkeypatch):
        """A browser MCP client from an allowed origin passes /mcp's own check;
        its tool's call must not be refused as a change from another site."""
        monkeypatch.setattr(settings, "MCP_ALLOWED_ORIGINS", ["https://assistant.example"])
        self._open_alerts()
        browser = {"Origin": "https://assistant.example", "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors"}
        result = call(client, "add_case", {"site_id": "loc-1", "case_number": "INC-5"}, headers=browser)
        assert result["isError"] is False, result
        assert {item["device_id"] for item in result["structuredContent"]["linked"]} == {"d1", "d2"}

    def test_malformed_device_ids_never_mean_all_devices(self, client):
        self._open_alerts()
        for bad in ("", 0, False, "d1", [1]):
            result = call(client, "add_case", {"site_id": "loc-1", "case_number": "INC-9", "device_ids": bad})
            assert result["isError"] and "device_ids must be a list of strings" in result["content"][0]["text"], bad
        history = call(client, "get_alert_history", {"site_id": "loc-1"})["structuredContent"]
        assert not [case for instance in history["instances"] for case in instance["cases"]]

    def test_all_or_nothing(self, client):
        self._open_alerts()
        result = call(client, "add_case", {"site_id": "loc-1", "case_number": "INC-7", "device_ids": ["d1", "gone"]})
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "nothing was changed" in text and '"missing_device_ids": ["gone"]' in text and "(HTTP 404)" in text
