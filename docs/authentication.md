# Sign-in and roles

## Without sign-in (default)

With `AUTH_MODE=disabled`:

- the map, the alert board and the read-only APIs are open to anyone who can reach the app;
- adding a case number to an alert works (the board needs it);
- **changing criticality overrides, managing maintenance windows and sending test notifications are refused** (403): they change what alarms for everyone, or send real messages to your channels. Set `ALLOW_UNAUTHENTICATED_WRITES=true` only if the app is reachable by trusted users alone; for scripts, an operator [API token](#api-tokens) is the safer way.

## With a sign-in proxy

Set `AUTH_MODE=header` and put the app behind a reverse proxy or SSO gateway (e.g. oauth2-proxy, nginx with an OIDC/SAML module) that handles the login and passes the user and their groups in headers.

```dotenv
AUTH_MODE=header
AUTH_HEADER_USER=X-Forwarded-User
AUTH_HEADER_GROUPS=X-Forwarded-Groups
AUTH_VIEWER_GROUPS=noc
AUTH_OPERATOR_GROUPS=nautobot-operators
AUTH_ADMIN_GROUPS=nautobot-admins
```

### Roles

| Role | Can |
|---|---|
| `viewer` | Open the map, the board and the read APIs, when `AUTH_REQUIRE_VIEWER=true` (otherwise they are open to everyone) |
| `operator` | Also add cases, view alert history, manage maintenance windows and criticality overrides |
| `admin` | Everything an operator can, send test notifications (`POST /api/notifications/test`) and manage API tokens |

A user's role is the highest one any of their groups maps to, or `AUTH_DEFAULT_ROLE` when none does.

### Trusting only the proxy

The app trusts the identity headers only from the proxy: the direct peer must be in `AUTH_TRUSTED_PROXIES` (default: localhost only) and, if `AUTH_PROXY_SECRET` is set, send it in `X-Auth-Proxy-Secret`. Anything else is treated as anonymous and logged, so a client that reaches the app directly cannot claim to be an admin. The startup log shows which proxies are trusted.

With the proxy as another service in the same compose project, pin the network's subnet and trust it:

```yaml
# docker-compose.override.yml
services:
  nautobot-maps:
    ports: !reset []          # only the proxy talks to the app
networks:
  default:
    ipam:
      config:
        - subnet: 172.30.0.0/24
```

```dotenv
AUTH_MODE=header
AUTH_TRUSTED_PROXIES=172.30.0.0/24
AUTH_PROXY_SECRET=<long random string>
```

and in the proxy (nginx):

```nginx
location / {
    proxy_pass http://nautobot-maps:5000;
    proxy_set_header X-Forwarded-User   $authenticated_user;   # from your SSO module
    proxy_set_header X-Forwarded-Groups $authenticated_groups;
    proxy_set_header X-Auth-Proxy-Secret "<the same secret>";
}
```

### Requiring sign-in everywhere

`AUTH_REQUIRE_VIEWER=true` makes every page and API need at least the `viewer` role, except `/healthz` and `/metrics` (probes and Prometheus send no identity; `/metrics` holds only counts). This includes a wall screen's browser and MCP clients: they sign in through the proxy, or with an API token.

## API tokens

Scripts, LibreNMS and MCP clients that can't log in through SSO use a named **API token** with a role (and optionally an expiry):

```
Authorization: Bearer nmt_...
```

- It works with `AUTH_MODE=disabled` and `header`, from any address (the token is the secret; `AUTH_TRUSTED_PROXIES` doesn't apply). With `disabled`, an operator token can make changes without `ALLOW_UNAUTHENTICATED_WRITES`.
- Changes made with it are recorded as `token:<name>` (e.g. on cases and maintenance windows).
- A wrong, expired or revoked token gets **401** on every request except `/healthz` and `/metrics` (always public), so a script notices.
- Only the token's SHA-256 is stored. The token itself is shown once, when it is created.
- Other `Bearer` values (not starting with `nmt_`, e.g. an access token your proxy forwards) are ignored; sign-in then works as without a token.

### Creating and revoking

On the command line (this is how the first admin token is made):

```sh
docker compose exec nautobot-maps python -m nautobot_maps token create librenms --role operator --expires-days 365
docker compose exec nautobot-maps python -m nautobot_maps token list
docker compose exec nautobot-maps python -m nautobot_maps token revoke 3
```

Or through the API (admin role; also in the API explorer at `/docs`): `POST /api/tokens` with `{"name": "librenms", "role": "operator", "expires_in_days": 365}` returns the token once; `GET /api/tokens` lists them (name, first characters, role, expiry, last use, state; never the secret); `POST /api/tokens/<id>/revoke` revokes one from the next request on.

### Behind a sign-in proxy

The request must reach the app with its `Authorization` header. If the proxy requires SSO on every path, let requests with a token past it, e.g. oauth2-proxy's `--skip-auth-route` for the API paths your scripts use (`--skip-jwt-bearer-tokens` doesn't help: these tokens are not JWTs). The app checks the token itself.

A path-based skip also lets requests **without** a token past SSO. Set `AUTH_REQUIRE_VIEWER=true` with it, so those get 401 instead of the read APIs that are otherwise open to everyone (or skip SSO only for requests that carry `Authorization: Bearer nmt_`, if your proxy can match on headers).

On routes the proxy doesn't authenticate, it must still **remove or overwrite** `X-Forwarded-User` and `X-Forwarded-Groups` from the client (nginx: `proxy_set_header X-Forwarded-User "";`). Otherwise a client without a token could send them through the trusted proxy and claim any user.
