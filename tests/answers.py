"""Every answer a test gets, checked against the model ``/docs`` shows for it
(#449): conftest installs ``check`` as ``deps.ANSWER_CHECK`` for every test
and fails the test on a mismatch, so the API spec can't drift from what the
routes send."""

import functools
import types
import typing

from pydantic import BaseModel, TypeAdapter, ValidationError

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


def _neutral(annotation):
    """An empty value of *annotation*'s type: None when it may be null."""
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        if type(None) in args:
            return None
        return _neutral(args[0])
    if origin is typing.Literal:
        return args[0]
    if origin in (list, dict):
        return origin()
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return complete(annotation, {})
    return {str: "", int: 0, float: 0.0, bool: False}.get(annotation)


def complete(model, data: dict) -> dict:
    """*data* (a test's stand-in for what the app reads or builds) with every
    field *model* requires that it leaves out, as an empty value, nested
    models and lists of them too: so the answer the route makes from it is
    one /docs describes."""
    result = dict(data)
    for name, field in model.model_fields.items():
        key = field.alias or name
        annotation = field.annotation
        if key not in result:
            if field.is_required():
                result[key] = _neutral(annotation)
            continue
        value = result[key]
        target = annotation
        if typing.get_origin(annotation) in (typing.Union, types.UnionType):
            target = next(a for a in typing.get_args(annotation) if a is not type(None))
        if isinstance(value, dict) and isinstance(target, type) and issubclass(target, BaseModel):
            result[key] = complete(target, value)
        elif isinstance(value, list) and typing.get_origin(target) is list:
            item = typing.get_args(target)[0]
            if isinstance(item, type) and issubclass(item, BaseModel):
                result[key] = [complete(item, v) if isinstance(v, dict) else v for v in value]
    return result
