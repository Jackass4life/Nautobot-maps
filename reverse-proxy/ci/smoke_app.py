"""Checks the proxy's rules with the app's own sign-in end to end (#723): nginx built
with SIGN_IN=app (reverse-proxy/compose.app-sign-in.yml), no oauth2-proxy.

    SESSION=<the app's sign-in token for olga> python reverse-proxy/ci/smoke_app.py \
        https://localhost:443 http://localhost:80

olga must have an identity with the operator role; the domain controller in AD_HOST
needn't answer (CI has none, nor sit_core: a sign-in then answers 503, which shows it
reached the app).  The AD sign-in itself: api/tests/test_ad_live.py.
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.request

HTTPS, HTTP = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/")
SIGNED_IN = {"Cookie": f"nm_session={os.environ['SESSION']}"}
# What a client might send to claim someone else: never counts, nginx empties them.
FORGED = {"X-Forwarded-User": "mallory", "X-Forwarded-Groups": "noc-operators,nautobot-admins"}
UNVERIFIED = ssl.create_default_context()
UNVERIFIED.check_hostname = False
UNVERIFIED.verify_mode = ssl.CERT_NONE


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


OPENER = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=UNVERIFIED))


def _lower(headers):
    """Header names in lowercase: servers send either."""
    return {name.lower(): value for name, value in headers.items()}


def call(url, headers=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={**(headers or {}), **({"Content-Type": "application/json"} if data else {})},
        method="POST" if data else "GET",
    )
    try:
        with OPENER.open(request, timeout=20) as response:
            return response.status, _lower(response.headers), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, _lower(error.headers), error.read().decode("utf-8", "replace")


def check(name, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + name + (f": {detail}" if not condition else ""))
    return condition


def as_json(body):
    try:
        return json.loads(body)
    except ValueError:
        return {}


results = []
status, headers, _ = call(f"{HTTP}/alerts")
results.append(
    check("HTTP goes to HTTPS", status == 301 and headers.get("location", "").startswith("https://"), status)
)
status, _, _ = call(f"{HTTP}/healthz")
results.append(check("/healthz answers on HTTP for probes", status == 200, status))

# No oauth2-proxy: a page without sign-in is the web image's page (its script asks the
# API, which says where to sign in), or, where the API serves the pages itself, the
# API's way to /auth.
status, headers, _ = call(f"{HTTPS}/alerts")
results.append(
    check(
        "a page without sign-in: the page or /auth, no login redirect",
        status == 200 or (status == 303 and headers.get("location", "").startswith("/auth?redirect=")),
        (status, headers.get("location")),
    )
)
results.append(check("HSTS", "max-age" in headers.get("strict-transport-security", ""), headers))
status, _, body = call(f"{HTTPS}/api/v1/page-config", FORGED)
results.append(
    check(
        "forged identity headers don't sign in; the API says where to",
        status == 401 and as_json(body).get("sign_in") == "/auth",
        (status, body[:200]),
    )
)
status, _, body = call(f"{HTTPS}/auth")
results.append(check("the sign-in page", status == 200 and "<html" in body.lower(), status))
status, headers, _ = call(f"{HTTPS}/oauth2/start")
results.append(check("no oauth2-proxy behind /oauth2/", status != 302 and "location" not in headers, (status, headers)))
status, _, body = call(f"{HTTPS}/api/v1/auth/login", body={"username": "olga", "password": "wrong"})
results.append(
    check(
        "the sign-in reaches the app without signing in first (no directory in CI: 503)",
        status == 503,
        (status, body[:200]),
    )
)

# Signed in: the app's cookie passes through, the forged headers still don't count.
status, _, body = call(f"{HTTPS}/api/v1/page-config", SIGNED_IN | FORGED)
config = as_json(body)
user = config.get("user") or {}
results.append(check("signed in with the app's cookie", status == 200 and user.get("name") == "olga", (status, user)))
results.append(check("not the forged user", "mallory" not in body, "mallory"))
results.append(
    check("the identity's role", (config.get("access") or {}).get("roles") == ["operator"], config.get("access"))
)
results.append(
    check("Sign out is the app's own", user.get("sign_out_url") == "/auth?signed_out=1", user.get("sign_out_url"))
)
status, _, _ = call(f"{HTTPS}/api/v1/alert-history", SIGNED_IN)
results.append(check("an operator's route", status == 200, status))

print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
