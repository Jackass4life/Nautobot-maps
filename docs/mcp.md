# MCP server (AI assistants)

With `MCP_ENABLED=true` the app serves an [MCP](https://modelcontextprotocol.io/) server at `/mcp`, so an AI assistant (Claude Code, Claude Desktop, or any MCP client) can answer questions like "which sites are down and since when?" and add a case to an alarm.

| Tool | What it does | Role when sign-in is on |
|---|---|---|
| `get_alert_board` | Sites with alarms (or one level, or all): level, reason, down devices with IP and role, downtime, cases; filter by tenant or text | viewer\* |
| `get_site` | One site by id or name | viewer\* |
| `get_location_detail` | All devices, ASNs and circuits at a location | viewer\* |
| `search_locations` | Locations within 5 km of an address or `lat,lon` | viewer\* |
| `get_alert_feed` | Recent changes: devices down/up, level changes | viewer\* |
| `get_alert_history` | Past and open incidents, downtime, cases | operator |
| `add_case` | Attach a case number to a site's down devices (all of them by default), like **+ Case** on the board | operator |

\*only with `AUTH_REQUIRE_VIEWER=true`.

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
