"""Every answer a test gets, checked against the model ``/docs`` shows for it
(#449): conftest installs ``check`` as ``deps.ANSWER_CHECK`` for every test
and fails the test on a mismatch, so the API spec can't drift from what the
routes send."""

import functools

from pydantic import TypeAdapter, ValidationError

problems: list[str] = []


@functools.cache
def _adapter(model) -> TypeAdapter:
    return TypeAdapter(model)


def model_for(route, status: int):
    """The model the spec shows for *status*: the route's ``responses`` entry,
    or its ``response_model`` for a success it doesn't list."""
    entry = route.responses.get(status) or route.responses.get(str(status)) or {}
    if "model" in entry:
        return entry["model"]
    if 200 <= status < 300:
        return route.response_model
    return None


def check(route, response) -> None:
    model = model_for(route, response.status_code)
    # A streamed answer (the exports) has no body to read here.
    if model is None or not hasattr(response, "body"):
        return
    where = f"{sorted(route.methods)} {route.path} answered {response.status_code}"
    if not (response.headers.get("content-type") or "").startswith("application/json"):
        problems.append(f"{where} with {response.headers.get('content-type')}, not JSON")
        return
    try:
        # Strict: "1" is not a number, as a client reading the spec would expect.
        _adapter(model).validate_json(response.body, strict=True)
    except ValidationError as exc:
        problems.append(f"{where}, not what the spec shows: {exc}")
