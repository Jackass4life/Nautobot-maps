"""A stand-in for oauth2-proxy in CI (#423): no identity provider needed.

The session cookie ``_nautobot_maps=olga`` is "signed in" as olga in the
noc-operators group (``=many``: also in 150 other groups); anything else is not.  Only what nginx uses: /oauth2/auth
(202 or 401, with the X-Auth-Request-* headers), /oauth2/start (to the login) and
/oauth2/sign_out (deletes the cookie, then to "/", where nginx's X-Auth-Request-Redirect sends oauth2-proxy, #546).
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Stub(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server's name)
        if self.path.startswith("/oauth2/auth"):
            cookie = self.headers.get("Cookie") or ""
            if "_nautobot_maps=olga" in cookie or "_nautobot_maps=many" in cookie:
                # "many": in 150 groups, as Entra ID sends them (an ID each).
                groups = ["noc-operators", "everyone"]
                if "_nautobot_maps=many" in cookie:
                    groups = [f"{i:08x}-0000-4000-8000-000000000000" for i in range(150)] + groups
                self.send_response(202)
                self.send_header("X-Auth-Request-Preferred-Username", "olga")
                self.send_header("X-Auth-Request-Email", "olga@example.com")
                self.send_header("X-Auth-Request-Groups", ",".join(groups))
            else:
                self.send_response(401)
        elif self.path.startswith("/oauth2/sign_out"):
            self.send_response(302)
            self.send_header("Set-Cookie", "_nautobot_maps=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax")
            # Fixed, never the request's header (CodeQL: response splitting);
            # tests/test_proxy_config.py checks nginx asks for "/".
            self.send_header("Location", "/")
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
