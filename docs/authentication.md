# Sign-in and roles

## Without sign-in (default)

With `AUTH_MODE=disabled`:

- the map, the alert board and the read-only APIs are open to anyone who can reach the app;
- adding a case number to an alert works (the board needs it);
- **changing criticality overrides, managing maintenance windows, sending test notifications and everything on the Admin page are refused** (403): they change what alarms for everyone, or send real messages to your channels. Set `ALLOW_UNAUTHENTICATED_WRITES=true` only if the app is reachable by trusted users alone; for scripts, an operator [API token](#api-tokens) is the safer way.

## With a sign-in proxy

Set `AUTH_MODE=header` and put the app behind a reverse proxy or SSO gateway that handles the login and passes the user and their groups in headers. The app ships one ready to use, nginx + oauth2-proxy ([the bundled proxy](#the-bundled-proxy-https-and-company-sign-in-423)); the settings below are what it sets for you, and what your own proxy needs.

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
| `admin` | Everything an operator can, send test notifications (`POST /api/notifications/test`), manage API tokens, show and replace tenants' status page links (`/api/status-links/…`) and check the wireless controllers (`POST /api/wireless/check`) |

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

### The bundled proxy: HTTPS and company sign-in (#423)

Instead of setting up a proxy yourself, use the one in `deploy/proxy/`: **nginx** for HTTPS and **oauth2-proxy** for the login with any OIDC provider (Entra ID, Keycloak, Okta, Google).

1. **Register the app** with your identity provider. The redirect URL is `https://<your host>/oauth2/callback`, and the ID token must carry the user's groups (examples below).
2. **Certificates:** put `fullchain.pem` and `privkey.pem` in `deploy/proxy/certs/` (never committed; from your CA: [your own certificate](#your-own-certificate)). To try it out, a self-signed one:
   ```sh
   openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=maps.example.com" \
     -keyout deploy/proxy/certs/privkey.pem -out deploy/proxy/certs/fullchain.pem
   ```
3. **Settings** in `.env` (see `.env.example`, "HTTPS and sign-in"): `COMPOSE_FILE=docker-compose.yml:deploy/proxy/compose.yml`, `PUBLIC_HOSTNAME`, `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `AUTH_PROXY_SECRET` and `OAUTH2_COOKIE_SECRET`, plus `AUTH_VIEWER_GROUPS` / `AUTH_OPERATOR_GROUPS` / `AUTH_ADMIN_GROUPS` with your provider's group names or IDs.
4. `docker compose up -d`, then open `https://<your host>`.

What it does:

- **Port 80** only redirects to HTTPS, except `/healthz` for probes. **443** serves TLS 1.2 and 1.3 with HSTS. nginx listens on IPv6 too where the host has it, and on IPv4 only where it doesn't (#527).
- **Every request** is checked with oauth2-proxy. Someone not signed in goes to your login page and comes back. The app gets the user (username, else e-mail) and groups as `X-Forwarded-User` / `X-Forwarded-Groups`, with `AUTH_PROXY_SECRET`. The top bar shows "olga · operator".
- **Without the company login:**
  - Requests with `Authorization: Bearer nmt_…` (API tokens: LibreNMS, Alertmanager, scripts, MCP). Matched on the header, not a path. The app checks the token.
  - The tenant [status page](status-page.md) and its files, because customers have no company login. The app checks their status token. Staff who are signed in are still recognised on the status page's data (`/api/status/`, #527): oauth2-proxy is asked, but only to pass on who is signed in, never to send anyone to the login. So a tenant's name on Availability opens its page for staff without a key, and a customer's key link works whether or not they are signed in.
  - `/healthz`.
  
  On these, nginx overwrites the identity headers (empty unless oauth2-proxy says who is signed in), so nobody can claim a user by sending them.
- **`/metrics`** is not served through the proxy. Prometheus scrapes the app inside the network.
- **The app:**
  - is no longer published on a host port;
  - trusts identity headers only from nginx's fixed address (`PROXY_IP`, default `172.30.0.10` on its own network `PROXY_SUBNET`; change both if that range is taken) and only with the secret;
  - runs with `AUTH_REQUIRE_VIEWER=true`.
- **The login** uses PKCE (`S256`, #527), which Entra ID, Keycloak, Okta, Google and GitLab all accept.
- **Sessions** last 12 hours and are refreshed while in use. The identity provider is not asked on every request. The session cookie is `SameSite=Lax`, so a browser never sends it with a POST from another site.
- **Users in many groups** (Entra ID sends an ID of 36 characters each) are fine: nginx's header buffers are raised to 32 KB.

**Entra ID (Azure AD):**
- `OIDC_ISSUER_URL=https://login.microsoftonline.com/<tenant-id>/v2.0` and `OIDC_EMAIL_CLAIM=preferred_username`.
- In the app registration, under **Token configuration**, add the **groups** claim to the ID token. Groups then arrive as object IDs, so use those IDs in `AUTH_*_GROUPS`. Users in more than 200 groups get no list: assign groups to the application (Enterprise app, Users and groups) or use app roles.

**Keycloak:**
- `OIDC_ISSUER_URL=https://keycloak.example.com/realms/<realm>`.
- Use a confidential client.
- Add a **Group Membership** mapper with the claim name `groups`, *Full group path* off, added to the ID token.

**GitLab** (self-managed or gitlab.com, #526):
- Create the application under **Admin Area → Applications** (instance-wide) or a group's **Settings → Applications**: redirect URI `https://<your host>/oauth2/callback`, **Confidential** on, scopes `openid`, `profile` and `email`.
- `OIDC_ISSUER_URL=https://gitlab.example.com` (the GitLab address itself, no path), and its application ID and secret as `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET`.
- `OIDC_GROUPS_CLAIM=groups_direct`: the groups the user is a direct member of, as full paths such as `netops/noc`. Use those paths in `AUTH_*_GROUPS` (e.g. `AUTH_OPERATOR_GROUPS=netops/noc`). Membership through a parent group doesn't count: add the users to the group itself.
- `AUTH_DEFAULT_ROLE` (a role for everyone who signs in) is only safe on a self-managed GitLab whose accounts are your staff. **Never on gitlab.com**: anyone with a gitlab.com account can sign in to the application.
- A self-managed GitLab with a certificate from your company CA also needs `OIDC_CA_FILE` (below).

#### Company CA

oauth2-proxy checks the identity provider's certificate against the public CAs. A self-managed GitLab, an on-prem Keycloak or ADFS with a certificate from your company's own CA then stops it at start:

```
error while discovery OIDC configuration: ... tls: failed to verify certificate: x509: certificate signed by unknown authority
```

Give it that CA (#526):

1. Get the CA certificate as a PEM file: the root (and any intermediate) CA from your IT, or the host's bundle if the host already trusts it (`/etc/ssl/certs/ca-certificates.crt` on Debian and Ubuntu, `/etc/pki/tls/certs/ca-bundle.crt` on RHEL). Copy it into the [config folder](configuration.md#configuration), e.g. `config/company-ca.pem`.
2. Make it readable by all: `chmod 644 config/company-ca.pem`. oauth2-proxy runs as a user of its own, not the app's group, and a CA certificate is public anyway.
3. `OIDC_CA_FILE=./config/company-ca.pem` in `.env`, then `docker compose up -d`.

It is trusted **together with** the public CAs, so a provider with a public certificate keeps working. A path that doesn't exist stops `docker compose up` with an error.

Don't turn the certificate check off instead (`OAUTH2_PROXY_SSL_INSECURE_SKIP_VERIFY=true`): then anyone on the network who can answer for the identity provider's address can sign in as anyone, admin included.

#### Your own certificate

The self-signed certificate from step 2 is for trying it out. With one from your CA, replace the two files in `deploy/proxy/certs/`:

- `fullchain.pem`: the server certificate **first**, then the intermediate certificate(s), all PEM (`-----BEGIN CERTIFICATE-----`). From separate files: `cat server.crt intermediate.crt > deploy/proxy/certs/fullchain.pem`. A DER file (no `BEGIN` line) first: `openssl x509 -inform der -in server.cer -out server.crt`.
- `privkey.pem`: the private key, PEM, without a passphrase. `chmod 600` it.
- From a `.pfx` / `.p12`: `openssl pkcs12 -in cert.pfx -nokeys -out deploy/proxy/certs/fullchain.pem` and `openssl pkcs12 -in cert.pfx -nocerts -nodes -out deploy/proxy/certs/privkey.pem` (add `-legacy` if OpenSSL 3 says "unsupported"); check the server certificate comes first.

Check before restarting:

```sh
# the key belongs to the certificate: the two lines are the same
openssl x509 -noout -pubkey -in deploy/proxy/certs/fullchain.pem | sha256sum
openssl pkey -pubout -in deploy/proxy/certs/privkey.pem | sha256sum
# the name is PUBLIC_HOSTNAME, and the dates
openssl x509 -noout -subject -ext subjectAltName -dates -in deploy/proxy/certs/fullchain.pem
```

Then `docker compose exec nginx nginx -t` and `docker compose restart nginx`: nginx reads the certificate only when it starts. `openssl s_client -connect <host>:443 -servername <host> </dev/null` should end in `Verify return code: 0 (ok)`; a browser that is happy while this complains means an intermediate is missing from `fullchain.pem`. Renewing is the same: replace the files and restart nginx.

#### HTTPS on another port

HTTPS must stay on port 443: the sign-in's redirect (`https://<PUBLIC_HOSTNAME>/oauth2/callback`) and nginx's HTTP→HTTPS redirect name no port. HTTP can move when port 80 is taken (`PROXY_HTTP_PORT=8080`); only the redirect from `http://` to `https://` is then lost.

### Signing out

**Sign out** in the ☰ menu (#546) is there for someone signed in through the proxy (not with an API token, and not with `AUTH_MODE=disabled`). It goes to `AUTH_SIGN_OUT_URL`; with your own proxy, set that to its sign-out address.

The bundled proxy sets it to `/oauth2/sign_out`: oauth2-proxy deletes its session cookie, then nginx sends the browser to `OIDC_LOGOUT_URL`, your identity provider's logout, so its session ends too. Without `OIDC_LOGOUT_URL` you land on the app again, and since you are still signed in at the identity provider, it signs you straight back in: set it.

| Provider | `OIDC_LOGOUT_URL` |
|---|---|
| Entra ID | `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/logout?post_logout_redirect_uri=https://<your host>/` |
| Keycloak | `https://keycloak.example.com/realms/<realm>/protocol/openid-connect/logout?client_id=<client id>&post_logout_redirect_uri=https://<your host>/` |
| GitLab | none: GitLab has no logout address for other applications. Sign out ends the app's session only; sign out of GitLab itself to end both |

- **Entra ID:** add `https://<your host>/` under the app registration's **Authentication → Redirect URIs** (Web) to come back to the app's sign-in; otherwise Entra ID still signs you out and stops at its own "You signed out" page.
- **Keycloak:** add `https://<your host>/` to the client's **Valid post logout redirect URIs**. Keycloak may ask "Do you want to log out?" first.

Ending the identity provider's session signs you out of it in that browser, as its own logout does: other web applications that use it (Outlook on the web, say) ask you to sign in again the next time they check with it.

### Changes from another site are refused

Behind any sign-in proxy, the browser's session cookie comes along with every request, so a page on another site could make a signed-in user send a change (a form posting to `/api/maintenance/<id>/end`, say).

The app refuses any change (POST, PUT, PATCH, DELETE) that the browser marks as coming from another site (`Sec-Fetch-Site`, else `Origin`), with **403**. This works in every mode.
- The app's own pages are not affected.
- Scripts, webhooks and MCP clients send no such headers or use an API token, so they are not affected either.
- `/mcp` keeps its own rule (`MCP_ALLOWED_ORIGINS`).

With your own proxy, also set its session cookie to `SameSite=Lax`.

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
- A token with role `status` belongs to one tenant and opens only that tenant's [status page](status-page.md) (403 everywhere else). Create it with `--role status --tenant "Acme Corp"` or `{"role": "status", "tenant": "Acme Corp"}`.
- Other `Bearer` values (not starting with `nmt_`, e.g. an access token your proxy forwards) are ignored; sign-in then works as without a token.

### Creating and revoking

On the command line (this is how the first admin token is made):

```sh
docker compose exec nautobot-maps python -m nautobot_maps token create librenms --role operator --expires-days 365
docker compose exec nautobot-maps python -m nautobot_maps token list
docker compose exec nautobot-maps python -m nautobot_maps token revoke 3
docker compose exec nautobot-maps python -m nautobot_maps token purge --older-than 90 --dry-run
```

On the **Admin** page (☰ → **Admin**, only for admins, #530) the same without a command line: the active tokens newest first (a search past 25), **New token** (name, role, for a status token its tenant picked from Nautobot's, and how long it works) shows the key once with **Copy**, **Revoke** per row (asks once more), and "+N revoked or expired" opens them, with **Purge**. With `AUTH_MODE=disabled` the page is there only with `ALLOW_UNAUTHENTICATED_WRITES=true`, as the routes it uses.

`token list` shows the tokens that work, then "+N revoked or expired (--all to show)"; `--all` lists those too, and `--tenant "Acme Corp"` only that tenant's status tokens (#528).

A revoked or expired token stays in the list with its name, for the audit trail: changes made with it are recorded as `token:<name>`, and a name can't be used twice. `token purge` deletes revoked and expired tokens for good, never an active one; `--older-than DAYS` only those that stopped working more than DAYS ago, so a recent revocation stays visible a while; `--dry-run` lists what would go and deletes nothing. Each purge is logged (`API token 'x' purged by cli`), and the names are free again. Nothing else refers to a token's record: cases and maintenance windows keep their `token:<name>` text, only the token's own record (when it was made and revoked) is gone.

Or through the API (admin role; also on the API page, `/api/docs`): `POST /api/tokens` with `{"name": "librenms", "role": "operator", "expires_in_days": 365}` returns the token once; `GET /api/tokens` lists the active ones (name, first characters, role, expiry, last use, state; never the secret) and `inactive`, how many are revoked or expired, `?all=true` lists those too and `?tenant=Acme%20Corp` only that tenant's; `POST /api/tokens/<id>/revoke` revokes one from the next request on; `POST /api/tokens/purge` with `{"older_than_days": 90, "dry_run": true}` (both optional) answers the tokens it deleted, or would delete.

### Behind a sign-in proxy

The request must reach the app with its `Authorization` header. If the proxy requires SSO on every path, let requests with a token past it, e.g. oauth2-proxy's `--skip-auth-route` for the API paths your scripts use (`--skip-jwt-bearer-tokens` doesn't help: these tokens are not JWTs). The app checks the token itself.

A path-based skip also lets requests **without** a token past SSO. Set `AUTH_REQUIRE_VIEWER=true` with it, so those get 401 instead of the read APIs that are otherwise open to everyone (or skip SSO only for requests that carry `Authorization: Bearer nmt_`, if your proxy can match on headers).

On routes the proxy doesn't authenticate, it must still **remove or overwrite** `X-Forwarded-User` and `X-Forwarded-Groups` from the client (nginx: `proxy_set_header X-Forwarded-User "";`). Otherwise a client without a token could send them through the trusted proxy and claim any user.
