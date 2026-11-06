"""``settings.NAME`` as the current app's code reads its settings (#600).

The code ported from nautobot_maps/ reads ``settings.NAUTOBOT_URL``; here that is
``get_settings().nautobot_url``, read at call time (tests change settings with the
``configure`` fixture). Constants of app.utils.config (``DEVICE_LEVELS``,
``WIRELESS_CONTROLLER_KINDS``, ...) read through too.
"""

from app.utils import config


class _Settings:
    def __getattr__(self, name: str) -> object:
        field = name.lower()
        if field in config.Settings.model_fields:
            return getattr(config.get_settings(), field)
        return getattr(config, name)

    def __setattr__(self, name: str, value: object) -> None:
        """``settings.NAME = value`` (and tests' ``monkeypatch.setattr``): for every module,
        and kept across fresh settings until undone, as the current app's globals were."""
        field = name.lower()
        if field not in config.Settings.model_fields:
            raise AttributeError(f"no setting {name}")
        # pytest's monkeypatch undoes with delattr (it reads attributes statically), and so
        # does mock.patch: what each assignment replaced is saved, the last put back first,
        # so a nested override undone restores the outer one (#603 review).
        saved = self.__dict__.setdefault("_saved", {}).setdefault(field, [])
        saved.append((field in config.OVERRIDES, config.OVERRIDES.get(field)))
        config.OVERRIDES[field] = value
        setattr(config.get_settings(), field, value)

    def __delattr__(self, name: str) -> None:
        """The assignment undone: the value before it, the settings read again."""
        field = name.lower()
        saved = self.__dict__.get("_saved", {}).get(field)
        was_set, value = saved.pop() if saved else (False, None)
        if was_set:
            config.OVERRIDES[field] = value
        else:
            config.OVERRIDES.pop(field, None)
        config.get_settings.cache_clear()


settings = _Settings()
