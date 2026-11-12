"""CI only (#672): the pages through the reverse-proxy image come from the web image,
with the API's security headers and no inline scripts; the API's paths reach the API.
smoke.py checks the sign-in rules on the same stack.

    python reverse-proxy/ci/images_check.py https://localhost:443
"""

import re
import ssl
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/")
SIGNED_IN = {"Cookie": "_nautobot_maps=olga"}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data: https://*.tile.openstreetmap.org; connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
INLINE = re.compile(r"<script(?![^>]*\bsrc=)([^>]*)>", re.I)
TLS = ssl.create_default_context()
TLS.check_hostname = False
TLS.verify_mode = ssl.CERT_NONE


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=TLS), NoRedirect)


def get(path, headers=None):
    request = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        with OPENER.open(request, timeout=15) as response:
            return response.status, response.headers, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read().decode("utf-8", "replace")


results = []


def check(name, condition, detail=""):
    print(f"{'ok  ' if condition else 'FAIL'} {name}" + ("" if condition else f": {detail}"))
    results.append(bool(condition))


status, headers, page = get("/alerts", SIGNED_IN)
check("a page, signed in", status == 200, status)
check("its CSP is the API's", headers.get("Content-Security-Policy") == CSP, headers.get("Content-Security-Policy"))
check("framing refused", headers.get("X-Frame-Options") == "DENY", headers.get("X-Frame-Options"))
check("asked again every time", headers.get("Cache-Control") == "no-cache", headers.get("Cache-Control"))
inline = [m.group(0) for m in INLINE.finditer(page) if "application/json" not in m.group(1)]
check("no inline script", not inline, inline[:2])
asset = re.search(r'src="(/_nuxt/[^"]+\.js)"', page)
check("the page loads its code from /_nuxt/", asset, page[:200])
if asset:
    status, headers, _ = get(asset.group(1))
    check("the code needs no login", status == 200, status)
    check(
        "the code is cached for a year",
        "max-age=31536000" in (headers.get("Cache-Control") or ""),
        headers.get("Cache-Control"),
    )
status, headers, _ = get("/status/any-link")
check(
    "the status page: no login, no referrer",
    status == 200 and headers.get("Referrer-Policy") == "no-referrer",
    (status, headers.get("Referrer-Policy")),
)
status, _, body = get("/api/docs", SIGNED_IN)
check("/api/docs is a page (the web image's)", status == 200 and '<div id="__nuxt"' in body, status)
status, headers, body = get("/api/v1/page-config", SIGNED_IN)
check("the API's paths reach the API, with who signed in", status == 200 and "olga" in body, (status, body[:200]))
status, _, body = get("/api/v1/help/overview", SIGNED_IN)
check("the help pages are in the API image (docs context)", status == 200 and "Nautobot" in body, (status, body[:200]))
status, _, body = get("/healthz")
check("/healthz is the API's", status == 200 and '"status"' in body, (status, body[:200]))

if not all(results):
    sys.exit(1)
print("All checks passed.")
