"""The error page (templates/error.html, #436): the one page the server still
renders, so a 401, 403, 404 or 500 reads well even without a frontend build;
Jinja with Flask's escaping and ``url_for('static', filename=...)``.  Also who
is signed in, for the top bar (#419, ``/api/page-config``).
"""

import pathlib

import jinja2

from nautobot_maps import auth

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "templates"


def bar_user() -> dict | None:
    """Who is signed in, for the top bar (#419): "olga · operator", a token's
    name, or nothing with AUTH_MODE=disabled."""
    user = auth.get_current_user()
    name = user.get("token") or user.get("username") or ""
    if not user.get("is_authenticated") or not name:
        return None
    return {"name": name, "role": user.get("role") or ""}


def url_for(endpoint: str, **values) -> str:
    """The one ``url_for`` the template uses: a static file's address."""
    if endpoint != "static" or "filename" not in values:
        raise ValueError(f"url_for({endpoint!r}) isn't available in templates rendered by FastAPI")
    return "/static/" + values["filename"]


# As Flask's: .html (and the other markup) autoescaped, the same loader.
environment = jinja2.Environment(
    loader=jinja2.FileSystemLoader(TEMPLATES),
    autoescape=jinja2.select_autoescape(["html", "htm", "xml", "xhtml", "svg"]),
)
environment.globals["url_for"] = url_for


def render(template: str, **context) -> str:
    return environment.get_template(template).render(**context)
