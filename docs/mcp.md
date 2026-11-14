# MCP server (AI assistants)

Lets an AI assistant (Claude Code, Claude Desktop, or any MCP client) answer questions such as "which sites are down and since when?" from the live board, and add a case to an alarm. Off by default.

## In short

1. Turn it on: `MCP_ENABLED=true` in `.env`, then `docker compose up -d`.
2. Connect the assistant to `https://<your host>/mcp` ([connecting a client](#connecting-a-client)).
3. It can only see and do what its user could on the board, and it never changes Nautobot.

## Tools

With `MCP_ENABLED=true` the app serves an [MCP](https://modelcontextprotocol.io/) server at `/mcp`, so an AI assistant (Claude Code, Claude Desktop, or any MCP client) can answer questions like "which sites are down and since when?" and add a case to an alarm.

| Tool | What it does | Roles when sign-in is on |
|---|---|---|
| `get_alert_board` | Sites with alarms (or one level, `wireless` for only access points down, or all): level, reason, down devices with IP, role and stack, downtime, cases; filter by tenant or text | viewer\* |
| `get_site` | One site by id or name | viewer\* |
| `get_location_detail` | All devices, ASNs and circuits at a location | viewer\* |
| `search_locations` | Locations within 5 km of an address or `lat,lon` | viewer\* |
| `get_alert_feed` | Recent changes: devices down/up, level changes; for one tenant or text, only those sites' changes | viewer\* |
| `get_alert_history` | Past and open incidents, downtime, cases | dov, operator (NWO), admin |
| `add_case` | Attach a case number to a site's down devices (all of them by default), like **+ Case** on the board | dov, operator (NWO), admin |

\*viewer, dov, operator (NWO) and admin; a role is needed only with `AUTH_REQUIRE_VIEWER=true`. Not the customer role, which has no board ([roles](authentication.md#roles)).

`tenant` and `search` match sites as the board's `?tenant=` / `?q=` link does: the tenant's whole name with case ignored, and the text in the site's name, path, address or country. For `get_alert_feed` the server filters before the limit, so the newest changes asked for are that tenant's, not the whole network's. It counts every stored site, including those `get_alert_board` leaves out unless `include_non_operational` is set, and when no site matches it says so in `note` rather than looking like nothing changed.

The tools run the same code and the same sign-in and role checks as the web pages, so an assistant can never do more than its user could on the board. `add_case` is the only change it can make; nothing is ever written to Nautobot.

## Connecting a client

Claude Code:

```bash
claude mcp add --transport http nautobot-maps https://nautobot-maps.example.com/mcp
```

With `AUTH_MODE=header`, the MCP client's requests must pass through your sign-in proxy like the browser's do. If your client can't sign in there, keep the MCP server on an internal address only, or leave it off.

## Details

- **Protocol:** Streamable HTTP, stateless (any worker answers any request). MCP 2026-07-28, and the earlier `initialize`-based versions 2025-11-25, 2025-06-18 and 2025-03-26.
- **Browsers:** a request with an `Origin` header (that is, from a web page) is refused unless the origin is in `MCP_ALLOWED_ORIGINS`. This also stops DNS-rebinding attacks.
- **Rate limit:** 120 tool calls a minute per caller and worker; with `CACHE_TYPE=RedisCache` the count is shared, so 120 a minute in total.
