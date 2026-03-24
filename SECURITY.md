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
- Use a strong, unique `FLASK_SECRET_KEY` in production.
- Run behind a reverse proxy (e.g., Nginx) with TLS in production.
- Keep dependencies up to date by regularly running `pip install --upgrade -r requirements.txt`.
- Restrict network access to your Nautobot instance as appropriate.
