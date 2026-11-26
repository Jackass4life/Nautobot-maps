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


def test_one_object_that_every_module_sees_change(override_settings):
    """#754: overrides and reloads change the shared object, never replace it, so a
    module that imported it at its top sees the new values."""
    from app.core import settings as module
    from app.services import availability_service

    shared = module.settings
    with overridden(CASE_URL_TEMPLATE="https://itsm.example/{case}"):
        assert module.settings is shared
        assert availability_service.settings.CASE_URL_TEMPLATE == "https://itsm.example/{case}"
    override_settings(CACHE_TTL="42")
    assert module.settings is shared
    assert availability_service.settings.CACHE_TTL == 42


def test_an_override_survives_a_reload_from_the_environment(override_settings):
    with overridden(NAUTOBOT_URL="http://kept"):
        override_settings(CACHE_TTL="7")
        assert (settings.NAUTOBOT_URL, settings.CACHE_TTL) == ("http://kept", 7)


def test_read_as_env_is_and_refused_like_a_bad_env(override_settings):
    """#780: text parsed, derived settings worked out again, an unsafe combination
    refused as at startup, and then nothing of it left behind."""
    override_settings(AUTH_REQUIRE_VIEWER="true", NAUTOBOT_MAPS_DATABASE_URL="postgresql://h/x")
    assert settings.AUTH_REQUIRE_VIEWER is True
    assert settings.DATABASE_URL == "postgresql+asyncpg://h/x"
    with pytest.raises(RuntimeError, match="at least 32"):
        override_settings(AD_HOST="dc1", JWT_SIGNING_SECRET="short")
    assert settings.AD_HOST == "" and settings.DATABASE_URL == "postgresql+asyncpg://h/x"
    override_settings(CACHE_TTL="7")  # the next override isn't refused for it
    assert settings.CACHE_TTL == 7
