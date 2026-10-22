"""A change from another site is refused (CSRF): behind a sign-in proxy the
browser's session cookie comes along, so a page elsewhere could otherwise end
a maintenance window or revoke a token with a plain form."""

import pytest

from nautobot_maps import settings

# Changes that need no body, so a plain cross-site form could send them.
BODYLESS = ["/api/maintenance/1/end", "/api/tokens/1/revoke", "/api/notifications/test"]


@pytest.fixture(autouse=True)
def writes_allowed(monkeypatch):
    # AUTH_MODE=disabled with writes open: only the cross-site check stands in the way.
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)


@pytest.mark.parametrize("path", BODYLESS)
@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
    ],
)
def test_a_change_from_another_site_is_refused(client, path, headers):
    response = client.post(path, headers=headers)
    assert response.status_code == 403
    assert response.get_json() == {"error": "Changes from another site are refused"}


def test_the_apps_own_pages_and_scripts_still_can(client):
    for headers in (
        {"Sec-Fetch-Site": "same-origin"},
        {"Origin": "http://localhost"},
        # A proxy may pass the Host without the browser's port.
        {"Origin": "http://localhost:8443"},
        # curl, scripts, webhooks: no browser headers.
        {},
    ):
        response = client.post("/api/notifications/test", headers=headers)
        assert response.status_code != 403 or "another site" not in response.get_data(as_text=True), headers


def test_reads_and_api_tokens_are_not_affected(client):
    assert client.get("/healthz", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    # A page can't add an Authorization header without the app's CORS consent: a token is not a cookie.
    response = client.post(
        "/api/alertmanager", headers={"Sec-Fetch-Site": "cross-site", "Authorization": "Bearer nmt_unknown"}
    )
    assert "another site" not in response.get_data(as_text=True)


def test_mcp_keeps_its_own_origin_rule(client, monkeypatch):
    monkeypatch.setattr(settings, "MCP_ENABLED", True)
    monkeypatch.setattr(settings, "MCP_ALLOWED_ORIGINS", ["https://assistant.example"])
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Origin": "https://assistant.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert "another site" not in response.get_data(as_text=True)
