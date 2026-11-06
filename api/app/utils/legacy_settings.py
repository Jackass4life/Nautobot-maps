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
        """Tests' ``monkeypatch.setattr(settings, "NAME", value)``: on the settings every
        module reads (``get_settings()``), until the next test's fresh settings."""
        field = name.lower()
        if field not in config.Settings.model_fields:
            raise AttributeError(f"no setting {name}")
        current = config.get_settings()
        # pytest's monkeypatch undoes with delattr (it reads attributes statically):
        # keep what was there to put back.
        originals = self.__dict__.setdefault("_originals", {})
        originals.setdefault(field, (current, getattr(current, field)))
        setattr(current, field, value)

    def __delattr__(self, name: str) -> None:
        field = name.lower()
        current, value = self.__dict__.get("_originals", {}).pop(field, (None, None))
        if current is not None and current is config.get_settings():
            setattr(current, field, value)


settings = _Settings()
