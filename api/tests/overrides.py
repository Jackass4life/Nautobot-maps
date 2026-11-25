"""Settings set in code for one test (#745, #754, #780): ``overridden(NAUTOBOT_URL="http://x")``,
or the ``override_settings`` fixture. Overrides are read as ``.env`` is (#780): text parsed
(``"true"``, lists, JSON), values given in code as they are, derived settings worked out
again and an unsafe combination refused as at startup; the environment isn't touched.
They are kept in ``ACTIVE`` and laid over the environment on every ``reload()``; undone, the
value before is put back (an outer override too)."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from app.core.settings import Settings, settings

# The overrides in force, laid over the environment's values on every reload().
ACTIVE: dict[str, object] = {}


def reload(**kwargs: object) -> None:
    """Read the settings from the environment again, into the same object, with the
    overrides in force on top (*kwargs* go to ``Settings``, e.g. ``_env_file=None``)."""
    fresh = Settings(**{**ACTIVE, **kwargs})  # type: ignore[arg-type]
    for field in Settings.model_fields:
        setattr(settings, field, getattr(fresh, field))


def override(**fields: object) -> Callable[[], None]:
    """Set *fields* (by their environment names, #760) until the returned function is
    called. Refused like a bad ``.env`` (the settings as they were) when they can't be."""
    saved: dict[str, tuple[bool, object]] = {}
    for field, value in fields.items():
        if field not in Settings.model_fields:
            raise AttributeError(f"no setting {field}")
        saved.setdefault(field, (field in ACTIVE, ACTIVE.get(field)))
        ACTIVE[field] = value

    def undo() -> None:
        for field, (was_set, value) in saved.items():
            if was_set:
                ACTIVE[field] = value
            else:
                ACTIVE.pop(field, None)
        # The environment's value where no override is left (it may have changed since).
        reload()

    try:
        reload()
    except Exception:
        undo()
        raise
    return undo


@contextmanager
def overridden(**fields: object) -> Iterator[None]:
    """*fields* set inside the ``with`` block."""
    undo = override(**fields)
    try:
        yield
    finally:
        undo()
