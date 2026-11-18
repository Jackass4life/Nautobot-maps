"""Settings set in code by tests (tests/overrides.py, #745; #603 review)."""

import pytest

from app.core.settings import settings
from tests.overrides import overridden, override


def test_a_nested_override_undone_restores_the_outer_one(override_settings):
    override_settings(NAUTOBOT_URL="http://outer")
    with overridden(NAUTOBOT_URL="http://inner"):
        assert settings.NAUTOBOT_URL == "http://inner"
    assert settings.NAUTOBOT_URL == "http://outer"
    undo = override(NAUTOBOT_URL="http://second")
    assert settings.NAUTOBOT_URL == "http://second"
    undo()
    assert settings.NAUTOBOT_URL == "http://outer"


def test_an_override_undone_puts_back_the_environments_value(override_settings):
    default = settings.NAUTOBOT_URL
    with overridden(NAUTOBOT_URL="http://x", NAUTOBOT_TOKEN="t"):
        assert settings.NAUTOBOT_TOKEN == "t"
    assert settings.NAUTOBOT_URL == default


def test_an_unknown_setting_is_refused():
    with pytest.raises(AttributeError, match="no setting NAUTOBOT_ULR"):
        override(NAUTOBOT_ULR="x")


def test_one_object_that_every_module_sees_change(configure):
    """#754: overrides and reloads change the shared object, never replace it, so a
    module that imported it at its top sees the new values."""
    from app.core import settings as module
    from app.services import availability_service

    shared = module.settings
    with overridden(CASE_URL_TEMPLATE="https://itsm.example/{case}"):
        assert module.settings is shared
        assert availability_service.settings.CASE_URL_TEMPLATE == "https://itsm.example/{case}"
    configure(CACHE_TTL="42")
    assert module.settings is shared
    assert availability_service.settings.CACHE_TTL == 42


def test_an_override_survives_a_reload_from_the_environment(configure):
    with overridden(NAUTOBOT_URL="http://kept"):
        configure(CACHE_TTL="7")
        assert (settings.NAUTOBOT_URL, settings.CACHE_TTL) == ("http://kept", 7)
