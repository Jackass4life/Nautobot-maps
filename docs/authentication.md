# Sign-in and roles

## Without sign-in (default)

With `AUTH_MODE=disabled`:

- the map, the alert board and the read-only APIs are open to anyone who can reach the app;
- adding a case number to an alert works (the board needs it);
- **changing criticality overrides is refused** (403): it changes which devices count as critical, for everyone. Set `ALLOW_UNAUTHENTICATED_WRITES=true` only if the app is reachable by trusted users alone.

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
| `operator` | Also add cases, view alert history and manage criticality overrides |
| `admin` | Everything an operator can (reserved for future administrative features) |

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

`AUTH_REQUIRE_VIEWER=true` makes every page and API need at least the `viewer` role, except `/healthz` and `/metrics` (probes and Prometheus send no identity; `/metrics` holds only counts). This includes a wall screen's browser and MCP clients: they must sign in through the proxy too.
