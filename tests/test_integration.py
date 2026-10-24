"""
Integration tests for the Nautobot Maps application.

These tests start a *real* mock-Nautobot HTTP server (uvicorn) in a background
thread, configure the Flask app to point to it, and exercise every API endpoint
end-to-end – no mocking of app internals.

Run with:
    python -m pytest tests/test_integration.py -v
"""

import json
import pathlib
import socket
import threading
import time

import mock_nautobot  # provided via tests/conftest.py path injection
import pytest
import uvicorn

from nautobot_maps import caching, inventory, settings
from tests import http_client

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_nautobot_server():
    """
    Start the mock Nautobot HTTP server on a random port.
    Yields the base URL (e.g. 'http://127.0.0.1:54321').
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mock_nautobot.app, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def integration_client(mock_nautobot_server):
    """
    Return a Flask test client for nautobot-maps, configured to talk to the
    real mock-Nautobot server started in the fixture above.
    """
    # Patch the module-level config variables so all calls go to the mock server
    settings.NAUTOBOT_URL = mock_nautobot_server
    settings.NAUTOBOT_TOKEN = "demo-token"
    caching.cache.clear()

    with http_client.FlaskStyleClient() as client:
        yield client

    # Restore to blank so other test modules don't accidentally hit the server
    settings.NAUTOBOT_URL = ""
    settings.NAUTOBOT_TOKEN = ""


# ---------------------------------------------------------------------------
# Helper – clear the cache between tests so each test gets a fresh request
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def clear_cache():
    caching.cache.clear()
    yield
    caching.cache.clear()


# ---------------------------------------------------------------------------
# 1. Map UI
# ---------------------------------------------------------------------------
class TestMapUI:
    def test_index_page_loads(self, integration_client, frontend_build):
        # Built with Nuxt (#463): the page is the build's; frontend/ and e2e/ test it.
        resp = integration_client.get("/")
        assert resp.status_code == 200 and 'id="__nuxt"' in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# 2. /api/locations – all locations with GPS coordinates
# ---------------------------------------------------------------------------
class TestLocationsEndpoint:
    def test_returns_200(self, integration_client):
        resp = integration_client.get("/api/locations")
        assert resp.status_code == 200

    def test_all_seeded_locations_returned(self, integration_client):
        """All 9 mock-Nautobot locations have GPS coords and must be returned."""
        data = integration_client.get("/api/locations").get_json()
        assert data["locations"]
        # The mock server has 9 locations, all with GPS
        assert len(data["locations"]) == 9

    def test_location_has_required_fields(self, integration_client):
        data = integration_client.get("/api/locations").get_json()
        for loc in data["locations"]:
            assert "id" in loc
            assert "name" in loc
            assert "latitude" in loc
            assert "longitude" in loc
            assert "status" in loc
            assert isinstance(loc["latitude"], float)
            assert isinstance(loc["longitude"], float)

    def test_active_location_present(self, integration_client):
        data = integration_client.get("/api/locations").get_json()
        names = [loc["name"] for loc in data["locations"]]
        assert "Copenhagen DC" in names

    def test_planned_location_present(self, integration_client):
        data = integration_client.get("/api/locations").get_json()
        statuses = {loc["name"]: loc["status"] for loc in data["locations"]}
        assert statuses.get("Oslo Office") == "Planned"

    def test_tenant_field_populated(self, integration_client):
        data = integration_client.get("/api/locations").get_json()
        cph = next(loc for loc in data["locations"] if loc["name"] == "Copenhagen DC")
        assert cph["tenant"] == "Acme Corp"

    def test_tenant_group_field_populated(self, integration_client):
        data = integration_client.get("/api/locations").get_json()
        cph = next(loc for loc in data["locations"] if loc["name"] == "Copenhagen DC")
        assert cph["tenant_group"] == "Corporate"

    def test_tenant_group_empty_when_no_group(self, integration_client):
        """Frankfurt DC has tenant DataCenter GmbH which has no tenant group."""
        data = integration_client.get("/api/locations").get_json()
        fra = next(loc for loc in data["locations"] if loc["name"] == "Frankfurt DC")
        assert fra["tenant_group"] == ""

    def test_asn_field_populated(self, integration_client):
        data = integration_client.get("/api/locations").get_json()
        cph = next(loc for loc in data["locations"] if loc["name"] == "Copenhagen DC")
        assert cph["asn"] == 65001

    def test_coordinates_are_floats(self, integration_client):
        """Nautobot returns lat/lon as strings; the app must coerce them to float."""
        data = integration_client.get("/api/locations").get_json()
        for loc in data["locations"]:
            assert isinstance(loc["latitude"], float)
            assert isinstance(loc["longitude"], float)


# ---------------------------------------------------------------------------
# 3. /api/locations/<id>/detail – devices + ASNs
# ---------------------------------------------------------------------------
class TestLocationDetailEndpoint:
    def test_returns_200_for_valid_id(self, integration_client):
        resp = integration_client.get("/api/locations/loc-cph/detail")
        assert resp.status_code == 200

    def test_devices_returned_for_copenhagen_dc(self, integration_client):
        data = integration_client.get("/api/locations/loc-cph/detail").get_json()
        assert len(data["devices"]) == 3
        device_names = [d["name"] for d in data["devices"]]
        assert "cph-core-rt01" in device_names
        assert "cph-dist-sw01" in device_names
        assert "cph-fw01" in device_names

    def test_device_fields_populated(self, integration_client):
        data = integration_client.get("/api/locations/loc-cph/detail").get_json()
        router = next(d for d in data["devices"] if d["name"] == "cph-core-rt01")
        assert router["manufacturer"] == "Cisco"
        assert router["device_type"] == "ASR1001-X"
        assert router["role"] == "Core Router"
        assert router["status"] == "Active"
        assert router["tenant"] == "Acme Corp"

    def test_every_circuit_returned_for_copenhagen_dc(self, integration_client):
        """Both circuits at the site, not just one (#235)."""
        data = integration_client.get("/api/locations/loc-cph/detail").get_json()
        assert [(c["cid"], c["status"], c["port_speed"]) for c in data["circuits"]] == [
            ("GTT-MPLS-7781", "Offline", "1 Gbps"),
            ("TEL-CPH-0001", "Active", "10 Gbps"),
        ]
        assert data["circuits_error"] == ""

    def test_no_circuits_for_stockholm(self, integration_client):
        data = integration_client.get("/api/locations/loc-sto/detail").get_json()
        assert data["circuits"] == []

    def test_asns_returned_for_copenhagen_dc(self, integration_client):
        data = integration_client.get("/api/locations/loc-cph/detail").get_json()
        assert len(data["asns"]) == 1
        assert data["asns"][0]["asn"] == 65001

    def test_multiple_asns_returned_for_london(self, integration_client):
        """London HQ has two ASNs."""
        data = integration_client.get("/api/locations/loc-lon/detail").get_json()
        asn_numbers = [a["asn"] for a in data["asns"]]
        assert 65050 in asn_numbers
        assert 65051 in asn_numbers

    def test_location_with_no_devices_returns_empty_list(self, integration_client):
        """Oslo Office has no devices."""
        data = integration_client.get("/api/locations/loc-osl/detail").get_json()
        assert data["devices"] == []

    def test_location_with_no_asns_returns_empty_list(self, integration_client):
        """Oslo Office has no ASNs."""
        data = integration_client.get("/api/locations/loc-osl/detail").get_json()
        assert data["asns"] == []

    def test_amsterdam_has_three_devices(self, integration_client):
        data = integration_client.get("/api/locations/loc-ams/detail").get_json()
        assert len(data["devices"]) == 3

    def test_london_has_eight_devices(self, integration_client):
        data = integration_client.get("/api/locations/loc-lon/detail").get_json()
        assert len(data["devices"]) == 8

    def test_london_has_offline_devices(self, integration_client):
        data = integration_client.get("/api/locations/loc-lon/detail").get_json()
        statuses = [d["status"] for d in data["devices"]]
        assert "Offline" in statuses

    def test_london_has_active_devices(self, integration_client):
        data = integration_client.get("/api/locations/loc-lon/detail").get_json()
        statuses = [d["status"] for d in data["devices"]]
        assert "Active" in statuses

    def test_london_colo_has_devices(self, integration_client):
        """London Colo shares the same coordinates as London HQ and has its own devices."""
        data = integration_client.get("/api/locations/loc-lon2/detail").get_json()
        assert len(data["devices"]) == 1
        assert data["devices"][0]["name"] == "lon2-edge-rt01"

    def test_london_colo_has_asns(self, integration_client):
        """London Colo has its own ASN."""
        data = integration_client.get("/api/locations/loc-lon2/detail").get_json()
        assert len(data["asns"]) == 1
        assert data["asns"][0]["asn"] == 65052

    def test_colocated_locations_both_in_locations_list(self, integration_client):
        """Both London HQ and London Colo appear in the locations list."""
        data = integration_client.get("/api/locations").get_json()
        names = [loc["name"] for loc in data["locations"]]
        assert "London HQ" in names
        assert "London Colo" in names

    def test_colocated_locations_have_same_coordinates(self, integration_client):
        """London HQ and London Colo share identical lat/lon."""
        data = integration_client.get("/api/locations").get_json()
        london_hq = next(loc for loc in data["locations"] if loc["name"] == "London HQ")
        london_colo = next(loc for loc in data["locations"] if loc["name"] == "London Colo")
        assert london_hq["latitude"] == london_colo["latitude"]
        assert london_hq["longitude"] == london_colo["longitude"]


# ---------------------------------------------------------------------------
# 4. /api/search – proximity search
# ---------------------------------------------------------------------------
class TestSearchEndpoint:
    def test_gps_search_finds_two_copenhagen_locations(self, integration_client):
        """
        Both Copenhagen DC (0 km) and Copenhagen Colocation (~1.2 km) are within
        5 km of coordinates 55.6761, 12.5683.
        """
        resp = integration_client.get("/api/search?q=55.6761,12.5683")
        assert resp.status_code == 200
        data = resp.get_json()
        names = [loc["name"] for loc in data["locations"]]
        assert "Copenhagen DC" in names
        assert "Copenhagen Colocation" in names
        assert data["count"] == 2

    def test_results_sorted_by_distance(self, integration_client):
        resp = integration_client.get("/api/search?q=55.6761,12.5683")
        distances = [loc["distance_km"] for loc in resp.get_json()["locations"]]
        assert distances == sorted(distances)

    def test_distance_km_is_zero_for_exact_match(self, integration_client):
        resp = integration_client.get("/api/search?q=55.6761,12.5683")
        data = resp.get_json()
        cph_dc = next(loc for loc in data["locations"] if loc["name"] == "Copenhagen DC")
        assert cph_dc["distance_km"] == pytest.approx(0.0, abs=0.01)

    def test_distant_search_returns_no_results(self, integration_client):
        """52.52, 13.40 is Berlin – no Nautobot locations within 5 km."""
        resp = integration_client.get("/api/search?q=52.52,13.40")
        data = resp.get_json()
        assert data["count"] == 0
        assert data["locations"] == []

    def test_radius_field_is_5(self, integration_client):
        resp = integration_client.get("/api/search?q=55.6761,12.5683")
        assert resp.get_json()["radius_km"] == 5

    def test_search_lat_lon_echoed_back(self, integration_client):
        resp = integration_client.get("/api/search?q=51.5074,-0.1278")
        data = resp.get_json()
        assert data["search_lat"] == pytest.approx(51.5074)
        assert data["search_lon"] == pytest.approx(-0.1278)

    def test_london_search_finds_london_hq(self, integration_client):
        resp = integration_client.get("/api/search?q=51.5074,-0.1278")
        data = resp.get_json()
        names = [loc["name"] for loc in data["locations"]]
        assert "London HQ" in names

    def test_london_search_finds_colocated_sites(self, integration_client):
        """Both London HQ and London Colo share the same coordinates."""
        resp = integration_client.get("/api/search?q=51.5074,-0.1278")
        data = resp.get_json()
        names = [loc["name"] for loc in data["locations"]]
        assert "London HQ" in names
        assert "London Colo" in names
        assert data["count"] == 2

    def test_missing_query_returns_400(self, integration_client):
        resp = integration_client.get("/api/search")
        assert resp.status_code == 400

    def test_distance_km_field_present_in_all_results(self, integration_client):
        resp = integration_client.get("/api/search?q=55.6761,12.5683")
        for loc in resp.get_json()["locations"]:
            assert "distance_km" in loc
            assert isinstance(loc["distance_km"], float)

    def test_stockholm_search_returns_only_stockholm(self, integration_client):
        """Stockholm PoP is the only location near 59.3293, 18.0686."""
        resp = integration_client.get("/api/search?q=59.3293,18.0686")
        data = resp.get_json()
        assert data["count"] == 1
        assert data["locations"][0]["name"] == "Stockholm PoP"


# ---------------------------------------------------------------------------
# 5. End-to-end scenario: open map → click location → see devices
# ---------------------------------------------------------------------------
class TestEndToEndScenario:
    def test_scenario_full_flow(self, integration_client, frontend_build):
        """
        Simulates a user:
        1. Opening the map
        2. Loading all locations
        3. Clicking Copenhagen DC to load its details
        4. Searching for nearby locations
        """
        # Step 1: Load the map page
        page_resp = integration_client.get("/")
        assert page_resp.status_code == 200

        # Step 2: Load all locations (the JS would do this via fetch)
        locs_resp = integration_client.get("/api/locations")
        assert locs_resp.status_code == 200
        locations = locs_resp.get_json()["locations"]
        cph = next(loc for loc in locations if loc["name"] == "Copenhagen DC")

        # Step 3: Click the Copenhagen DC marker → fetch details
        detail_resp = integration_client.get(f"/api/locations/{cph['id']}/detail")
        assert detail_resp.status_code == 200
        detail = detail_resp.get_json()
        assert detail["devices"]
        assert detail["asns"]

        # Step 4: Search near Copenhagen
        search_resp = integration_client.get(f"/api/search?q={cph['latitude']},{cph['longitude']}")
        assert search_resp.status_code == 200
        nearby = search_resp.get_json()
        assert nearby["count"] >= 1
        assert any(loc["name"] == "Copenhagen DC" for loc in nearby["locations"])


# ---------------------------------------------------------------------------
# 6. Alert board backed by the persisted inventory snapshot
# ---------------------------------------------------------------------------
@pytest.fixture
def persisted_integration_client(integration_client, pg_database, monkeypatch):
    """Integration client with PostgreSQL persistence and a synced inventory snapshot."""
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
    assert inventory.ensure_snapshot(force=True, wait=True)
    return integration_client


class TestAlertBoardWithPersistence:
    def _alerts_by_site(self, client):
        resp = client.get("/api/alerts")
        assert resp.status_code == 200
        return {site["id"]: site for site in resp.get_json()["alerts"]}

    def test_cached_devices_are_linked_to_their_location(self, persisted_integration_client):
        devices = inventory.read_devices()
        assert len(devices) == sum(len(devs) for devs in mock_nautobot.DEVICES.values())
        assert all(device["location_id"] for device in devices)

    def test_london_hq_alerts_on_offline_devices(self, persisted_integration_client):
        london = self._alerts_by_site(persisted_integration_client)["loc-lon"]
        assert london["alert_level"] != "ok"
        assert london["down_device_count"] == 2
        assert {d["device_name"] for d in london["down_devices"]} == {"lon-acc-sw01", "lon-acc-sw02"}

    def test_devices_without_primary_ip_are_excluded(self, persisted_integration_client):
        london = self._alerts_by_site(persisted_integration_client)["loc-lon"]
        with_ip = [
            d for d in mock_nautobot.DEVICES["loc-lon"] if d["id"] not in mock_nautobot.DEVICES_WITHOUT_PRIMARY_IP
        ]
        assert london["device_count"] == len(with_ip)

    def test_site_rollup_through_a_real_sync(self, persisted_integration_client, monkeypatch):
        """Parents come from the Nautobot API, are cached, and the board rolls up to Sites (#158)."""
        monkeypatch.setattr(settings, "ALERT_BOARD_SITE_LOCATION_TYPE", "site")
        caching.cache.clear()
        alerts = self._alerts_by_site(persisted_integration_client)
        for hidden in ("loc-emea", "loc-dnk", "loc-aar-bld-a", "loc-aar-bld-a-f2"):
            assert hidden not in alerts
        aarhus = alerts["loc-aar"]
        assert aarhus["ancestor_path"] == "EMEA › DNK"
        assert aarhus["device_count"] == 2
        assert [(d["device_name"], d["location_path"]) for d in aarhus["down_devices"]] == [
            ("aar-acc-sw01", "Bygning A › Etage 2")
        ]
        # Locations without a Site above them keep their own rows.
        assert alerts["loc-lon"]["down_device_count"] == 2

    def test_every_tenant_of_a_site_through_a_real_sync(self, persisted_integration_client):
        """Tenants linked by a Location <-> Tenant relationship are listed after its own (#238)."""
        alerts = self._alerts_by_site(persisted_integration_client)
        assert alerts["loc-lon"]["tenants"] == ["Acme Corp", "DataCenter GmbH", "EuroIX", "Nordic Net"]
        assert alerts["loc-lon"]["tenant"] == "Acme Corp"
        # Amsterdam's own tenant is EuroIX; Acme Corp is linked.
        assert alerts["loc-ams"]["tenants"] == ["EuroIX", "Acme Corp"]
        assert alerts["loc-sto"]["tenants"] == ["Acme Corp"]

    def test_tenant_descriptions_through_a_real_sync(self, persisted_integration_client):
        """The demo's tenant (i) (#263, #265): mock descriptions reach the board and the map."""
        alerts = self._alerts_by_site(persisted_integration_client)
        london = alerts["loc-lon"]["tenant_descriptions"]
        assert set(london) == {"Acme Corp", "DataCenter GmbH", "Nordic Net"}  # EuroIX has none
        assert london["Acme Corp"].startswith("Retail bank")
        # The board shows three names and "+1 more": the (i) the demo README
        # points at must be on one of the first three.
        assert [name for name in alerts["loc-lon"]["tenants"][:3] if name in london] == ["Acme Corp", "DataCenter GmbH"]
        # What /api/locations serves, read directly: the endpoint would also
        # queue a background sync that outlives this test's database schema.
        locations = {loc["id"]: loc for loc in inventory.read_locations()}
        assert set(locations["loc-cph"]["tenant_descriptions"]) == {"Acme Corp"}
        assert set(locations["loc-fra"]["tenant_descriptions"]) == {"DataCenter GmbH"}
        # The site path (#367), as on the board, so a link's ?q= finds the same sites.
        everywhere = {loc["id"]: loc for loc in inventory.read_locations(include_without_coordinates=True)}
        floor = everywhere["loc-aar-bld-a-f2"]["ancestor_path"]
        assert floor == "EMEA › DNK › Aarhus Campus › Bygning A"
        assert alerts["loc-aar-bld-a-f2"]["ancestor_path"] == floor
        assert locations["loc-cph"]["ancestor_path"] == ""

    def test_a_level_change_notifies_through_a_real_board_build(self, persisted_integration_client, monkeypatch):
        """End to end (#282): a real sync and board build queue and send a notification."""
        from nautobot_maps import alerts, db, notify, settings

        posts = []

        class Accepted:
            status_code = 202

        monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "https://hooks.example.com/x")
        monkeypatch.setattr(settings, "NOTIFY_MIN_LEVEL", "medium")
        monkeypatch.setattr(notify.requests, "post", lambda url, data=None, **kw: posts.append(data) or Accepted())
        alerts.build_alert_board_payload(snapshot_only=True)  # records every site's level
        conn = db.get_conn()
        try:
            conn.execute("UPDATE site_alert_levels SET alert_level = 'ok' WHERE site_id = 'loc-lon'")
        finally:
            conn.close()
        alerts.build_alert_board_payload(snapshot_only=True)  # London: OK -> Medium
        assert notify.send_pending() == 1
        (body,) = [json.loads(data) for data in posts]
        assert body["type"] == "alarm" and body["previous_level"] == "ok" and body["level"] == "medium"
        assert body["site"]["name"] == "London HQ" and "Nordic Net" in body["site"]["tenants"]
        assert sorted(device["name"] for device in body["down_devices"]) == ["lon-acc-sw01", "lon-acc-sw02"]

    def test_sites_with_devices_report_device_counts(self, persisted_integration_client):
        alerts = self._alerts_by_site(persisted_integration_client)
        for location_id in mock_nautobot.DEVICES:
            assert alerts[location_id]["device_count"] > 0, location_id


# ---------------------------------------------------------------------------
# 7. Alert board cold start and Refresh (#121)
# ---------------------------------------------------------------------------
class TestAlertBoardColdStart:
    def test_first_request_starts_sync_and_board_fills_in(self, integration_client, pg_database, monkeypatch):
        import time

        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")

        # Fresh database, and /alerts is the first page anyone opens.
        first = integration_client.get("/api/alerts").get_json()
        assert first["alerts"] == []
        assert first["sync_pending"] is True

        # The UI keeps polling while sync_pending is set; do the same here.
        deadline = time.monotonic() + 15
        data = first
        while data["sync_pending"] and time.monotonic() < deadline:
            time.sleep(0.2)
            data = integration_client.get("/api/alerts").get_json()

        assert data["sync_pending"] is False
        assert len(data["alerts"]) == len(mock_nautobot.LOCATIONS)
        london = next(site for site in data["alerts"] if site["id"] == "loc-lon")
        assert london["down_device_count"] == 2


# ---------------------------------------------------------------------------
# 8. One case number across several down devices (#133)
# ---------------------------------------------------------------------------
class TestMultiDeviceCaseFlow:
    def test_case_added_to_all_down_devices_at_london_hq(self, persisted_integration_client):
        client = persisted_integration_client
        board = client.get("/api/alerts").get_json()
        london = next(site for site in board["alerts"] if site["id"] == "loc-lon")
        device_ids = [device["device_id"] for device in london["down_devices"]]
        assert len(device_ids) == 2

        resp = client.post(
            "/api/alert-cases",
            json={"site_id": "loc-lon", "device_ids": device_ids, "case_number": "INC-7001"},
        )
        assert resp.status_code == 200

        board = client.get("/api/alerts").get_json()
        london = next(site for site in board["alerts"] if site["id"] == "loc-lon")
        assert london["active_cases"] == ["INC-7001"]
        assert all(device["case_numbers"] == ["INC-7001"] for device in london["down_devices"])
