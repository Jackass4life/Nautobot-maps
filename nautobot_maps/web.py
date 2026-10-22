"""The app's wiring (#165, #424): security headers and the CSP, error pages,
the sign-in check and the top bar's user.  The routes are in
nautobot_maps/routes/, one blueprint per area; app.py calls register().
"""

import logging
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from nautobot_maps import auth, compression, routes, settings

logger = logging.getLogger(__name__)

bp = Blueprint("web", __name__)
bp.before_app_request(auth.check_viewer)


def register(app) -> None:
    """This blueprint (its hooks first), then one per area."""
    app.register_blueprint(bp)
    for blueprint in routes.BLUEPRINTS:
        app.register_blueprint(blueprint)


@bp.app_context_processor
def top_bar_user():
    """Who is signed in, for the top bar (#419): "olga · operator", a token's
    name, or nothing with AUTH_MODE=disabled."""
    user = auth.get_current_user()
    name = user.get("token") or user.get("username") or ""
    if not user.get("is_authenticated") or not name:
        return {"bar_user": None}
    return {"bar_user": {"name": name, "role": user.get("role") or ""}}


def tile_source() -> str:
    """The CSP source for MAP_TILE_URL's host, with ``{s}`` subdomains as ``*``."""
    parsed = urlsplit(settings.MAP_TILE_URL)
    if not parsed.netloc:
        return ""  # a relative URL: same origin, covered by 'self'
    return f"{parsed.scheme}://{parsed.netloc.replace('{s}', '*')}"


def content_security_policy() -> str:
    """Only this app's own scripts and styles; images may also come from the tile server (#198).

    No inline styles (#370): an HTML injection can't restyle the page (an
    overlay with a fake link).  Markers and the error page use classes and
    CSS files; style set from script (Leaflet's own, element.style) is
    allowed by the CSP, as it isn't markup.
    """
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            f"img-src 'self' data: {tile_source()}".strip(),
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


# Registered first, so it runs last (Flask runs these in reverse): every other hook sees the plain answer.
bp.after_app_request(compression.compress)


@bp.after_app_request
def security_headers(response):
    """Browser security headers on every response (#198); a header the
    response already has is left alone."""
    response.headers.setdefault("Content-Security-Policy", content_security_policy())
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


# ---------------------------------------------------------------------------
# Persistence (PostgreSQL)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


def wants_json():
    """Return True when the client prefers a JSON response."""
    return (
        request.path.startswith("/api/")
        or request.accept_mimetypes.best_match(["application/json", "text/html"]) == "application/json"
    )


@bp.app_errorhandler(404)
def page_not_found(exc):
    if wants_json():
        return jsonify({"error": "Not found"}), 404
    return (
        render_template(
            "error.html",
            error_code=404,
            error_title="Page Not Found",
            error_message="The page you are looking for does not exist. Check the URL or head back to the map.",
        ),
        404,
    )


@bp.app_errorhandler(405)
def method_not_allowed(exc):
    if wants_json():
        return jsonify({"error": "Method not allowed"}), 405
    return (
        render_template(
            "error.html",
            error_code=405,
            error_title="Method Not Allowed",
            error_message="The HTTP method used is not allowed for this URL.",
        ),
        405,
    )


@bp.app_errorhandler(500)
def internal_server_error(exc):
    if wants_json():
        return jsonify({"error": "Internal server error"}), 500
    return (
        render_template(
            "error.html",
            error_code=500,
            error_title="Internal Server Error",
            error_message="Something went wrong on our end. Please try again later.",
        ),
        500,
    )


@bp.app_errorhandler(HTTPException)
def api_http_error(exc):
    if wants_json():
        status_code = exc.code or 500
        if status_code == 404:
            message = "Not found"
        elif status_code == 405:
            message = "Method not allowed"
        elif status_code == 500:
            message = "Internal server error"
        else:
            message = exc.description or "Request failed"
        return jsonify({"error": message}), status_code
    return exc


@bp.app_errorhandler(Exception)
def api_unhandled_error(exc):
    logger.error("Unhandled application error: %s", exc)
    if wants_json():
        return jsonify({"error": "Internal server error"}), 500
    return internal_server_error(exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Criticality override REST endpoints
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Alert lifecycle / case tracking endpoints
# ---------------------------------------------------------------------------
