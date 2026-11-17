"""Settings set in code by tests (tests/overrides.py, #745; #603 review)."""

import pytest

from app.core.settings import get_settings
from tests.overrides import overridden, override


def test_a_nested_override_undone_restores_the_outer_one(override_settings):
    override_settings(nautobot_url="http://outer")
    with overridden(nautobot_url="http://inner"):
        assert get_settings().nautobot_url == "http://inner"
    assert get_settings().nautobot_url == "http://outer"
    undo = override(NAUTOBOT_URL="http://second")
    assert get_settings().nautobot_url == "http://second"
    undo()
    assert get_settings().nautobot_url == "http://outer"


def test_an_override_undone_puts_back_the_environments_value(override_settings):
    default = get_settings().nautobot_url
    with overridden(nautobot_url="http://x", nautobot_token="t"):
        assert get_settings().nautobot_token == "t"
    assert get_settings().nautobot_url == default


def test_an_unknown_setting_is_refused():
    with pytest.raises(AttributeError, match="no setting NAUTOBOT_ULR"):
        override(NAUTOBOT_ULR="x")
