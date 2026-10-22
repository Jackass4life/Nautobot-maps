"""A stand-in for oauth2-proxy in CI (#423): no identity provider needed.

The session cookie ``_nautobot_maps=olga`` is "signed in" as olga in the
noc-operators group; anything else is not.  Only what nginx uses: /oauth2/auth
(202 or 401, with the X-Auth-Request-* headers) and /oauth2/start (to the login).
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Stub(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server's name)
        if self.path.startswith("/oauth2/auth"):
            if "_nautobot_maps=olga" in (self.headers.get("Cookie") or ""):
                self.send_response(202)
                self.send_header("X-Auth-Request-Preferred-Username", "olga")
                self.send_header("X-Auth-Request-Email", "olga@example.com")
                self.send_header("X-Auth-Request-Groups", "noc-operators,everyone")
            else:
                self.send_response(401)
        elif self.path.startswith("/oauth2/start"):
            self.send_response(302)
            self.send_header("Location", "https://idp.example.com/authorize")
        else:
            self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 4180), Stub).serve_forever()
