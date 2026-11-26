"""Criticality settings (#786, from test_app.py): critical role keywords, the
criticality rules and the override endpoints."""

import asyncio

import pytest

from app.services import alerts_service as alerts
from tests import db as adb
from tests.fakes import auth_config


# ---------------------------------------------------------------------------
# Tests: configurable critical role keywords
# ---------------------------------------------------------------------------
class TestConfigurableCriticalKeywords:
    """Tests for the _get_critical_keywords / compute_alert_level helpers."""

    def test_default_keywords_applied(self, override_settings):
        """Without any configuration the built-in defaults are used."""
        override_settings(CRITICAL_ROLE_KEYWORDS="", CRITICALITY_RULES={})
        kw = alerts.get_critical_keywords()
        assert "core" in kw
        assert "router" in kw

    def test_env_override_replaces_defaults(self, override_settings):
        """CRITICAL_ROLE_KEYWORDS replaces defaults when no JSON rules."""
        override_settings(CRITICAL_ROLE_KEYWORDS="Firewall, border", CRITICALITY_RULES={})
        kw = alerts.get_critical_keywords()
        assert kw == ("firewall", "border")

    def test_env_override_used_as_fallback_for_unknown_type(self, override_settings):
        """When rules have no matching type and no 'default' key, env override is used."""
        override_settings(
            CRITICAL_ROLE_KEYWORDS="firewall", CRITICALITY_RULES={"datacenter": ["core", "spine"]}
        )
        kw = alerts.get_critical_keywords("office")
        assert kw == ("firewall",)

    def test_location_type_rule_matched(self, override_settings):
        """The exact location_type key is returned when present in rules."""
        override_settings(
            CRITICALITY_RULES={
                "datacenter": ["core", "firewall"],
                "office": ["router"],
            }
        )
        kw = alerts.get_critical_keywords("Datacenter")
        assert "firewall" in kw

    def test_rules_default_key_used_for_unknown_type(self, override_settings):
        """The 'default' key in rules is the fallback for unknown location types."""
        override_settings(
            CRITICALITY_RULES={
                "default": ["core", "spine"],
                "office": ["router"],
            }
        )
        kw = alerts.get_critical_keywords("warehouse")
        assert kw == ("core", "spine")

    def test_compute_alert_level_respects_location_type(self, override_settings):
        """compute_alert_level uses the correct keyword set for the given location type."""
        override_settings(
            CRITICALITY_RULES={
                "office": ["router"],
                "datacenter": ["core", "firewall"],
            }
        )
        # A "firewall" device offline in a datacenter → critical
        dc_devices = [{"id": "d1", "name": "fw01", "role": "Firewall", "status": "offline"}]
        result = alerts.compute_alert_level(dc_devices, location_type="datacenter")
        assert result["level"] == "critical"

        # Same device in an office (only "router" is critical there) → medium (if >25%) or ok
        office_devices = [
            {"id": "d1", "name": "fw01", "role": "Firewall", "status": "offline"},
            {"id": "d2", "name": "sw01", "role": "Switch", "status": "active"},
        ]
        result = alerts.compute_alert_level(office_devices, location_type="office")
        assert result["level"] != "critical"

    def test_compute_alert_level_no_devices(self):
        assert alerts.compute_alert_level([]) == {
            "level": "no_data",
            "reason": "No monitored devices",
        }

    def test_compute_alert_level_medium_threshold(self):
        """More than 25% of devices down → medium alert."""
        devices = [
            {"id": "d1", "name": "sw01", "role": "Switch", "status": "offline"},
            {"id": "d2", "name": "sw02", "role": "Switch", "status": "active"},
            {"id": "d3", "name": "sw03", "role": "Switch", "status": "active"},
        ]
        # 1/3 ≈ 33% > 25% → medium
        result = alerts.compute_alert_level(devices)
        assert result["level"] == "medium"

    def test_compute_alert_level_low_when_below_threshold(self):
        """Under 25% down and no core device down → ok."""
        devices = [
            {"id": "d1", "name": "sw01", "role": "Switch", "status": "offline"},
            {"id": "d2", "name": "sw02", "role": "Switch", "status": "active"},
            {"id": "d3", "name": "sw03", "role": "Switch", "status": "active"},
            {"id": "d4", "name": "sw04", "role": "Switch", "status": "active"},
            {"id": "d5", "name": "sw05", "role": "Switch", "status": "active"},
        ]
        # 1/5 = 20% ≤ 25% → ok
        result = alerts.compute_alert_level(devices)
        assert result == {"level": "low", "reason": "1/5 devices offline (20%)"}


# ---------------------------------------------------------------------------
# Tests: criticality override REST endpoints
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("allow_unauthenticated_writes")
class TestCriticalityOverrideEndpoints:
    """Tests for /api/criticality-overrides (requires the persistence database)."""

    def test_list_empty(self, client):
        resp = client.get("/api/v1/criticality-overrides")
        assert resp.status_code == 200
        assert resp.json()["overrides"] == []

    def test_create_override(self, client):
        resp = client.post(
            "/api/v1/criticality-overrides",
            json={
                "nautobot_device_id": "dev-abc",
                "is_critical": False,
                "reason": "Local firewall",
                "updated_by": "admin",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["nautobot_device_id"] == "dev-abc"
        assert data["is_critical"] is False

    def test_list_after_create(self, client):
        client.post(
            "/api/v1/criticality-overrides",
            json={
                "nautobot_device_id": "dev-abc",
                "is_critical": True,
                "reason": "Core router",
                "updated_by": "admin",
            },
        )
        resp = client.get("/api/v1/criticality-overrides")
        overrides = resp.json()["overrides"]
        assert len(overrides) == 1
        assert overrides[0]["nautobot_device_id"] == "dev-abc"

    def test_update_override(self, client):
        """Posting the same device_id a second time updates in-place."""
        client.post(
            "/api/v1/criticality-overrides",
            json={"nautobot_device_id": "dev-x", "is_critical": True},
        )
        client.post(
            "/api/v1/criticality-overrides",
            json={"nautobot_device_id": "dev-x", "is_critical": False, "reason": "Changed"},
        )
        resp = client.get("/api/v1/criticality-overrides")
        overrides = resp.json()["overrides"]
        assert len(overrides) == 1
        assert overrides[0]["is_critical"] == 0  # stored as int

    def test_delete_override(self, client):
        client.post(
            "/api/v1/criticality-overrides",
            json={"nautobot_device_id": "dev-del", "is_critical": True},
        )
        resp = client.delete("/api/v1/criticality-overrides/dev-del")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        # Should be gone now
        resp2 = client.get("/api/v1/criticality-overrides")
        assert resp2.json()["overrides"] == []

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/v1/criticality-overrides/does-not-exist")
        assert resp.status_code == 404

    def test_create_missing_device_id_returns_400(self, client):
        resp = client.post(
            "/api/v1/criticality-overrides",
            json={"is_critical": True},
        )
        assert resp.status_code == 400

    def test_override_affects_compute_alert_level(self):
        """A device marked is_critical=False must not trigger a critical alert."""
        adb.sql(
            "INSERT INTO device_criticality_override "
            "(nautobot_device_id, is_critical, reason, updated_by) "
            "VALUES (%s, %s, %s, %s)",
            ("dev-fw", 0, "Local firewall – not critical", "test"),
        )

        devices = [
            {"id": "dev-fw", "name": "fw-local", "role": "Core Router", "status": "offline"},
            # A healthy second device, so "every device down" (also Critical) does not apply.
            {"id": "dev-sw", "name": "sw-local", "role": "Switch", "status": "active"},
        ]
        # The override says is_critical=False, so even a "Core Router" that's
        # offline should not produce a critical alert.
        result = alerts.compute_alert_level(
            devices, override_map=asyncio.run(alerts.criticality_overrides(devices))
        )
        assert result["level"] != "critical"

    def test_requires_operator_role_when_auth_enabled(self, client):
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            resp = client.post(
                "/api/v1/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-viewers"},
            )
        assert resp.status_code == 403
        assert resp.json()["required_role"] == "operator"

    def test_accepts_operator_group_when_auth_enabled(self, client):
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            resp = client.post(
                "/api/v1/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
        assert resp.status_code == 200

    def test_uses_authenticated_username_as_updated_by(self, client):
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            resp = client.post(
                "/api/v1/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
            assert resp.status_code == 200
            listed = client.get(
                "/api/v1/criticality-overrides",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
        assert listed.status_code == 200
        assert listed.json()["overrides"][0]["updated_by"] == "alice"


# ---------------------------------------------------------------------------
# Tests: criticality_rules.json loading
# ---------------------------------------------------------------------------
class TestCriticalityRules:
    def test_rules_from_the_setting(self, override_settings):
        """CRITICALITY_RULES (#739, JSON) gives each location type its keywords, read at
        call time."""
        override_settings(
            CRITICALITY_RULES='{"Datacenter": ["Core", "firewall"], "office": ["router"]}'
        )
        assert alerts.get_critical_keywords("datacenter") == ("core", "firewall")
        assert alerts.get_critical_keywords("Office") == ("router",)
