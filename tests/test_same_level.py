"""One site, one level: the board, the map and the site panel agree (#316)."""

import pytest

from nautobot_maps import alerts, caching, inventory, settings
from tests import test_integration

# The demo's mock Nautobot on a real HTTP port.
mock_nautobot_server = test_integration.mock_nautobot_server


@pytest.fixture
def synced(pg_database, mock_nautobot_server, monkeypatch):
    monkeypatch.setattr(settings, "NAUTOBOT_URL", mock_nautobot_server)
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "demo-token")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    inventory.sync_nautobot(force=True)
    # The test reads the snapshot; no background sync against the mock.
    monkeypatch.setattr(inventory, "ensure_snapshot", lambda *args, **kwargs: False)
    caching.cache.clear()


def three_views():
    board = {row["id"]: row for row in alerts.cached_alert_board()["alerts"]}
    levels = alerts.build_location_alert_levels()["levels"]
    return board, levels


def test_every_site_has_the_same_level_and_count_everywhere(synced):
    board, levels = three_views()
    mapped = {loc["id"] for loc in inventory.get_locations(snapshot_only=True)}
    compared = 0
    for site_id in sorted(mapped & set(board)):
        row = board[site_id]
        detail = alerts.get_location_detail(site_id, row.get("location_type"))
        # Every row, "no data" (grey) too.
        expected = (row["alert_level"], row["down_device_count"], row["device_count"])
        marker = levels[site_id]
        assert (marker["level"], marker["down"], marker["monitored"]) == expected, site_id
        panel = detail["alert"]["level"], detail["counts"]["down"], detail["counts"]["monitored"]
        assert panel == expected, site_id
        compared += 1
    assert compared >= 9, "every mapped site"


def test_london_is_medium_everywhere_and_the_panel_says_why_one_device_is_not_counted(synced):
    board, levels = three_views()
    assert (board["loc-lon"]["alert_level"], board["loc-lon"]["device_count"]) == ("medium", 7)
    assert (levels["loc-lon"]["level"], levels["loc-lon"]["monitored"]) == ("medium", 7)
    detail = alerts.get_location_detail("loc-lon", "Data Center")
    assert detail["alert"]["level"] == "medium" and detail["counts"] == {"down": 2, "monitored": 7}
    not_monitored = {d["name"]: d["not_monitored"] for d in detail["devices"] if d["not_monitored"]}
    assert not_monitored == {"lon-oob-sw01": "no primary IP"}
    assert len(detail["devices"]) == 8, "the panel still lists every device"


def test_the_map_knows_when_a_site_went_into_alarm(synced):
    """The map's alarm list puts the newest first, like the board (#334)."""
    board, levels = three_views()
    assert board["loc-lon"]["latest_down_at"] and levels["loc-lon"]["since"] == board["loc-lon"]["latest_down_at"]
    quiet = next(site_id for site_id, row in board.items() if site_id in levels and not row.get("latest_down_at"))
    assert "since" not in levels[quiet], "nothing down: no time"


def test_excluded_statuses_count_nowhere(synced, monkeypatch):
    monkeypatch.setattr(settings, "ALERT_BOARD_EXCLUDED_DEVICE_STATUSES", {"offline"})
    board, levels = three_views()
    assert board["loc-lon"]["alert_level"] == "ok" and levels["loc-lon"]["level"] == "ok"
    detail = alerts.get_location_detail("loc-lon", "Data Center")
    assert detail["alert"]["level"] == "ok" and detail["counts"] == {"down": 0, "monitored": 5}
    reasons = {d["name"]: d["not_monitored"] for d in detail["devices"] if d["not_monitored"]}
    assert reasons["lon-acc-sw01"] == "status Offline is excluded"


def test_a_mapped_location_below_a_rolled_up_site_uses_the_same_rule(synced, monkeypatch):
    """With rows per Site, a floor below a site isn't a row: its own devices, by the board's rule."""
    from nautobot_maps import db

    conn = db.get_conn()
    try:
        with db.transaction(conn):  # put Etage 2 (below Aarhus Campus) on the map
            conn.execute(
                "UPDATE nautobot_location_cache SET latitude = 56.15, longitude = 10.2 "
                "WHERE location_id = 'loc-aar-bld-a-f2'"
            )
    finally:
        conn.close()
    monkeypatch.setattr(settings, "ALERT_BOARD_SITE_LOCATION_TYPE", "site")
    board, levels = three_views()
    assert "loc-aar-bld-a-f2" not in board and "loc-aar" in board  # rolled up into its site
    floor = levels["loc-aar-bld-a-f2"]
    assert (floor["level"], floor["down"], floor["monitored"]) == ("critical", 1, 1)


def _monitored_london_ids():
    return [
        d["id"]
        for d in inventory.read_devices()
        if d.get("location_id") == "loc-lon" and alerts.device_has_primary_ip(d)
    ]


def _window(body):
    from nautobot_maps import db, maintenance

    conn = db.get_conn()
    try:
        return maintenance.create(
            conn, {"site_id": "loc-lon", "reason": "Upgrade", "duration_minutes": 60, **body}, "olga"
        )
    finally:
        conn.close()


def test_every_device_in_maintenance_is_no_data_everywhere_and_says_why(synced):
    _window({"device_ids": _monitored_london_ids()})
    caching.cache.clear()
    board, levels = three_views()
    assert board["loc-lon"]["alert_level"] == "no_data"
    # Copied as it is, not scored again by the panel (#316).
    assert levels["loc-lon"] == {"level": "no_data", "reason": "No monitored devices", "down": 0, "monitored": 0}
    detail = alerts.get_location_detail("loc-lon", "Data Center")
    assert detail["alert"]["level"] == "no_data" and detail["counts"] == {"down": 0, "monitored": 0}
    in_window = {d["name"] for d in detail["devices"] if d.get("maintenance_until")}
    assert len(in_window) == 7 and "lon-oob-sw01" not in in_window


def test_a_site_in_maintenance_keeps_its_counts(synced):
    _window({})
    caching.cache.clear()
    _, levels = three_views()
    assert levels["loc-lon"]["level"] == "maintenance" and levels["loc-lon"]["monitored"] == 7


def test_pending_devices_count_as_monitored_also_below_a_rolled_up_site(synced, monkeypatch):
    from nautobot_maps import db, timeutil

    conn = db.get_conn()
    try:
        with db.transaction(conn):
            conn.execute(
                "UPDATE nautobot_location_cache SET latitude = 56.15, longitude = 10.2 "
                "WHERE location_id = 'loc-aar-bld-a-f2'"
            )
            # Its only device went down just now: within the alert delay.
            conn.execute(
                "UPDATE nautobot_device_cache SET last_updated = %s WHERE device_id = 'dev-aar-2'",
                (timeutil.iso_utc_now(),),
            )
    finally:
        conn.close()
    monkeypatch.setattr(settings, "ALERT_BOARD_SITE_LOCATION_TYPE", "site")
    monkeypatch.setattr(settings, "ALERT_DELAY_SECONDS", 3600)
    caching.cache.clear()
    _, levels = three_views()
    assert levels["loc-aar-bld-a-f2"] == {"level": "ok", "reason": "", "down": 0, "monitored": 1}


def test_a_pending_device_without_a_primary_ip_is_not_monitored(synced, monkeypatch):
    """Its state row can outlive its IP; the board's rule still applies."""
    from nautobot_maps import db, timeutil

    conn = db.get_conn()
    try:
        with db.transaction(conn):
            conn.execute(
                "UPDATE nautobot_location_cache SET latitude = 56.15, longitude = 10.2 "
                "WHERE location_id = 'loc-aar-bld-a-f2'"
            )
            conn.execute(
                "UPDATE nautobot_device_cache SET last_updated = %s WHERE device_id = 'dev-aar-2'",
                (timeutil.iso_utc_now(),),
            )
    finally:
        conn.close()
    monkeypatch.setattr(settings, "ALERT_BOARD_SITE_LOCATION_TYPE", "site")
    monkeypatch.setattr(settings, "ALERT_DELAY_SECONDS", 3600)
    caching.cache.clear()
    three_views()  # the board records that dev-aar-2 is pending
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            conn.execute("UPDATE nautobot_device_cache SET primary_ip = '' WHERE device_id = 'dev-aar-2'")
    finally:
        conn.close()
    caching.cache.clear()
    _, levels = three_views()
    assert "loc-aar-bld-a-f2" not in levels, "no monitored device: no data"


def test_a_stack_is_one_device_everywhere(synced):
    """Paris's stack of four: an IP on member 2 is the stack's, and not
    answering, the whole stack is down: one device down on the board, the map
    and in the panel (#405)."""
    from nautobot_maps import db

    conn = db.get_conn()
    try:
        with db.transaction(conn):
            conn.execute(
                "UPDATE nautobot_device_cache SET primary_ip = '10.0.6.9', status = 'Offline' "
                "WHERE device_id = 'dev-par-stk-2'"
            )
    finally:
        conn.close()
    caching.cache.clear()
    board, levels = three_views()
    row = board["loc-par"]
    assert (row["down_device_count"], row["device_count"]) == (1, 2), "the router and the stack"
    assert [d["device_id"] for d in row["down_devices"]] == ["dev-par-stk-1"], "the stack, by its master"
    assert row["down_devices"][0]["stack"]["whole"] is True
    assert (levels["loc-par"]["down"], levels["loc-par"]["monitored"]) == (1, 2)
    detail = alerts.get_location_detail("loc-par", row.get("location_type"))
    assert detail["counts"] == {"down": 1, "monitored": 2}
    by_id = {d["id"]: d for d in detail["devices"]}
    assert by_id["dev-par-stk-1"]["status"] == "Offline", "the panel's stack row is red"
    assert by_id["dev-par-stk-2"]["not_monitored"] == "monitored with its stack"
    assert len(detail["devices"]) == 5, "the panel still lists every member"
