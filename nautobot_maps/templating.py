"""The HTML templates (templates/, #436): Jinja with the filters they use, the
same escaping Flask had, ``url_for('static', filename=...)`` and the top bar's
signed-in user.  The pages render with ``render`` (nautobot_maps/api/pages.py).
"""

import pathlib

import jinja2

from nautobot_maps import auth, availability, navigation

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "templates"


def downtime(seconds) -> str:
    return availability.format_downtime(seconds)


def thousands(count) -> str:
    """1,200 (#342)."""
    return f"{count:,}"


FILTERS = {"downtime": downtime, "thousands": thousands}


def bar_user() -> dict | None:
    """Who is signed in, for the top bar (#419): "olga · operator", a token's
    name, or nothing with AUTH_MODE=disabled."""
    user = auth.get_current_user()
    name = user.get("token") or user.get("username") or ""
    if not user.get("is_authenticated") or not name:
        return None
    return {"name": name, "role": user.get("role") or ""}


def url_for(endpoint: str, **values) -> str:
    """The one ``url_for`` the templates use: a static file's address."""
    if endpoint != "static" or "filename" not in values:
        raise ValueError(f"url_for({endpoint!r}) isn't available in templates rendered by FastAPI")
    return "/static/" + values["filename"]


# As Flask's: .html (and the other markup) autoescaped, the same loader.
environment = jinja2.Environment(
    loader=jinja2.FileSystemLoader(TEMPLATES),
    autoescape=jinja2.select_autoescape(["html", "htm", "xml", "xhtml", "svg"]),
)
environment.filters.update(FILTERS)
environment.globals["url_for"] = url_for
environment.globals["nav_pages"] = navigation.pages


def render(template: str, **context) -> str:
    if "bar_user" not in context:
        context["bar_user"] = bar_user()
    return environment.get_template(template).render(**context)
