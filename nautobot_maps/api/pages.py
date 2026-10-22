"""HTML pages from FastAPI routes (#436): the templates rendered as Flask did."""

from fastapi.responses import HTMLResponse

from nautobot_maps import templating


def page(template: str, *, status_code: int = 200, headers: dict | None = None, **context) -> HTMLResponse:
    return HTMLResponse(templating.render(template, **context), status_code=status_code, headers=headers)
