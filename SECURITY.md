# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly.

**Please do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please report security issues by using [GitHub's private vulnerability reporting](../../security/advisories/new).

You should receive a response within 48 hours. If the issue is confirmed, a fix will be released as soon as possible.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Security Best Practices

When deploying Nautobot Maps:

- **Never** commit your `.env` file or expose API tokens in source code.
- Sign people in and serve HTTPS: the bundled nginx + oauth2-proxy (`deploy/proxy/`) does both with your identity provider, or put your own authenticating proxy in front with `AUTH_MODE=header` (see [docs/authentication.md](docs/authentication.md)).
- Let the app trust identity headers only from that proxy (`AUTH_TRUSTED_PROXIES`, `AUTH_PROXY_SECRET`), and set `AUTH_REQUIRE_VIEWER=true` so nothing is open without a role. Changes sent from other sites are refused (CSRF); with your own proxy, set its session cookie to `SameSite=Lax` as well.
- Keep dependencies up to date: Dependabot proposes updates weekly, and CI runs `pip-audit`, `npm audit` and a Trivy image scan on every pull request. Deploy from a current release.
- Restrict network access to your Nautobot instance as appropriate.
