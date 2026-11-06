"""JSON request bodies as the current app reads them (nautobot_maps/api/deps.py, #592).

A route reads its body with ``json_body`` and checks its shape itself, with
its own messages (``{"error": ...}``, 400); ``body_schema`` documents it in the
spec. FastAPI's own body validation would answer 422 in another shape.
"""

import json

from fastapi import Request
from pydantic import BaseModel


async def json_body(request: Request) -> object:
    """The JSON as Flask's ``get_json(silent=True)`` read it: None unless it says it
    is JSON (``application/json`` or ``+json``) and parses."""
    mimetype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not (
        mimetype == "application/json"
        or (mimetype.startswith("application/") and mimetype.endswith("+json"))
    ):
        return None
    try:
        return json.loads(await request.body())
    except ValueError:
        return None


def body_schema(model: type[BaseModel], required: bool = True) -> dict:
    """``openapi_extra`` describing a route's JSON body with *model*."""
    return {
        "requestBody": {
            "required": required,
            "content": {"application/json": {"schema": model.model_json_schema(by_alias=True)}},
        }
    }
