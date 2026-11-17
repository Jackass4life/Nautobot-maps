"""Settings set in code by tests (tests/overrides.py, #745; #603 review)."""

import pytest

from app.core.settings import settings
from tests.overrides import overridden, override


def test_a_nested_override_undone_restores_the_outer_one(override_settings):
    override_settings(nautobot_url="http://outer")
    with overridden(nautobot_url="http://inner"):
        assert settings.nautobot_url == "http://inner"
    assert settings.nautobot_url == "http://outer"
    undo = override(NAUTOBOT_URL="http://second")
    assert settings.nautobot_url == "http://second"
    undo()
    assert settings.nautobot_url == "http://outer"


def test_an_override_undone_puts_back_the_environments_value(override_settings):
    default = settings.nautobot_url
    with overridden(nautobot_url="http://x", nautobot_token="t"):
        assert settings.nautobot_token == "t"
    assert settings.nautobot_url == default


def test_an_unknown_setting_is_refused():
    with pytest.raises(AttributeError, match="no setting NAUTOBOT_ULR"):
        override(NAUTOBOT_ULR="x")


def test_one_object_that_every_module_sees_change(configure):
    """#754: overrides and reloads change the shared object, never replace it, so a
    module that imported it at its top sees the new values."""
    from app.core import settings as module
    from app.services import availability_service

    shared = module.settings
    with overridden(case_url_template="https://itsm.example/{case}"):
        assert module.settings is shared
        assert availability_service.settings.case_url_template == "https://itsm.example/{case}"
    configure(CACHE_TTL="42")
    assert module.settings is shared
    assert availability_service.settings.cache_ttl == 42


def test_an_override_survives_a_reload_from_the_environment(configure):
    with overridden(nautobot_url="http://kept"):
        configure(CACHE_TTL="7")
        assert (settings.nautobot_url, settings.cache_ttl) == ("http://kept", 7)
