"""Checks the proxy's rules end to end (#423): nginx with the stub sign-in.

    python deploy/proxy/ci/smoke.py https://localhost:443 http://localhost:80

The app must run with AUTH_MODE=header and noc-operators as an operator group.
"""

import ssl
import sys
import urllib.error
import urllib.request

HTTPS, HTTP = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/")
SIGNED_IN = {"Cookie": "_nautobot_maps=olga"}
# What a client might send to claim someone else.
FORGED = {"X-Forwarded-User": "mallory", "X-Forwarded-Groups": "noc-operators,nautobot-admins"}
UNVERIFIED = ssl.create_default_context()
UNVERIFIED.check_hostname = False
UNVERIFIED.verify_mode = ssl.CERT_NONE


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


OPENER = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=UNVERIFIED))


def get(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with OPENER.open(request, timeout=20) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode("utf-8", "replace")


def check(name, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + name + (f": {detail}" if not condition else ""))
    return condition


results = []
status, headers, _ = get(f"{HTTP}/alerts")
results.append(
    check("HTTP goes to HTTPS", status == 301 and headers.get("Location", "").startswith("https://"), status)
)
status, _, _ = get(f"{HTTP}/healthz")
results.append(check("/healthz answers on HTTP for probes", status == 200, status))

status, headers, _ = get(f"{HTTPS}/alerts")
results.append(
    check("not signed in: to the login", status == 302 and "/oauth2/start" in headers.get("Location", ""), status)
)
status, _, _ = get(f"{HTTPS}/api/alerts", FORGED)
results.append(check("forged identity headers don't sign in", status == 302, status))

status, headers, body = get(f"{HTTPS}/alerts", SIGNED_IN | FORGED)
results.append(check("signed in: the page", status == 200, status))
results.append(check("HSTS", "max-age" in headers.get("Strict-Transport-Security", ""), headers))
# The top bar (#419) names who signed in, never the forged user.
results.append(check("the top bar shows who signed in", '<span class="app-user-name">olga</span>' in body, "no olga"))
results.append(check("not the forged user", "mallory" not in body, "mallory"))
# The alert history needs the operator role: the user and groups reached the
# app from the proxy it trusts (address and AUTH_PROXY_SECRET).
status, _, _ = get(f"{HTTPS}/api/alert-history", SIGNED_IN)
results.append(check("signed in as an operator (noc-operators)", status == 200, status))

status, _, body = get(f"{HTTPS}/api/alerts", {"Authorization": "Bearer nmt_not-a-real-token"} | FORGED)
results.append(
    check("an API token skips the login; the app checks it", status == 401 and "token" in body.lower(), status)
)

status, _, _ = get(f"{HTTPS}/status/Acme%20Corp")
results.append(check("the status page needs no company login", status == 200, status))
status, _, _ = get(f"{HTTPS}/static/js/status/main.js")
results.append(check("nor its files", status == 200, status))
status, _, _ = get(f"{HTTPS}/static/js/alerts/main.js")
results.append(check("other files do", status == 302, status))
status, _, _ = get(f"{HTTPS}/api/status/Acme%20Corp", FORGED)
results.append(check("forged headers on the status API are dropped", status in (401, 403), status))

status, _, _ = get(f"{HTTPS}/metrics", SIGNED_IN)
results.append(check("/metrics is not served", status == 404, status))

sys.exit(0 if all(results) else 1)
