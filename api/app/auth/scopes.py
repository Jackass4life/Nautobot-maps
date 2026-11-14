"""Scopes (#711, from the example's app/auth/decorators.py): a permission is a scope,
``part:part``, and what a role or token holds may use ``*`` for any one part
(``alerts:*``, ``*:*``). A required scope may name a path parameter,
``tenant:{tenant}:view``, filled in from the request."""

from collections.abc import Iterable, Mapping

WILDCARD = "*"


def is_scope_covered(scope: str, granted: Iterable[str]) -> bool:
    """Whether one of *granted* covers *scope*: the same number of parts, each equal
    (capitals ignored) or ``*``."""
    parts = scope.lower().split(":")
    for held in granted:
        held_parts = held.lower().split(":")
        if len(held_parts) == len(parts) and all(
            mine == theirs or theirs == WILDCARD
            for mine, theirs in zip(parts, held_parts, strict=True)
        ):
            return True
    return False


def fill(scope: str, params: Mapping[str, object]) -> str:
    """*scope* with its ``{name}`` parts from the path (#711); a name the path doesn't
    have stays as it is, so it is covered only by a wildcard or itself."""
    try:
        return scope.format_map(_Keep(params))
    except (ValueError, IndexError):
        return scope


class _Keep(dict):
    def __init__(self, params: Mapping[str, object]):
        super().__init__({key: str(value).replace(":", "") for key, value in params.items()})

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
