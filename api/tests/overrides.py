"""Settings set in code for one test (#745): ``overridden(nautobot_url="http://x")``,
or the ``override_settings`` fixture. Each override goes into ``config.OVERRIDES``,
which ``get_settings()`` lays over the environment's settings; undone, the value
before it is put back (an outer override too) and the settings are read again."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from app.core import settings as config


def override(**fields: object) -> Callable[[], None]:
    """Set *fields* (a field name, any case) until the returned function is called."""
    saved: dict[str, tuple[bool, object]] = {}
    for name, value in fields.items():
        field = name.lower()
        if field not in config.Settings.model_fields:
            raise AttributeError(f"no setting {name}")
        saved.setdefault(field, (field in config.OVERRIDES, config.OVERRIDES.get(field)))
        config.OVERRIDES[field] = value
    config.get_settings.cache_clear()

    def undo() -> None:
        for field, (was_set, value) in saved.items():
            if was_set:
                config.OVERRIDES[field] = value
            else:
                config.OVERRIDES.pop(field, None)
        config.get_settings.cache_clear()

    return undo


@contextmanager
def overridden(**fields: object) -> Iterator[None]:
    """*fields* set inside the ``with`` block."""
    undo = override(**fields)
    try:
        yield
    finally:
        undo()
