"""``settings.NAME`` overrides, as tests make them (#603 review)."""

from unittest import mock

from app.utils.legacy_settings import settings


def test_a_nested_override_undone_restores_the_outer_one(monkeypatch):
    default = settings.NAUTOBOT_URL
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "http://outer")
    with mock.patch.object(settings, "NAUTOBOT_URL", "http://inner"):
        assert settings.NAUTOBOT_URL == "http://inner"
    assert settings.NAUTOBOT_URL == "http://outer"
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "http://second")
    monkeypatch.undo()
    assert settings.NAUTOBOT_URL == default
