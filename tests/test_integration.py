"""
Integration tests for the Nautobot Maps application.

These tests start a *real* mock-Nautobot HTTP server (werkzeug) in a background
thread, configure the Flask app to point to it, and exercise every API endpoint
end-to-end – no mocking of app internals.

Run with:
    python -m pytest tests/test_integration.py -v
"""

import json
import pathlib
import re
import threading

import mock_nautobot  # provided via tests/conftest.py path injection
import pytest
from werkzeug.serving import make_server

import app as flask_app
from nautobot_maps import caching, inventory, settings
from tests.js_modules import js_source, run_js

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
    server = make_server("127.0.0.1", 0, mock_nautobot.app)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


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

    flask_app.app.config["TESTING"] = True

    with flask_app.app.test_client() as client:
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
    def test_index_page_loads(self, integration_client):
        resp = integration_client.get("/")
        assert resp.status_code == 200

    def test_index_contains_leaflet_reference(self, integration_client):
        resp = integration_client.get("/")
        assert b"leaflet" in resp.data.lower()

    def test_index_contains_search_input(self, integration_client):
        resp = integration_client.get("/")
        assert b'id="search-input"' in resp.data

    def test_static_css_served(self, integration_client):
        resp = integration_client.get("/static/css/map.css")
        assert resp.status_code == 200

    def test_static_js_served(self, integration_client):
        resp = integration_client.get("/static/js/map/main.js")
        assert resp.status_code == 200

    def test_js_contains_location_inspector_flow(self, integration_client):
        """The site panel module has the persistent location inspector logic."""
        resp = integration_client.get("/static/js/map/inspector.js")
        js = resp.data.decode()
        assert "openInspectorForLocation" in js
        assert "loadLocationDetail" in js
        assert "detailCache" in js
        assert "inspector-device-search" in js
        panel = integration_client.get("/static/js/map/panel.js").data.decode()
        assert "Loading alert status…" in panel

    def test_css_contains_inspector_layout(self, integration_client):
        """The CSS defines the responsive inspector drawer/bottom-sheet layout."""
        resp = integration_client.get("/static/css/map.css")
        css = resp.data.decode()
        assert "#location-inspector" in css
        assert ".inspector-site-tab" in css
        assert ".inspector-device-search" in css
        assert "bottom: 0;" in css

    def test_js_runtime_helpers_cover_resize_and_keyboard_access(self):
        """Inspector helper runtime behavior should defer resize and wire keyboard activation."""
        run_js("""
import { scheduleMapResize } from "@js/map/inspector.js";
import { wireMarkerAccessibility } from "@js/map/markers.js";
import { state } from "@js/map/state.js";

const rafCallbacks = [];
setGlobal("window", { requestAnimationFrame(callback) { rafCallbacks.push(callback); return rafCallbacks.length; } });
let invalidations = 0;
state.map = { invalidateSize() { invalidations += 1; }, getContainer() { return { id: "map" }; } };
scheduleMapResize();
check(rafCallbacks.length === 1, `expected one queued animation frame, got ${rafCallbacks.length}`);
state.map = {
  invalidateSize() { throw new Error("resize should use the originally scheduled map instance"); },
  getContainer() { return { id: "replacement-map" }; },
};
rafCallbacks.shift()();
check(invalidations === 0 && rafCallbacks.length === 1, "resize should wait for the second animation frame");
rafCallbacks.shift()();
check(invalidations === 1, `expected one resize invalidation, got ${invalidations}`);
state.map = {
  invalidateSize() { throw new Error("guard should skip resize when no container is available"); },
  getContainer() { return null; },
};
scheduleMapResize();
check(rafCallbacks.length === 0, "guarded resize should not queue animation frames");

const listeners = {};
const el = element("marker", { addEventListener(name, handler) { listeners[name] = handler; } });
let activations = 0;
const marker = {
  on(name, handler) { if (name === "add") this._onAdd = handler; },
  getElement() { return el; },
};
wireMarkerAccessibility(marker, "Zoom to 3 clustered locations", () => { activations += 1; });
marker._onAdd();
check(el.attrs.role === "button" && el.attrs.tabindex === "0" && el.attrs["aria-label"] === "Zoom to 3 clustered locations",
  "marker accessibility attributes were not applied");
let prevented = false;
listeners.keydown({ key: "Enter", preventDefault() { prevented = true; } });
listeners.keydown({ key: " ", preventDefault() { prevented = true; } });
listeners.keydown({ key: "Escape", preventDefault() { throw new Error("non-activation keys should not be prevented"); } });
check(prevented && activations === 2, `expected two keyboard activations, got ${activations}`);
""")


class TestAlertBoardUI:
    def _css_rules(self, css, selector):
        import re

        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        pattern = re.compile(r"([^{}]+)\{([^}]*)\}")
        return [
            body
            for selectors, body in pattern.findall(css)
            if selector in [part.strip() for part in selectors.split(",")]
        ]

    def test_table_shell_never_clips_overflow(self, integration_client):
        """The alert table must scroll, not clip, so the Action column stays reachable."""
        css = integration_client.get("/static/css/alerts.css").get_data(as_text=True)
        rules = self._css_rules(css, ".table-shell")
        assert rules
        assert any("overflow-x: auto" in body for body in rules)
        assert not any("overflow: hidden" in body for body in rules)

    def test_action_column_is_pinned(self, integration_client):
        """The last (Action) column sticks to the right edge while the table scrolls."""
        css = integration_client.get("/static/css/alerts.css").get_data(as_text=True)
        rules = self._css_rules(css, "thead th:last-child")
        assert any("position: sticky" in body and "right: 0" in body for body in rules)
        page = integration_client.get("/alerts").get_data(as_text=True)
        # No visible heading: the buttons say what they do (#311).
        assert page.rstrip().count('<th class="col-action"><span class="visually-hidden">Actions</span></th>') == 1


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
    def test_scenario_full_flow(self, integration_client):
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

    def test_refresh_button_sends_a_value_the_server_accepts(self, integration_client):
        js = integration_client.get("/static/js/alerts/board.js").get_data(as_text=True)
        assert 'params.set("refresh", "1")' in js
        assert "persistence_configured === false" in js
        # #121: a timestamp as the refresh value was silently ignored.  Date.now()
        # itself is fine elsewhere (the next-update countdown uses it).
        assert not re.search(r"refresh[\"'`]?\s*[,=:]\s*[\"'`]?\$?\{?\s*Date\.now", js)
        assert "sync_pending" in js


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


class TestAlertBoardCountdown:
    """Runtime checks of the next-update countdown on the board (#152)."""

    def test_countdown_formats_and_triggers_one_background_update(self):
        run_js("""
import { formatCountdown } from "@js/lib/format.js";
import { renderNextUpdate, setNextUpdate } from "@js/alerts/board.js";
import { els, state } from "@js/alerts/state.js";

// The board's reloads are counted, and never answered.
const loads = [];
globalThis.fetch = (url) => { loads.push(url); return new Promise(() => {}); };
els.refreshBtn = element("refresh");
els.nextUpdateEl = element("next-update", { hidden: true });
const nextUpdateEl = els.nextUpdateEl;

check(formatCountdown(0) === "0:00", formatCountdown(0));
check(formatCountdown(59.2) === "1:00", formatCountdown(59.2));
check(formatCountdown(222) === "3:42", formatCountdown(222));
check(formatCountdown(3725) === "1:02:05", formatCountdown(3725));
check(formatCountdown(-5) === "0:00", formatCountdown(-5));

// Counting down.
setNextUpdate({ next_update_in_seconds: 222 }, 1000000);
check(!nextUpdateEl.hidden && nextUpdateEl.textContent === "Next update in 3:42", nextUpdateEl.textContent);
renderNextUpdate(1000000 + 22000);
check(nextUpdateEl.textContent === "Next update in 3:20", nextUpdateEl.textContent);
check(loads.length === 0, "no update before zero");

// At zero: exactly one background reload (no refresh), then none until the gap has passed.
renderNextUpdate(1000000 + 222000);
check(nextUpdateEl.textContent === "Updating…", nextUpdateEl.textContent);
check(loads.length === 1 && loads[0] === "/api/alerts", JSON.stringify(loads));
check(!els.refreshBtn.disabled, "a background reload leaves the Refresh button alone");
renderNextUpdate(1000000 + 223000);
renderNextUpdate(1000000 + 240000);
check(loads.length === 1, "reloaded again inside the gap");
renderNextUpdate(1000000 + 252001);
check(loads.length === 2, "should retry after the gap");

// Not while the operator's own load runs.
els.refreshBtn.disabled = true;
renderNextUpdate(1000000 + 400000);
check(loads.length === 2, "must not reload while a load runs");
els.refreshBtn.disabled = false;

// A running sync shows Updating…; an unknown schedule hides the countdown.
state.latestPayload = { sync_pending: true };
setNextUpdate({ next_update_in_seconds: null }, 2000000);
check(!nextUpdateEl.hidden && nextUpdateEl.textContent === "Updating…", "sync pending");
state.latestPayload = { sync_pending: false };
renderNextUpdate(2000000);
check(nextUpdateEl.hidden, "hidden when unknown");
""")

    def test_board_template_has_countdown_element(self, integration_client):
        html = integration_client.get("/alerts").data.decode()
        assert 'id="next-update"' in html


class TestSiteMetaInTheBrowser:
    """The grey line under a site shows only the path above it (#178)."""

    def test_the_row_shows_only_the_path(self):
        """Not "LATAM › BRA · Active · Site": the row uses siteMeta alone (#269)."""
        run_js("""
import { siteRowsHtml } from "@js/alerts/rows.js";
const site = { id: "s1", name: "Rio", ancestor_path: "LATAM › BRA", status: "Active", location_type: "Site Type X" };
const html = siteRowsHtml([site], { wallView: false, isExpanded: () => false });
check(html.includes('<div class="site-meta">LATAM › BRA</div>'), html);
check(!html.includes("Active") && !html.includes("Site Type X"), "status/type are not shown on the row: " + html);
""")

    def test_site_meta_is_only_the_path(self):
        run_js("""
import { siteMeta } from "@js/alerts/rows.js";
const site = { ancestor_path: "EMEA › DNK", parent: "DNK", country: "Denmark", tenant: "Acme", tenant_group: "JM" };
let meta = siteMeta(site);
check(meta === "EMEA › DNK", meta);
meta = siteMeta({ parent: "R&D", country: "Denmark", tenant: "Acme" });
check(meta === "R&amp;D", meta);
check(siteMeta({ tenant: "Acme" }) === "", "empty without a path");
""")


class TestCompactActionsInTheBrowser:
    """One line of small buttons per row; the case form is in a side panel (#179)."""

    IMPORTS = """
import { caseButtonLabel, caseTargetIds, renderCaseForm, syncCaseSelection } from "@js/alerts/case-panel.js";
import { closeSidePanel, openSidePanel } from "@js/alerts/panels.js";
import { actionCell, siteNameHtml } from "@js/alerts/rows.js";
import { els } from "@js/alerts/state.js";

// Panels start hidden; the row buttons that open them are looked up again on close.
function panel(name) {
  const el = element(name);
  el.classList.add("hidden");
  return el;
}
const buttons = {};
document.querySelector = (selector) => buttons[selector] || null;
const historyPanel = panel("history-panel");
const casePanel = panel("case-panel");
Object.assign(els, { historyPanel, casePanel });
// A button in a row's "⋯" menu (#311) is found in its menu.
function rowButton(name, menu = null) {
  return element(name, { closest: (selector) => (selector === "details.row-menu" ? menu : null) });
}
"""

    def _run(self, body):
        run_js(self.IMPORTS + body)

    def test_action_cell_is_one_line_of_buttons(self):
        self._run("""
const site = { id: "s1", name: "Aarhus <HQ>", latitude: 56.1, longitude: 10.2,
  down_devices: [{ device_id: "d1", device_name: "sw01" }, { device_id: "", device_name: "ghost" }] };
let html = actionCell(site);
check(html.includes('class="action-btn case-open-btn"') && html.includes("+ Case"), html);
// One visible action (#311): + Case first, Copy and Maint. in the "⋯" menu after it.
const menuAt = html.indexOf('<details class="row-menu">');
check(menuAt > html.indexOf("case-open-btn") && html.indexOf("copy-site-btn") > menuAt, html);
check(html.includes('aria-label="More actions for Aarhus &lt;HQ&gt;"'), html);
// Copy, Maint. and History are all in the menu; the map is the site name
// (no map link here); the one link is to the site's maintenance history (#353).
check(html.indexOf("history-btn") > menuAt && !html.includes('href="/?') && html.indexOf("maint-history-link") > menuAt, html);
check(html.includes("for Aarhus &lt;HQ&gt;"), "screen readers hear the site: " + html);
// The whole case form is gone from the row.
check(!html.includes("case-form") && !html.includes("checkbox") && !html.includes("<input"), html);

// Down devices without an id: Copy but no + Case.
html = actionCell({ id: "s2", name: "Oslo", down_devices: [{ device_id: "", device_name: "ghost" }] });
check(!html.includes("case-open-btn") && html.includes("copy-site-btn"), html);

// Nothing down: nothing to expand, so History is in the row's menu.
html = actionCell({ id: "s3", name: "Bergen", down_devices: [] });
check(html.includes('class="action-btn history-btn"') && !html.includes("copy-site-btn"), html);
check(html.indexOf("history-btn") > html.indexOf('<details class="row-menu">'), html);

// An open alert kept after a failed observation: nothing down now, but a
// list to expand: the same actions as any site with down devices.
html = actionCell({ id: "s4", name: "Kiel", down_device_count: 0, down_devices: [{ device_id: "d9", device_name: "sw9" }] });
check(html.includes("history-btn") && html.includes("copy-site-btn"), html);
""")

    def test_toggle_and_rows_use_the_same_list(self):
        """The toggle, the actions and the device rows all follow down_devices, not the count."""
        self._run("""
import { renderDownDeviceRows, siteRowsHtml } from "@js/alerts/rows.js";
const kept = { id: "s4", name: "Kiel", down_device_count: 0, down_devices: [{ device_id: "d9", device_name: "sw9" }] };
const html = siteRowsHtml([kept], { wallView: false, isExpanded: () => true });
check(html.includes('class="site-toggle-btn"') && html.includes("sw9"), html);
check(renderDownDeviceRows(kept, true).includes("sw9"), "device rows");
const none = siteRowsHtml([{ id: "s5", name: "Oslo", down_device_count: 3 }], { wallView: false, isExpanded: () => true });
check(!none.includes("site-toggle-btn") && none.includes("site-toggle-spacer"), none);
""")

    def test_site_name_links_to_the_map(self):
        self._run("""
let html = siteNameHtml({ id: "loc 1", name: "Aarhus <HQ>", latitude: 56.1, longitude: 10.2 });
check(html.includes('href="/?location_id=loc%201"') && html.includes("Aarhus &lt;HQ&gt;"), html);
html = siteNameHtml({ id: "s2", name: "Oslo" });
check(!html.includes("href") && html.includes(">Oslo<"), "no coordinates: plain text: " + html);
""")

    def test_case_form_lists_down_devices(self):
        self._run("""
let html = renderCaseForm({ id: "s1", down_devices: [
  { device_id: "d1", device_name: "sw<1>" }, { device_id: "d2", device_name: "sw2" }, { device_id: "" }] });
check(html.includes('<form class="case-form" data-site-id="s1">'), html);
check((html.match(/case-device-checkbox/g) || []).length === 2, "one checkbox per device with an id");
check(html.includes("All down devices (2)") && html.includes("sw&lt;1&gt;"), html);
check(html.includes('type="submit"') && html.includes("Add case to 2 devices"), html);
check(html.includes('<label class="case-input-label" for="case-input">'), "the case field has a label");

html = renderCaseForm({ id: "s1", down_devices: [{ device_id: "d1", device_name: "sw1" }] });
check(!html.includes("All down devices"), "no select-all for one device");
""")

    def test_case_form_shows_existing_cases_and_ticks_the_rest(self):
        """A second ticket isn't added by mistake (#273)."""
        self._run("""
const site = { id: "s1", down_devices: [
  { device_id: "d1", device_name: "sw1", case_numbers: ["INC-881"] },
  { device_id: "d2", device_name: "sw2", case_numbers: [] },
  { device_id: "d3", device_name: "sw3", case_numbers: ["INC-881", "<x>"] }] };
let html = renderCaseForm(site);
check(html.includes("Open cases on this site:") && (html.match(/>INC-881</g) || []).length === 3, "site line once + two devices: " + html);
check(html.includes("&lt;x&gt;") && !html.includes("<x>"), "escaped");
check(/value="d1" data-cases="INC-881" \\/>/.test(html), "a device with a case starts unticked: " + html);
check(/value="d2" data-cases="" checked \\/>/.test(html), "a device without one starts ticked");
check(html.includes("Add case to 1 device<") && !html.includes("Every down device already"), html);
check(!/case-select-all" checked/.test(html), "select-all is not fully ticked");

html = renderCaseForm({ id: "s2", down_devices: [{ device_id: "d1", device_name: "sw1", case_numbers: ["INC-1"] }] });
check(html.includes("Every down device already has a case") && html.includes("doesn't replace it"), html);
check(html.includes("disabled>Select devices<"), "nothing ticked, nothing to save: " + html);

html = renderCaseForm({ id: "s3", down_devices: [{ device_id: "d1", device_name: "sw1" }] });
check(!html.includes("Open cases") && !html.includes("Every down device"), "no cases: no notes");
""")

    def test_the_request_only_sends_devices_without_the_number(self):
        """Mixed selection: the POST must not re-link a device that has the number (Copilot on #274)."""
        self._run("""
import { initCasePanel } from "@js/alerts/case-panel.js";
const sent = [];
globalThis.fetch = async (url, options) => {
  sent.push(JSON.parse(options.body));
  return { ok: false, status: 500, json: async () => ({ error: "stop here" }) };
};
const errors = [];
document.getElementById = (id) => (id === "error-toast" ? element("toast") : null);
const listeners = {};
casePanel.addEventListener = (type, listener) => { listeners[type] = listener; };
initCasePanel();

const box = (checked, cases, value) => ({ checked, value, dataset: { cases: cases.join("\\n") }, nextElementSibling: { textContent: value } });
const boxes = [box(true, ["INC-881"], "d1"), box(true, [], "d2"), box(false, [], "d3")];
const form = element("form", {
  dataset: { siteId: "s1" },
  querySelectorAll: () => boxes,
  querySelector: (sel) => (sel === ".case-input" ? { value: " INC-881 " } : sel === ".case-save-btn" ? element("save") : null),
});
listeners.submit({ target: { closest: () => form }, preventDefault() {} });
await new Promise((resolve) => setTimeout(resolve, 0));
check(sent.length === 1, JSON.stringify(sent));
check(JSON.stringify(sent[0]) === '{"site_id":"s1","device_ids":["d2"],"case_number":"INC-881"}', JSON.stringify(sent[0]));
""")

    def test_button_says_when_the_number_is_already_there(self):
        self._run("""
check(caseButtonLabel(0) === "Select devices", "none");
check(caseButtonLabel(2) === "Add case to 2 devices", caseButtonLabel(2));
check(caseButtonLabel(1, 1) === "Already on 1 device", caseButtonLabel(1, 1));
check(caseButtonLabel(3, 1) === "Add case to 2 devices (already on 1)", caseButtonLabel(3, 1));

const box = (checked, cases, value) => ({ checked, value, dataset: { cases: cases.join("\\n") } });
const boxes = [box(true, ["INC-881"], "d1"), box(true, [], "d2"), box(false, ["INC-9"], "d3")];
const input = { value: "" };
const button = { textContent: "", disabled: false };
const selectAll = { checked: false, indeterminate: false };
const form = { querySelectorAll: () => boxes, querySelector: (sel) =>
  sel === ".case-input" ? input : sel === ".case-save-btn" ? button : selectAll };
syncCaseSelection(form);
check(button.textContent === "Add case to 2 devices" && !button.disabled && selectAll.indeterminate, button.textContent);
input.value = " inc-881 ";
syncCaseSelection(form);
check(button.textContent === "Add case to 1 device (already on 1)" && !button.disabled, "ignoring case: " + button.textContent);
// What is sent matches the label: d1 already has it, only d2 gets it.
check(JSON.stringify(caseTargetIds(form, " inc-881 ")) === '["d2"]', JSON.stringify(caseTargetIds(form, "inc-881")));
check(JSON.stringify(caseTargetIds(form, "INC-900")) === '["d1","d2"]', "a new number goes to every ticked device");
check(JSON.stringify(caseTargetIds(form, "")) === '["d1","d2"]', "nothing typed yet");
boxes[1].checked = false;
syncCaseSelection(form);
check(button.textContent === "Already on 1 device" && button.disabled, button.textContent);
""")

    def test_one_panel_at_a_time_and_focus_returns_to_the_row(self):
        self._run("""
buttons['.history-btn[data-site-id="s1"]'] = rowButton("history s1");
buttons['.case-open-btn[data-site-id="s1"]'] = rowButton("case s1");

openSidePanel(historyPanel, '.history-btn[data-site-id="s1"]', element("history close"));
check(!historyPanel.classList.contains("hidden") && historyPanel.attrs["aria-hidden"] === "false", "history open");
check(focused.at(-1) === "history close", "focus moves into the panel");

// Opening the case panel closes History.
openSidePanel(casePanel, '.case-open-btn[data-site-id="s1"]', element("case input"));
check(historyPanel.classList.contains("hidden") && historyPanel.attrs["aria-hidden"] === "true", "history closed");
check(focused.at(-1) === "case input", "focus in the case field");

// Closing (Close button or Escape) returns focus to the + Case button,
// looked up again because the board may have re-rendered meanwhile.
buttons['.case-open-btn[data-site-id="s1"]'] = rowButton("case s1 (re-rendered)");
closeSidePanel(casePanel);
check(casePanel.classList.contains("hidden"), "case panel closed");
check(focused.at(-1) === "case s1 (re-rendered)", focused.join());
closeSidePanel(casePanel);
check(focused.length === 3, "closing a closed panel does nothing");

// Opened from a row's "⋯" menu, closed since: focus goes to the menu button (#311).
const menuButton = element("menu s1");
const menu = { open: false, querySelector: () => menuButton };
buttons['.copy-site-btn[data-site-id="s1"]'] = rowButton("copy s1", menu);
openSidePanel(casePanel, '.copy-site-btn[data-site-id="s1"]');
closeSidePanel(casePanel);
check(focused.at(-1) === "menu s1", focused.join());

// The row is gone (e.g. the site recovered): focus is not moved.
openSidePanel(historyPanel, '.history-btn[data-site-id="gone"]');
check(focused.at(-1) === "history-panel", "without a target the panel itself gets focus");
closeSidePanel(historyPanel);
check(focused.at(-1) === "history-panel", "no focus without the row");
""")

    def test_board_page_has_the_case_panel(self, integration_client):
        html = integration_client.get("/alerts").data.decode()
        assert 'id="case-panel"' in html and 'role="dialog"' in html
        assert 'id="case-close"' in html


class TestAlertFeedInTheBrowser:
    """The activity panel beside the board (#180)."""

    def _run(self, body):
        run_js(
            """
import { formatFeedTime } from "@js/lib/format.js";
import { feedStartsCollapsed, renderFeed, renderFeedEvent } from "@js/alerts/feed.js";
"""
            + body,
            tz="UTC",
        )

    def test_entries(self):
        self._run("""
const now = new Date("2026-09-28T15:00:00Z");
check(formatFeedTime("2026-09-28T12:05:00Z", now).includes("12:05"), formatFeedTime("2026-09-28T12:05:00Z", now));
check(formatFeedTime("2026-09-25T12:05:00Z", now).includes("Sep"), "another day shows the date");
check(formatFeedTime("nonsense", now) === "", "bad time");

let html = renderFeedEvent({ kind: "down", at: "2026-09-28T12:05:00Z", down_since: "2026-09-28T12:05:00Z",
  site_name: "Site <One>", device_name: "sw01" }, now);
check(html.includes("feed-down") && html.includes("<strong>sw01</strong> down") && html.includes("Site &lt;One&gt;"), html);
check(!html.includes("down since"), "noticed when it went down: no extra time");

html = renderFeedEvent({ kind: "down", at: "2026-09-28T12:05:00Z", down_since: "2026-09-25T09:00:00Z",
  site_name: "Site One", device_name: "sw01" }, now);
check(html.includes("down since") && html.includes("Sep"), html);

html = renderFeedEvent({ kind: "up", at: "2026-09-28T12:10:00Z", site_name: "Site One", device_id: "d-1" }, now);
check(html.includes("feed-up") && html.includes("<strong>d-1</strong> back up"), html);

html = renderFeedEvent({ kind: "severity", at: "2026-09-28T12:10:00Z", site_name: "Site One",
  from_level: "ok", to_level: "critical" }, now);
check(html.includes("feed-severity") && html.includes("alert-ok") && html.includes("alert-critical"), html);
check(html.indexOf("alert-ok") < html.indexOf("alert-critical"), "from → to");
""")

    def test_empty_states(self):
        self._run("""
check(renderFeed({ events: [] }, []).includes("Choose what to show"), "no kinds");
check(renderFeed({ events: [], persistence_configured: false }, ["down"]).includes("PostgreSQL"), "no database");
check(renderFeed({ events: [], persistence_configured: true }, ["down"]).includes("No changes yet"), "empty");
const html = renderFeed({ events: [
  { kind: "up", at: "2026-09-28T12:10:00Z", site_name: "A", device_name: "x" },
  { kind: "down", at: "2026-09-28T12:05:00Z", site_name: "A", device_name: "x" },
], persistence_configured: true }, ["down", "up"]);
check((html.match(/<li class="feed-entry/g) || []).length === 2, html);
""")

    def test_starts_collapsed_only_without_room_or_when_hidden(self):
        self._run("""
check(feedStartsCollapsed(null, 1440) === true, "laptop: the table keeps its room");
check(feedStartsCollapsed(null, 1920) === false, "wide screen: open");
check(feedStartsCollapsed("0", 1440) === false, "opened by the user stays open");
check(feedStartsCollapsed("1", 1920) === true, "hidden by the user stays hidden");
""")

    def test_board_page_has_the_feed_panel(self, integration_client):
        html = integration_client.get("/alerts").data.decode()
        assert 'id="feed-panel"' in html and 'id="feed-list"' in html
        assert 'id="feed-show"' in html  # brings a hidden feed back
        for kind in ("down", "up", "severity"):
            assert f'data-feed-kind="{kind}" aria-pressed="true"' in html


class TestSeverityTiersInTheBrowser:
    """Low and No data on the board and the map (#124)."""

    def test_board_renders_the_new_levels(self):
        run_js("""
import { alertBadge, severityWeight } from "@js/alerts/rows.js";
const sorted = ["ok", "no_data", "low", "medium", "critical"].sort((a, b) => severityWeight(a) - severityWeight(b));
check(sorted.join() === "critical,medium,low,no_data,ok", sorted.join());
check(alertBadge("no_data").includes(">NO DATA<") && alertBadge("no_data").includes("alert-no_data"), alertBadge("no_data"));
check(alertBadge("low").includes(">LOW<"), alertBadge("low"));
check(alertBadge(undefined).includes("NO DATA"), "missing level shows as No data");
""")

    def test_map_renders_the_new_levels(self):
        run_js("""
import { statusLineHtml } from "@js/map/panel.js";
const noData = statusLineHtml({ alert: { level: "no_data", reason: "No monitored devices" } });
check(noData.includes("No data · no monitored devices") && !noData.includes("site-status-reason"), noData);
const ok = statusLineHtml({ alert: { level: "ok", reason: "" }, counts: { down: 0, monitored: 3 } });
check(ok.includes("is-ok") && ok.includes("OK · 3 devices"), ok);
const low = statusLineHtml({ alert: { level: "low", reason: "1/20 devices offline (5%)" }, counts: { down: 1, monitored: 20 } });
check(low.includes("is-low") && low.includes("Low · 1 of 20 down") && low.includes("1/20 devices offline"), low);
check(statusLineHtml({ alert: { level: "medium" }, counts: { down: 6, monitored: 20 }, since: "14:05" }).includes("Medium · 6 of 20 down · since 14:05"), "medium");
""")

    def test_board_page_has_low_and_no_data_tiles_and_filters(self, integration_client):
        html = integration_client.get("/alerts").get_data(as_text=True)
        for needle in ('id="summary-no_data"', 'data-severity-filter="no_data"'):
            assert needle in html
        # One chip per choice that leads somewhere; the levels are in the summary line (#311).
        for gone in ('id="summary-unknown"', 'id="summary-low"', 'data-severity-filter="low"', 'id="summary-ok"'):
            assert gone not in html


class TestNewestDownSortInTheBrowser:
    """The board lists the site with the newest down device first (#228)."""

    def _run(self, body):
        run_js(
            """
import { compareSites, rememberSort, restoreSort } from "@js/alerts/filters.js";
import { SORT_KEY, els } from "@js/alerts/state.js";
"""
            + body
        )

    def test_newest_down_first_then_severity(self):
        self._run("""
const sites = [
  { name: "Healthy", alert_level: "ok", down_device_count: 0, latest_down_at: null },
  { name: "Old outage", alert_level: "critical", down_device_count: 5, latest_down_at: "2026-09-29T08:00:00Z" },
  { name: "No data", alert_level: "no_data", down_device_count: 0 },
  { name: "Just now", alert_level: "low", down_device_count: 1, latest_down_at: "2026-09-29T11:59:00Z" },
  { name: "An hour ago", alert_level: "medium", down_device_count: 2, latest_down_at: "2026-09-29T11:00:00Z" },
  { name: "Down, no alert yet", alert_level: "medium", down_device_count: 1, latest_down_at: "not a time" },
];
const order = (sort) => [...sites].sort((a, b) => compareSites(a, b, sort)).map((s) => s.name).join(" | ");
check(order("newest") === "Just now | An hour ago | Old outage | Down, no alert yet | No data | Healthy", order("newest"));
// The other sorts are unchanged.
check(order("severity") === "Old outage | An hour ago | Down, no alert yet | Just now | No data | Healthy", order("severity"));
check(order("site").startsWith("An hour ago | Down, no alert yet | Healthy"), order("site"));
""")

    def test_the_choice_is_remembered_and_reset(self):
        self._run("""
const store = {};
setGlobal("localStorage", {
  getItem: (key) => (key in store ? store[key] : null),
  setItem: (key, value) => { store[key] = String(value); },
  removeItem: (key) => { delete store[key]; },
});
const sortBy = { value: "", options: ["newest", "severity", "site"].map((value) => ({ value })) };
els.sortBy = sortBy;

restoreSort();
check(sortBy.value === "newest", "default: " + sortBy.value);
rememberSort("severity");
restoreSort();
check(sortBy.value === "severity", "remembered: " + sortBy.value);
rememberSort("newest");
check(!(SORT_KEY in store), "choosing the default forgets the choice");
store[SORT_KEY] = "gone";
restoreSort();
check(sortBy.value === "newest", "an option that no longer exists falls back to the default");
setGlobal("localStorage", { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } });
restoreSort();
rememberSort("site");
check(sortBy.value === "newest", "blocked storage keeps the default");
""")

    def test_board_page_defaults_to_newest_down(self, integration_client):
        html = integration_client.get("/alerts").get_data(as_text=True)
        select = html[html.index('<select id="sort-by"') :]
        first_option = re.search(r"<option [^>]*>", select).group(0)
        assert 'value="newest"' in first_option and "selected" in first_option
        assert '<option value="severity">' in select


class TestCopySiteInTheBrowser:
    """Copy a site and its down devices as text for an ITSM ticket (#227)."""

    def _run(self, body, tz="Europe/Copenhagen"):
        run_js(
            """
import { formatReportTime } from "@js/lib/format.js";
import { copyText, siteReport } from "@js/alerts/panels.js";
import { actionCell } from "@js/alerts/rows.js";
"""
            + body,
            tz=tz,
        )

    SITE = """
const site = {
  id: "loc 1", name: "Aarhus HQ", ancestor_path: "EMEA › DNK", physical_address: "Havnegade 1, Aarhus",
  tenant: "Acme", alert_level: "critical", alert_reason: "Core device(s) offline: core01",
  device_count: 12, down_device_count: 2, active_cases: ["INC-7"],
  down_devices: [
    { device_id: "d1", device_name: "acc01", device_ip: "10.1.0.3", role: "Access Switch", status: "Offline",
      location_path: "Bygning A › Etage 2", down_started_at: "2026-09-29T08:00:00Z", case_numbers: ["INC-7"] },
    { device_id: "d2", device_name: "core01", device_ip: "", role: "Core Router", status: "Failed",
      down_started_at: "2026-09-29T10:30:00Z", case_numbers: [] },
  ],
};
"""

    def test_report_names_the_likely_cause_first(self):
        """The down core device first, the devices behind it say so (#285)."""
        self._run(
            self.SITE
            + """
site.down_devices[1].core = true;
site.down_devices[0].probable_cause = "core01";
const text = siteReport(site, "");
check(text.includes("Likely cause: core01 down; 1 other device probably behind it"), text);
const lines = text.split("\\n").filter((line) => line.startsWith("- "));
check(lines[0].startsWith("- core01") && lines[0].includes("| Core device"), lines.join("\\n"));
check(lines[1].startsWith("- acc01") && lines[1].endsWith("| Likely caused by core01"), lines.join("\\n"));
"""
        )

    def test_report_lists_the_site_and_every_down_device(self):
        self._run(
            self.SITE
            + """
const text = siteReport(site, "https://nautobot.example.com/");
const expected = [
  "Site: Aarhus HQ",
  "Location: EMEA › DNK",
  "Address: Havnegade 1, Aarhus",
  "Tenant: Acme",
  "Severity: CRITICAL (2 of 12 devices down)",
  "Reason: Core device(s) offline: core01",
  "Cases: INC-7",
  "Nautobot: https://nautobot.example.com/dcim/locations/loc%201/",
  "",
  "Down devices (2):",
  "- core01 | IP — | Role Core Router | Status Failed | Down since 2026-09-29 12:30 +02:00",
  "- acc01 | IP 10.1.0.3 | Role Access Switch | Status Offline | Location Bygning A › Etage 2 | Down since 2026-09-29 10:00 +02:00 | Case INC-7",
].join("\\n");
check(text === expected, "\\n" + text);
"""
        )

    def test_report_without_optional_fields(self):
        self._run("""
const text = siteReport({ id: "s1", alert_level: "no_data", down_devices: [{ device_id: "d1" }] });
check(text.startsWith("Site: s1\\nSeverity: NO DATA (0 of 0 devices down)\\n\\nDown devices (1):"), text);
check(text.endsWith("- d1 | IP — | Role — | Status —"), text);
check(!text.includes("Nautobot:") && !text.includes("undefined"), text);
""")

    def test_report_times_carry_their_offset(self):
        self._run(
            """
check(formatReportTime("2026-01-15T08:05:00Z") === "2026-01-15 08:05 +00:00", formatReportTime("2026-01-15T08:05:00Z"));
check(formatReportTime("") === "" && formatReportTime("nonsense") === "", "no time");
""",
            tz="UTC",
        )
        self._run(
            """
check(formatReportTime("2026-01-15T08:05:00Z") === "2026-01-15 03:05 -05:00", formatReportTime("2026-01-15T08:05:00Z"));
""",
            tz="America/New_York",
        )

    def test_copy_button_only_for_sites_with_down_devices(self):
        self._run(
            self.SITE
            + """
let html = actionCell(site);
check(html.includes('class="action-btn copy-site-btn"') && html.includes('data-site-id="loc 1"'), html);
check(html.includes("Copy<span class=\\"visually-hidden\\"> for Aarhus HQ</span>"), html);
html = actionCell({ id: "s2", name: "Oslo", down_devices: [] });
check(!html.includes("copy-site-btn"), html);
"""
        )

    def test_plain_http_falls_back_to_the_copy_command(self):
        self._run("""
const calls = [];
let execResult = true;
document.body = { appendChild: (el) => calls.push(["append", el.className, el.value]) };
document.createElement = () => ({ setAttribute() {}, select() { calls.push(["select"]); }, remove() { calls.push(["remove"]); } });
document.execCommand = (command) => { calls.push(["exec", command]); return execResult; };
setGlobal("window", { isSecureContext: false });
setGlobal("navigator", { clipboard: { writeText: () => { throw new Error("must not use the API over HTTP"); } } });
check(await copyText("hello") === true, "copied with the fallback");
check(JSON.stringify(calls) === JSON.stringify([["append", "clipboard-buffer", "hello"], ["select"], ["exec", "copy"], ["remove"]]), JSON.stringify(calls));
execResult = false;
check(await copyText("hello") === false, "reports failure so the panel opens");
""")

    def test_secure_pages_use_the_clipboard_api(self):
        self._run("""
const written = [];
setGlobal("window", { isSecureContext: true });
setGlobal("navigator", { clipboard: { writeText: async (text) => { written.push(text); } } });
document.createElement = () => { throw new Error("no fallback needed"); };
check(await copyText("site text") === true && written[0] === "site text", JSON.stringify(written));
""")

    def test_board_page_has_the_copy_panel_and_config(self, integration_client):
        html = integration_client.get("/alerts").get_data(as_text=True)
        assert 'id="copy-panel"' in html and 'id="copy-text"' in html and 'id="copy-close"' in html
        assert 'id="copy-status"' in html and 'aria-live="polite"' in html
        assert 'id="app-config"' in html and "data-nautobot-url=" in html


class TestSeverityTilesInTheBrowser:
    """The summary tiles filter the board; it opens on Alarms (#232)."""

    def _run(self, body):
        run_js(
            """
import { formatBoardStatus } from "@js/alerts/board.js";
import { getFilteredAlerts, renderMoreFiltersLabel, setSeverityFilter } from "@js/alerts/filters.js";
import { DEFAULT_SORT, els, state } from "@js/alerts/state.js";

const button = (value) => element(value, { dataset: { severityFilter: value } });
els.severityFilterButtons = ["alarms", "critical", "medium", "low", "no_data", "ok", "all"].map(button);
const pressed = () => els.severityFilterButtons.filter((b) => b.attrs["aria-pressed"] === "true").map((b) => b.dataset.severityFilter).join();
const [filterSite, filterStatus, filterType, filterTenant] = ["site", "status", "type", "tenant"].map((name) => element(name));
const toggleNonOperational = element("non-operational");
const moreFiltersToggle = element("more-filters");
const sortBy = element("sort-by", { value: "severity" });
const boardStatus = element("board-status");
Object.assign(els, { filterSite, filterStatus, filterType, filterTenant, toggleNonOperational, moreFiltersToggle, sortBy, boardStatus });
state.allAlerts = [
  { name: "Oslo", alert_level: "ok" },
  { name: "Aarhus", alert_level: "critical", down_device_count: 3 },
  { name: "Bergen", alert_level: "no_data" },
  { name: "London", alert_level: "low", down_device_count: 1, tenant: "Acme" },
  { name: "Paris", alert_level: "medium", down_device_count: 2 },
];
"""
            + body
        )

    def test_opens_on_alarms_and_each_tile_filters(self):
        self._run("""
const names = () => getFilteredAlerts().map((site) => site.name).join(",");
check(names() === "Aarhus,Paris,London", "default Alarms (No data is not an alarm): " + names());
setSeverityFilter("all");
check(pressed() === "all", "one tile pressed: " + pressed());
check(names() === "Aarhus,Paris,London,Bergen,Oslo", "all sites: " + names());
setSeverityFilter("no_data");
check(names() === "Bergen", "one level: " + names());
setSeverityFilter("alarms");
filterTenant.value = "Acme";
check(names() === "London", "with a tenant filter: " + names());
setSeverityFilter("nonsense");
check(state.severityFilter === "alarms" && pressed() === "alarms", "unknown value: Alarms");
""")

    def test_status_line_says_when_limited(self):
        self._run("""
// One short line (#311).
formatBoardStatus({ checked_at: null, stale: false }, 3);
check(boardStatus.textContent === "3 sites with alarms · updated unknown", boardStatus.textContent);
setSeverityFilter("all");
formatBoardStatus({ checked_at: null, stale: false }, 1);
check(boardStatus.textContent === "1 site · updated unknown" && !boardStatus.classList.contains("board-stale"), boardStatus.textContent);
// "Stale" only when it is, and in the warning colour.
formatBoardStatus({ checked_at: null, stale: true }, 1);
check(boardStatus.textContent.endsWith("· data may be out of date") && boardStatus.classList.contains("board-stale"), boardStatus.textContent);
""")

    def test_more_filters_says_when_in_use(self):
        self._run("""
sortBy.value = DEFAULT_SORT;
renderMoreFiltersLabel();
check(moreFiltersToggle.textContent === "More filters", moreFiltersToggle.textContent);
filterStatus.value = "Active";
toggleNonOperational.checked = true;
renderMoreFiltersLabel();
check(moreFiltersToggle.textContent === "More filters (2)", moreFiltersToggle.textContent);
// Sort is behind More filters too (#311): another order counts.
sortBy.value = "site";
renderMoreFiltersLabel();
check(moreFiltersToggle.textContent === "More filters (3)", moreFiltersToggle.textContent);
""")

    def test_board_page_has_the_tiles_and_more_filters(self, integration_client):
        html = integration_client.get("/alerts").get_data(as_text=True)
        assert 'data-severity-filter="alarms" aria-pressed="true"' in html
        chips = html[html.index('<div class="severity-chips"') : html.index('<section class="toolbar"')]
        assert chips.count('aria-pressed="true"') == 1 and chips.count("data-severity-filter=") == 4
        assert 'data-severity-filter="all" aria-pressed="false"' in html
        assert 'id="summary-alarms"' in html
        # Status, type and non-operational are behind More filters, closed by default.
        more = html[html.index('<div id="more-filters"') :]
        assert more.startswith('<div id="more-filters" class="more-filters" hidden>')
        for needle in ('id="sort-by"', 'id="filter-status"', 'id="filter-type"', 'id="toggle-non-operational"'):
            assert needle in more[: more.index("</div>")]
        # Reset only appears when a filter is in use (#311).
        assert 'id="clear-alert-filters" hidden>' in html
        assert 'aria-controls="more-filters"' in html
        # Gone: the duplicate severity controls.
        for gone in ("data-quick-severity", 'id="filter-severity"', 'name="site-scope"', "page-subtitle"):
            assert gone not in html


class TestWallViewInTheBrowser:
    """The wall-screen view, /alerts?view=wall (#243)."""

    def _run(self, body):
        run_js(
            """
import { formatClock } from "@js/lib/format.js";
import { loadAlertBoard } from "@js/alerts/board.js";
import { config, els, state } from "@js/alerts/state.js";
import { renderWallStatus, showWallError } from "@js/alerts/wall.js";

const WALL_STALE_MS = 10 * 60000;
els.wallUpdatedEl = element("wall-updated");
els.wallErrorEl = element("wall-error", { hidden: true });
const { wallUpdatedEl, wallErrorEl } = els;
state.allAlerts = [
  { name: "Aarhus", alert_level: "critical" },
  { name: "Bergen", alert_level: "no_data" },
  { name: "London", alert_level: "low" },
];
"""
            + body,
            tz="UTC",
        )

    def test_status_says_when_it_last_updated_and_when_it_is_stale(self):
        self._run("""
renderWallStatus();
check(wallUpdatedEl.textContent === "Loading…", wallUpdatedEl.textContent);
state.lastLoadedAt = Date.UTC(2026, 9, 5, 14, 5);
renderWallStatus(state.lastLoadedAt + 60000);
const clock = formatClock(state.lastLoadedAt);
check(clock.includes("05"), clock);
check(wallUpdatedEl.textContent === `Updated ${clock} · 2 sites with alarms · 2 without case or ack`, wallUpdatedEl.textContent);
check(!wallUpdatedEl.classList.contains("wall-stale"), "fresh");
state.allAlerts = [{ alert_level: "medium" }];
renderWallStatus(state.lastLoadedAt);
check(wallUpdatedEl.textContent.endsWith("· 1 site with alarms · 1 without case or ack"), wallUpdatedEl.textContent);
renderWallStatus(state.lastLoadedAt + WALL_STALE_MS);
check(wallUpdatedEl.textContent === `NOT UPDATED since ${clock}` && wallUpdatedEl.classList.contains("wall-stale"), wallUpdatedEl.textContent);
""")

    def test_error_banner_keeps_the_last_board(self):
        self._run("""
showWallError("HTTP 502");
check(!wallErrorEl.hidden && wallErrorEl.textContent === "Alert board not updating: HTTP 502.", wallErrorEl.textContent);
state.lastLoadedAt = Date.UTC(2026, 9, 5, 9, 30);
showWallError("HTTP 502");
check(wallErrorEl.textContent.endsWith(`Showing the board from ${formatClock(state.lastLoadedAt)}.`), wallErrorEl.textContent);
""")

    def test_loads_newest_wins_and_failures_keep_an_empty_board(self):
        """Copilot review on #245: overlapping loads, and an empty last-good board."""
        self._run("""
config.wallView = true;
state.allAlerts = [];
const names = ["boardStatus", "alertsTableBody", "refreshBtn", "filterSite", "filterStatus", "filterType",
  "filterTenant", "sortBy", "summaryAlarms", "summaryNoData", "summaryMaintenance", "summaryTotal"];
names.forEach((name) => { els[name] = element(name); });
els.sortBy.value = "newest";
const pending = [];
globalThis.fetch = () => new Promise((resolve) => pending.push(resolve));
const reply = (index, body, ok = true) =>
  pending[index]({ ok, status: ok ? 200 : 502, statusText: "", headers: { get: () => "application/json" }, json: async () => body });

// Two loads overlap; the older answers last and must be ignored.
const first = loadAlertBoard(false, { background: true });
const second = loadAlertBoard(false, { background: true });
reply(1, { alerts: [] });
await second;
reply(0, { alerts: [{ id: "s1", name: "Stale", alert_level: "critical" }] });
await first;
check(els.alertsTableBody.innerHTML.includes("No active alarms.") && !els.alertsTableBody.innerHTML.includes("Stale"),
  "only the newest load renders: " + els.alertsTableBody.innerHTML);
check(state.allAlerts.length === 0 && state.lastLoadedAt !== null, "the empty board was loaded");

// The board is empty but loaded: a failure keeps it and shows the banner.
const board = els.alertsTableBody.innerHTML;
const failing = loadAlertBoard(false, { background: true });
reply(2, { error: "upstream down" }, false);
await failing;
check(!wallErrorEl.hidden && wallErrorEl.textContent.startsWith("Alert board not updating: upstream down."), wallErrorEl.textContent);
check(els.alertsTableBody.innerHTML === board, "kept: " + els.alertsTableBody.innerHTML);
""")

    def test_wall_page(self, integration_client):
        html = integration_client.get("/alerts?view=wall").get_data(as_text=True)
        assert '<body class="wall-view">' in html
        assert 'id="wall-updated"' in html
        assert '<div id="wall-error" class="wall-error" role="alert" hidden></div>' in html
        assert 'href="/alerts">Operator view</a>' in html

    def test_operator_page_links_to_the_wall_view(self, integration_client):
        html = integration_client.get("/alerts").get_data(as_text=True)
        assert "<body>" in html and "wall-view" not in html.split("<body>", 1)[1].split(">", 1)[0]
        assert 'id="wall-updated"' not in html
        toolbar = html[
            html.index('<section class="toolbar"') : html.index("</section>", html.index('<section class="toolbar"'))
        ]
        assert "view=wall" not in toolbar, "the link is in the top navigation, not the toolbar"

    @pytest.mark.parametrize("path", ["/", "/alerts", "/docs", "/availability", "/maintenance", "/circuits"])
    def test_every_page_has_wall_view_in_the_top_navigation(self, integration_client, path):
        html = integration_client.get(path).get_data(as_text=True)
        start = html.index('class="app-nav"')
        # The map page's nav is a <div>, the others a <nav>; it ends at the first of either.
        end = min(i for i in (html.find("</nav>", start), html.find("</div>", start)) if i != -1)
        nav = html[start:end]
        links = re.findall(r'href="([^"]+)"', nav)
        assert links == [
            "/",
            "/alerts",
            "/availability",
            "/maintenance",
            "/circuits",
            "/help",
            "/docs",
            "/alerts?view=wall",
        ], links

    def test_wall_css_hides_the_controls(self, integration_client):
        css = integration_client.get("/static/css/alerts.css").get_data(as_text=True)
        hidden = css[css.index(".wall-view .page-header,") :]
        hidden = hidden[: hidden.index("}")]
        for selector in (".toolbar", ".summary-bar", ".feed-panel", ".col-action"):
            assert f".wall-view {selector}" in hidden
        # The tenants stay on the wall, readable (#351).
        assert ".site-tenants" not in hidden and ".wall-view .site-tenants" in css
        assert "display: none !important" in hidden
        # Case numbers stay visible: the room can see a site has someone on it.
        assert ".col-cases" not in hidden
        # The physical address stays too, at a readable size.
        assert ".site-address" not in hidden
        assert re.search(r"\.wall-view \.site-address[,\s][^{]*\{\s*color: var\(--color-text\)", css)

    def test_wall_text_is_the_browser_standard(self, integration_client):
        """Same sizes as the board (#267): a big screen is sized with the browser's zoom."""
        css = integration_client.get("/static/css/alerts.css").get_data(as_text=True)
        wall = css[css.index("/* Wall-screen view") : css.index("/* Availability report")]
        # Every font-size in the wall rules, in any unit (px, em, clamp(), ...).
        sizes = {
            selectors.strip().splitlines()[-1]: value.strip()
            for selectors, value in re.findall(r"([^{}]+)\{[^}]*?font-size:\s*([^;}]+)", wall)
        }
        assert ".wall-view" not in sizes, "no base size: the page's 16 px standard applies"
        for selector in (".wall-view .site-name", ".wall-view .alert-badge", ".wall-view .case-pill"):
            assert selector not in sizes, f"{selector} uses the board's size"
        for selector, value in sizes.items():
            if "empty-state" in selector:
                continue
            number = re.fullmatch(r"([\d.]+)rem", value)
            assert number, f"{selector}: font-size {value!r}; use rem, at most 1rem"
            assert float(number.group(1)) <= 1.0, f"{selector}: {value}"
        assert ".wall-view .case-pill" in css


class TestTenantInfoTipInTheBrowser:
    """The floating box for the tenant (i) (#263): placed inside the window."""

    def _run(self, body):
        run_js('import { placeInfoTip } from "@js/lib/tooltip.js";\n' + body)

    def test_placement_stays_in_the_window(self):
        self._run("""
const rect = (left, top) => ({ left, top, width: 15, height: 15, bottom: top + 15 });
let at = placeInfoTip(rect(500, 100), 200, 60, 1200, 800);
check(at.top === 123 && at.left === 407.5, "below, centred: " + JSON.stringify(at));
at = placeInfoTip(rect(1190, 100), 200, 60, 1200, 800);
check(at.left === 992, "kept off the right edge: " + JSON.stringify(at));
at = placeInfoTip(rect(0, 100), 200, 60, 1200, 800);
check(at.left === 8, "kept off the left edge: " + JSON.stringify(at));
at = placeInfoTip(rect(500, 760), 200, 60, 1200, 800);
check(at.top === 692, "above when there is no room below: " + JSON.stringify(at));
""")

    def test_pages_load_the_tooltip(self, integration_client):
        """Both pages that show an (i) load its CSS, and their main module starts it first."""
        for path, page_script in (("/", "map/main.js"), ("/alerts", "alerts/main.js")):
            html = integration_client.get(path).get_data(as_text=True)
            assert 'href="/static/css/tooltip.css"' in html and f'src="/static/js/{page_script}"' in html, path
            main = js_source(page_script)
            first_call = main.index("initInfoTips();")
            assert 'import { initInfoTips } from "../lib/tooltip.js";' in main
            assert first_call < main.index("Object.assign(els"), "before the page's own listeners"

    def test_map_tenant_row(self):
        run_js("""
import { tenantRow } from "@js/map/panel.js";
let row = tenantRow({ tenant: "Acme", tenant_descriptions: { Acme: "Bank & co" } });
check(row.includes('Acme<span class="info-tip"') && row.includes('data-info-tip="Bank &amp; co"'), row);
row = tenantRow({ tenant: "Acme", tenant_descriptions: { Other: "x" } });
check(row.includes("Acme") && !row.includes("info-tip"), row);
check(tenantRow({ tenant: "" }) === "", "no tenant, no row");
check(!tenantRow({ tenant: "constructor", tenant_descriptions: {} }).includes("info-tip"), "inherited property");
""")


class TestWallCaseStateInTheBrowser:
    """Wall view: which sites someone is on, at a glance (#271)."""

    def _run(self, body):
        run_js(
            """
import { caseSortRank, filterSites } from "@js/alerts/filters.js";
import { caseStateBadge, siteCaseState, siteNameHtml, siteRowsHtml } from "@js/alerts/rows.js";
import { els, state } from "@js/alerts/state.js";
import { renderWallStatus } from "@js/alerts/wall.js";
els.wallUpdatedEl = element("wall-updated");
const dev = (name, cases) => ({ device_id: name, device_name: name, case_numbers: cases });
"""
            + body
        )

    def test_case_state_and_badge(self):
        self._run("""
const none = { down_devices: [dev("a", []), dev("b", [])] };
const partly = { down_devices: [dev("a", ["INC-881"]), dev("b", [])] };
const handled = { down_devices: [dev("a", ["INC-1", "INC-2"]), dev("b", ["INC-1"])] };
check(siteCaseState(none).state === "none", "none");
check(JSON.stringify(siteCaseState(partly)) === '{"state":"partly","without":1,"total":2}', JSON.stringify(siteCaseState(partly)));
check(siteCaseState(handled).state === "handled", "handled");
// An alarm without listed devices, or devices without the field, never looks handled.
check(siteCaseState({ alert_level: "critical" }).state === "none", "no devices");
check(siteCaseState({ down_devices: [{ device_id: "x" }] }).state === "none", "no case_numbers field");

check(caseStateBadge(none).includes(">NO CASE<") && caseStateBadge(none).includes("case-state-none"), caseStateBadge(none));
check(caseStateBadge(partly).includes(">1 of 2 no case or ack<"), caseStateBadge(partly));
let badge = caseStateBadge(handled);
check(badge.includes("case-state-handled") && badge.includes("✓ INC-1, INC-2<"), "each case once: " + badge);
badge = caseStateBadge({ down_devices: [dev("a", ['<b>"x"'])] });
check(badge.includes("&lt;b&gt;&quot;x&quot;") && !badge.includes("<b>"), badge);
""")

    def test_handled_sites_sort_last_and_count_in_the_status(self):
        self._run("""
const sites = [
  { name: "London", alert_level: "medium", down_devices: [dev("a", ["INC-1"])] },
  { name: "Aarhus", alert_level: "critical", down_devices: [dev("b", [])] },
  { name: "Oslo", alert_level: "low", down_devices: [dev("c", ["INC-2"]), dev("d", [])] },
];
const order = [...sites].sort((a, b) => caseSortRank(a) - caseSortRank(b)).map((s) => s.name).join();
check(order === "Aarhus,Oslo,London", "handled last, the rest keep their order: " + order);
state.allAlerts = sites;
state.lastLoadedAt = Date.now();
renderWallStatus(state.lastLoadedAt);
check(els.wallUpdatedEl.textContent.endsWith("· 3 sites with alarms · 2 without case or ack"), els.wallUpdatedEl.textContent);
""")

    def test_wall_site_name_is_plain_text(self):
        self._run("""
const site = { id: "loc-1", name: "Aarhus HQ", latitude: 56.1, longitude: 10.2 };
check(!siteNameHtml(site, false).includes("href") && siteNameHtml(site, false).includes(">Aarhus HQ<"), siteNameHtml(site, false));
check(siteNameHtml(site).includes('href="/?location_id=loc-1"'), "the board keeps the link");
""")

    def test_rows_use_it_only_on_the_wall(self):
        self._run("""
const sites = [
  { id: "l", name: "London", alert_level: "medium", latitude: 1, longitude: 2, down_devices: [dev("a", ["INC-1"])] },
  { id: "a", name: "Aarhus", alert_level: "critical", latitude: 1, longitude: 2, down_devices: [dev("b", [])] },
];
const criteria = { sort: "site", severityFilter: "alarms" };
check(filterSites(sites, { ...criteria, wallView: true }).map((s) => s.name).join() === "Aarhus,London", "wall: nobody on it first");
check(filterSites(sites, { ...criteria, wallView: false }).map((s) => s.name).join() === "Aarhus,London", "board: by the sort");
check(filterSites([sites[0], sites[1]], { ...criteria, sort: "newest", wallView: false })[0].name === "Aarhus", "severity");

const wall = siteRowsHtml(sites, { wallView: true, isExpanded: () => true });
check(!wall.includes("href=") && wall.includes("case-state-handled") && wall.includes("case-state-none"), wall);
check(wall.includes('class="site-row case-handled handled-first"'), "handled rows are faded under a line: " + wall);
const board = siteRowsHtml(sites, { wallView: false, isExpanded: () => true });
check(board.includes('href="/?location_id=l"') && !board.includes("case-state") && !board.includes("case-handled"), board);
""")
        css = (REPO_ROOT / "static" / "css" / "alerts.css").read_text(encoding="utf-8")
        handled = re.search(r"\.wall-view tr\.case-handled > td,[^{]*\{([^}]*)\}", css)
        assert handled and "color: var(--color-text-muted)" in handled.group(1)
        # Quieter by colour, not opacity: text and badges keep readable contrast.
        assert "case-handled" not in "".join(re.findall(r"[^{}]*\{[^}]*opacity[^}]*\}", css))
        assert re.search(r"\.wall-view tr\.handled-first > td \{\s*border-top: 2px dashed", css)


class TestSinceWordingInTheBrowser:
    """Times say what they mean: "in alarm" for a site, "down" for a device (#275)."""

    def test_labels(self):
        run_js("""
import { deviceDowntimeSeconds, siteAlarmSeconds, sinceLabel } from "@js/alerts/rows.js";
check(sinceLabel("in alarm", 13200) === "in alarm 3h 40m", sinceLabel("in alarm", 13200));
check(sinceLabel("down", 1500) === "down 25m", sinceLabel("down", 1500));
check(sinceLabel("in alarm", null) === "—", "no open alarm: a dash, no words");
check(sinceLabel("in alarm", 0) === "in alarm <1m" && sinceLabel("down", 59) === "down <1m", "under a minute is not a dash");
// An unknown start is said, never shown as "<1m" (Copilot on #280).
check(sinceLabel("down", NaN) === "down, time unknown", sinceLabel("down", NaN));
check(Number.isNaN(deviceDowntimeSeconds({}, 0)) && deviceDowntimeSeconds({ down_started_at: "1970-01-01T00:00:30Z" }, 90000) === 60, "device");
check(siteAlarmSeconds({ down_devices: [] }) === null, "nothing down");
check(Number.isNaN(siteAlarmSeconds({ current_downtime_seconds: 0, down_devices: [{ device_id: "a" }] })), "unknown start");
check(siteAlarmSeconds({ current_downtime_seconds: 30, down_devices: [{ down_started_at: "2026-10-07T12:00:00Z" }] }) === 30, "known");
""")

    def test_no_lone_dash_without_a_path(self):
        """A site with no location path gets no grey line, not a bare "—" (#279)."""
        run_js("""
import { siteRowsHtml } from "@js/alerts/rows.js";
const html = siteRowsHtml([{ id: "s1", name: "Oslo" }], { wallView: false, isExpanded: () => false });
const nameCell = html.slice(html.indexOf("<td>"), html.indexOf("</td>"));
check(!nameCell.includes("site-meta") && !nameCell.includes("—"), nameCell);
""")

    def test_rows_and_header(self, integration_client):
        run_js("""
import { siteRowsHtml } from "@js/alerts/rows.js";
const started = new Date(Date.now() - 25 * 60000 - 5000).toISOString();
const site = { id: "s1", name: "Oslo", alert_level: "low", current_downtime_seconds: 13200,
  down_devices: [{ device_id: "d1", device_name: "sw1", down_started_at: started }] };
const since = (html) => html.match(/<td class="since-cell">([^<]*)<\\/td>/g).join(" ");
let html = siteRowsHtml([site], { wallView: false, isExpanded: () => true });
check(since(html) === '<td class="since-cell">in alarm 3h 40m</td> <td class="since-cell">down 25m</td>', since(html));
html = siteRowsHtml([site], { wallView: true, isExpanded: () => true });
check(since(html).startsWith('<td class="since-cell"></td>'), "the wall shows no site time: " + since(html));
""")
        html = integration_client.get("/alerts").get_data(as_text=True)
        assert "<th>Since</th>" in html and "<th>Downtime</th>" not in html


class TestHistoryExportInTheBrowser:
    """CSV download links in the History panel (#277)."""

    def test_links(self):
        run_js("""
import { historyExportBar, historyExportUrl } from "@js/alerts/panels.js";
check(historyExportUrl("loc 1&x", "devices", "all") === "/api/alert-history.csv?site_id=loc+1%26x&days=all&view=devices",
  historyExportUrl("loc 1&x", "devices", "all"));
const bar = historyExportBar('loc"1');
check(bar.includes('data-site-id="loc&quot;1"'), bar);
check(bar.includes('href="/api/alert-history.csv?site_id=loc%221&amp;days=30&amp;view=incidents" download>Incidents CSV'), bar);
check(bar.includes('data-view="devices"') && bar.includes(">Per-device CSV<"), bar);
check(bar.includes('<option value="30" selected>'), "30 days by default");
""")


class TestMaintenanceInTheBrowser:
    """Maintenance on the board and the wall view (#283)."""

    def test_helpers(self):
        run_js("""
import { caseSortRank } from "@js/alerts/filters.js";
import { alertBadge, maintenanceNote, siteAlarmSeconds, siteCaseState } from "@js/alerts/rows.js";
check(alertBadge("maintenance").includes('class="alert-badge alert-maintenance">MAINTENANCE<'), alertBadge("maintenance"));
const note = maintenanceNote("2026-10-09T14:00:00Z", "Core <upgrade>");
check(note.startsWith("until ") && note.endsWith(" · Core &lt;upgrade&gt;"), note);
check(maintenanceNote("", "x") === "x", "no time: just the reason");

// A device in maintenance doesn't need a case and doesn't count for "in alarm".
const site = { alert_level: "medium", current_downtime_seconds: 600, down_devices: [
  { device_id: "core", down_started_at: "2026-10-09T11:00:00Z", maintenance_until: "2026-10-09T14:00:00Z", case_numbers: [] },
  { device_id: "acc1", down_started_at: "2026-10-09T11:50:00Z", case_numbers: ["INC-1"] } ] };
check(siteCaseState(site).state === "handled", JSON.stringify(siteCaseState(site)));
check(siteAlarmSeconds(site) === 600, "known start from the other device");
check(siteAlarmSeconds({ alert_level: "maintenance", down_devices: site.down_devices }) === null, "maintenance: no time");

// Wall order: nobody on it, someone on it, maintenance.
check(caseSortRank({ alert_level: "critical", down_devices: [{ case_numbers: [] }] }) === 0, "none");
check(caseSortRank(site) === 1, "handled");
check(caseSortRank({ alert_level: "maintenance", down_devices: [] }) === 2, "maintenance last");
""")

    def test_rows_and_wall(self, integration_client):
        run_js("""
import { filterSites } from "@js/alerts/filters.js";
import { siteRowsHtml } from "@js/alerts/rows.js";
const planned = { id: "m", name: "Bergen", alert_level: "maintenance", maintenance: { until: "", reason: "Core <upgrade>" } };
const alarm = { id: "a", name: "Aarhus", alert_level: "critical" };
const criteria = { sort: "newest", severityFilter: "alarms" };
check(filterSites([planned, alarm], { ...criteria, wallView: true }).map((s) => s.name).join() === "Aarhus,Bergen", "the wall lists maintenance last");
check(filterSites([planned, alarm], { ...criteria, wallView: false }).map((s) => s.name).join() === "Aarhus", "the board's Alarms don't");
const wall = siteRowsHtml([alarm, planned], { wallView: true, isExpanded: () => true });
check(wall.includes('<tr class="maintenance-heading"><td colspan="5">In maintenance</td></tr>'), wall);
check(wall.indexOf("maintenance-heading") > wall.indexOf("Aarhus") && wall.includes("Core &lt;upgrade&gt;"), wall);
""")
        html = integration_client.get("/alerts").get_data(as_text=True)
        assert 'data-severity-filter="maintenance"' in html and 'id="summary-maintenance"' in html
        run_js("""
import { LEVEL_COLORS, displayLevel } from "@js/map/levels.js";
import { updateMarkerForAlert } from "@js/map/markers.js";
import { locationAlerts } from "@js/map/state.js";
// The site panel's live level doesn't turn a site in maintenance red.
locationAlerts["m"] = "maintenance";
updateMarkerForAlert("m", "critical");
check(displayLevel("m") === "maintenance", locationAlerts["m"]);
// Maintenance has its own colour in the one scale of pins, stacks and clusters (#311).
check(LEVEL_COLORS.maintenance === "#2563eb", LEVEL_COLORS.maintenance);
""")


class TestMaintenancePanelInTheBrowser:
    """Managing maintenance windows from the board (#283)."""

    def _run(self, body):
        run_js(
            """
import { maintenanceRequestBody, renderMaintenancePanel } from "@js/alerts/maintenance.js";
import { actionCell, maintenanceButton } from "@js/alerts/rows.js";
"""
            + body,
            tz="Europe/Copenhagen",
        )

    def test_request_body(self):
        self._run("""
const base = { siteId: "loc-1", scope: "site", deviceIds: [], when: "now", duration: "120", reason: " Core upgrade " };
let body = maintenanceRequestBody(base);
check(JSON.stringify(body) === '{"site_id":"loc-1","reason":"Core upgrade","duration_minutes":120}', JSON.stringify(body));
check(maintenanceRequestBody({ ...base, reason: " " }).error === "Enter a reason.", "reason");
check(maintenanceRequestBody({ ...base, scope: "devices" }).error.startsWith("Tick at least one device"), "devices");
body = maintenanceRequestBody({ ...base, scope: "devices", deviceIds: ["d1", "d2"] });
check(JSON.stringify(body.device_ids) === '["d1","d2"]', JSON.stringify(body));
// Planned: days from the calendar and times, the browser's local time (Copenhagen,
// +02:00 in October) sent as UTC (lib/when.js, #343).
const planned = { ...base, when: "planned", startDay: "2026-10-09", startTime: "22:00", endDay: "2026-10-10", endTime: "02:00" };
body = maintenanceRequestBody(planned);
check(body.starts_at === "2026-10-09T20:00:00.000Z" && body.ends_at === "2026-10-10T00:00:00.000Z", JSON.stringify(body));
check(!("duration_minutes" in body), "planned has no duration");
check(maintenanceRequestBody({ ...planned, endDay: "" }).error === "Choose the days and times.", "both");
check(maintenanceRequestBody({ ...planned, endDay: "2026-10-08" }).error === "To must be after From.", "order");
""")

    def test_panel(self):
        self._run("""
const devices = [{ id: "d1", name: "sw<1>", role: "Access", status: "Active", location_path: "Bygning A" }];
const windows = [
  { id: 7, state: "active", device_id: "", ends_at: "2026-10-09T14:00:00Z", reason: "Core <upgrade>", created_by: "olga" },
  { id: 8, state: "upcoming", device_id: "d1", starts_at: "2026-10-09T20:00:00Z", ends_at: "2026-10-10T00:00:00Z", reason: "PSU" },
];
let html = renderMaintenancePanel({ id: "loc-1" }, windows, devices);
check(html.includes("ACTIVE") && html.includes("<strong>Whole site</strong> · until ") && html.includes("Core &lt;upgrade&gt; · by olga"), html);
check(html.includes('data-window-id="7">End now<') && html.includes('data-window-id="8">Cancel<'), "end vs cancel");
check(html.includes("PLANNED") && html.includes("<strong>sw&lt;1&gt;</strong>"), "device window named");
check(html.includes('class="maint-device" value="d1"') && html.includes("Bygning A · Access · Active"), "device choice");
check(html.includes('<form class="maint-form" data-site-id="loc-1">'), "form");
// "When" is mounted into it (lib/when.js mountWhen, tested there): no typed dates (#343).
check(html.includes('<div class="maint-when"></div>') && !html.includes("datetime-local"), "when");
html = renderMaintenancePanel({ id: "loc-2" }, [], []);
check(html.includes("No maintenance now or planned.") && html.includes("No monitored devices."), html);
""")

    def test_button_only_where_there_is_something_to_maintain(self):
        self._run("""
check(maintenanceButton({ id: "a", name: "A", device_count: 3 }).includes('data-site-id="a"'), "monitored");
check(maintenanceButton({ id: "b", name: "B", device_count: 0 }) === "", "nothing to maintain");
check(maintenanceButton({ id: "c", name: "C", device_count: 0, maintenance: { id: 1 } }) !== "", "a window can still be ended");
check(maintenanceButton({ id: "d", name: "D", device_count: 0, maintenance_device_count: 2 }) !== "", "all devices in windows");
// In the row's menu with and without down devices.
check(actionCell({ id: "e", name: "E", device_count: 3 }).includes("maint-open-btn"), "nothing down");
check(actionCell({ id: "f", name: "F", device_count: 3, down_devices: [{ device_id: "x" }] }).includes("maint-open-btn"), "down");
""")

    def test_board_has_the_panel(self, integration_client):
        html = integration_client.get("/alerts").get_data(as_text=True)
        assert 'id="maint-panel"' in html and 'id="maint-content"' in html


class TestApiExplorerInTheBrowser:
    """Try it on the API explorer (#230)."""

    def _run(self, body):
        run_js(
            """
import { buildUrl, curlCommand, fillPath, formatResponseBody } from "@js/docs/explorer.js";
const MAX_BODY_CHARS = 50000;
"""
            + body
        )

    def test_urls(self):
        self._run("""
check(fillPath("/api/locations/<location_id>/detail", { location_id: "a b/c" }) === "/api/locations/a%20b%2Fc/detail", "encoded");
check(fillPath("/api/x/<string:id>", { id: "7" }) === "/api/x/7", "converter");
let url = buildUrl("/api/alert-feed", {}, { limit: "2", since: "", kinds: "down,up" });
check(url === "/api/alert-feed?limit=2&kinds=down%2Cup", url);
check(buildUrl("/api/alerts", {}, { refresh: "" }) === "/api/alerts", "empty query values are left out");
""")

    def test_curl_command(self):
        self._run("""
let cmd = curlCommand("GET", "/api/alerts?refresh=1", null, "http://maps.example");
check(cmd === "curl -sS 'http://maps.example/api/alerts?refresh=1'", cmd);
cmd = curlCommand("POST", "/api/alert-cases", '{"case_number": "it\\'s"}', "http://maps.example");
check(cmd === "curl -sS -X POST -H 'Content-Type: application/json' --data '{\\"case_number\\": \\"it'\\\\''s\\"}' 'http://maps.example/api/alert-cases'", cmd);
check(curlCommand("DELETE", "/api/x/1", null, "").startsWith("curl -sS -X DELETE '/api/x/1'"), "delete");
""")

    def test_response_bodies(self):
        self._run("""
check(formatResponseBody('{"a":[1,2]}', "application/json") === '{\\n  "a": [\\n    1,\\n    2\\n  ]\\n}', "pretty JSON");
check(formatResponseBody("not json", "application/json") === "not json", "broken JSON as it is");
check(formatResponseBody("metric 1", "text/plain") === "metric 1", "text");
const long = "x".repeat(MAX_BODY_CHARS + 5);
const shown = formatResponseBody(long, "text/html");
check(shown.startsWith("x".repeat(MAX_BODY_CHARS)) && shown.endsWith("(5 more characters)"), shown.slice(-40));
""")


# A stand-in for Leaflet and its layers, for the map's marker tests (#334).
# Markers record their icons; layers record what they hold.
MAP_FAKES = """
const made = [];
setGlobal("L", {
  divIcon: (options) => options,
  marker(latlng, options) {
    const marker = {
      latlng, options: { ...options }, icon: options.icon, iconSets: 0,
      on() { return this; }, bindPopup() { return this; }, getElement: () => null, getLatLng: () => latlng,
      addTo(layer) { layer.addLayer(this); return this; },
      setIcon(icon) { this.icon = icon; this.iconSets += 1; },
      setZIndexOffset(z) { this.options.zIndexOffset = z; },
    };
    made.push(marker);
    return marker;
  },
  circle: () => ({ addTo() { return this; } }),
});
function fakeLayer() {
  const held = new Set();
  return {
    held, refreshed: 0,
    addLayer(marker) { held.add(marker); },
    addLayers(markers) { markers.forEach((marker) => held.add(marker)); },
    clearLayers() { held.clear(); },
    hasLayer(marker) { return held.has(marker); },
    refreshClusters() { this.refreshed += 1; },
  };
}
const site = (id, lat, lon) => ({ id, name: id, latitude: lat, longitude: lon });
"""


class TestMapAlertColoursInTheBrowser:
    """Markers are coloured from /api/location-alerts, without a click (#234)."""

    def test_levels_are_applied_and_cleared(self):
        run_js(
            MAP_FAKES
            + """
import { displayLevel, worstAlertLevel } from "@js/map/levels.js";
import { onRedrawNeeded, renderMarkers } from "@js/map/markers.js";
import { loadLocationAlerts } from "@js/map/refresh.js";
import { detailCache, els, locationAlerts, markerByLocId, state } from "@js/map/state.js";

state.markerLayer = fakeLayer();
state.clusterLayer = fakeLayer();
let redraws = 0;
onRedrawNeeded(() => { redraws += 1; renderMarkers(state.allLocations); });
state.allLocations = [site("loc-old", 1, 1), site("loc-same", 2, 2), site("loc-a", 3, 3), site("loc-quiet", 4, 4)];
Object.assign(locationAlerts, { "loc-old": "medium", "loc-same": "low" });
renderMarkers(state.allLocations);
check(state.markerLayer.held.size === 2 && state.clusterLayer.held.size === 2, "alarms on top, the rest clustered");
detailCache.set("loc-a", {});
detailCache.set("loc-open", {});
state.selectedLocationId = "loc-open";
els.alertLevelsNote = element("note", { hidden: true });
console.warn = () => {};

// Never loaded: grey would read as "no data", so the page says so (#311).
globalThis.fetch = async () => { throw new Error("offline"); };
await loadLocationAlerts();
check(!els.alertLevelsNote.hidden, "first load failed: the note is shown");

// loc-old recovers and loc-a goes critical: both cross the cluster line, one redraw for both.
let payload = { levels: { "loc-a": { level: "critical" }, "loc-same": { level: "low" } } };
globalThis.fetch = async () => new Response(JSON.stringify(payload));
await loadLocationAlerts();
check(els.alertLevelsNote.hidden, "loaded: no note");
check(JSON.stringify(locationAlerts) === JSON.stringify({ "loc-same": "low", "loc-a": "critical" }), JSON.stringify(locationAlerts));
check(redraws === 1, "one redraw for every site that crossed: " + redraws);
check(state.markerLayer.hasLayer(markerByLocId["loc-a"]) && state.clusterLayer.hasLayer(markerByLocId["loc-old"]), "moved");
check(!detailCache.has("loc-a") && detailCache.has("loc-open"), "old site details are dropped, the open one kept");

// A quiet site gets data (No data -> OK): its pin and its cluster are recoloured, nothing redrawn.
payload = { levels: { ...payload.levels, "loc-quiet": { level: "ok" } } };
await loadLocationAlerts();
check(redraws === 1 && markerByLocId["loc-quiet"].iconSets === 1 && state.clusterLayer.refreshed === 1, "recoloured in place");

check(worstAlertLevel(["loc-same", "loc-a", "loc-none"]) === "critical", "worst level");
// Without a level a location has no monitored devices: grey, not green (#311).
check(worstAlertLevel(["loc-none"]) === "no_data" && displayLevel("loc-none") === "no_data", "no data");
locationAlerts["loc-fine"] = "ok";
check(worstAlertLevel(["loc-none", "loc-fine"]) === "ok", "OK beats no data");
locationAlerts["loc-planned-work"] = "maintenance";
check(worstAlertLevel(["loc-fine", "loc-planned-work"]) === "maintenance", "maintenance beats OK");
check(worstAlertLevel(["loc-planned-work", "loc-same"]) === "low", "an alarm beats maintenance");

globalThis.fetch = async () => { throw new Error("offline"); };
await loadLocationAlerts();
check(locationAlerts["loc-a"] === "critical", "a failed refresh keeps the colours");
check(els.alertLevelsNote.hidden, "after a first load, a failed refresh adds no note");
"""
        )


class TestMapRequestsInTheBrowser:
    """The map reads the API's answer as the board does (#350): lib/http.js getJson."""

    def test_search_and_detail_errors(self):
        run_js(
            MAP_FAKES
            + """
import { loadLocationDetail } from "@js/map/inspector.js";
import { doSearch } from "@js/map/search.js";
import { els, state } from "@js/map/state.js";

state.markerLayer = fakeLayer();
state.clusterLayer = fakeLayer();
state.allLocations = [site("loc-a", 1, 1)];
const toast = element("error-toast");
document.getElementById = (id) => (id === "error-toast" ? toast : null);
Object.assign(els, {
  searchInput: element("q", { value: "Atlantis" }),
  searchBtn: element("go", { style: {} }),
  searchResults: element("results"),
});
const page = new Response(
  "<html><head><title>502 Bad Gateway</title><style>body { color: red }</style></head><body><h1>502 Bad Gateway</h1></body></html>",
  { status: 502, headers: { "Content-Type": "text/html" } },
);

globalThis.fetch = async () => new Response(JSON.stringify({ error: "Address not found: Atlantis" }), { status: 404 });
await doSearch();
check(toast.textContent === "Address not found: Atlantis", toast.textContent);
check(els.searchResults.classList.contains("hidden") && !els.searchBtn.disabled, "results hidden, button back");

// An error page: its words, not a JSON parse error or its markup.
globalThis.fetch = async () => page.clone();
await doSearch();
check(toast.textContent === "502 Bad Gateway 502 Bad Gateway", toast.textContent);

globalThis.fetch = async () => new Response("", { status: 503 });
await doSearch();
check(toast.textContent === "Search failed: HTTP 503", toast.textContent);

els.searchResults.classList.remove("hidden");
globalThis.fetch = async () => { throw new Error("offline"); };
await doSearch();
check(toast.textContent === "Search failed: offline" && els.searchResults.classList.contains("hidden"), toast.textContent);

globalThis.fetch = async () => page.clone();
let failed = null;
await loadLocationDetail("loc-a").catch((err) => { failed = err; });
check(failed && failed.message === "502 Bad Gateway 502 Bad Gateway", String(failed));
"""
        )


class TestMapAtScaleInTheBrowser:
    """Sites that need attention are never clustered; the rest cluster quietly (#334)."""

    def test_which_sites_cluster(self):
        run_js(
            MAP_FAKES
            + """
import { groupByCoords, needsAttention, quietCluster, renderMarkers, splitGroups } from "@js/map/markers.js";
import { locationAlerts, markerByLocId, state } from "@js/map/state.js";

for (const level of ["critical", "medium", "low", "maintenance"]) check(needsAttention(level), level);
for (const level of ["ok", "no_data", undefined]) check(!needsAttention(level), String(level));

// 1,200 sites, 25 in alarm, 3 in maintenance; two co-located pairs.
const sites = Array.from({ length: 1200 }, (_, i) => site(`s${i}`, 40 + (i % 40) * 0.5, -5 + Math.floor(i / 40) * 0.8));
sites.push(site("pair-alarm", 60, 10), site("pair-ok", 60, 10), site("pair-q1", 61, 11), site("pair-q2", 61, 11));
for (let i = 0; i < 25; i += 1) locationAlerts[`s${i * 40}`] = ["critical", "medium", "low"][i % 3];
for (let i = 0; i < 3; i += 1) locationAlerts[`s${i * 40 + 7}`] = "maintenance";
locationAlerts["pair-alarm"] = "critical";
for (let i = 0; i < 1200; i += 1) if (!locationAlerts[`s${i}`] && i % 2) locationAlerts[`s${i}`] = "ok";
locationAlerts["pair-ok"] = "ok";

const { attention, quiet } = splitGroups(groupByCoords(sites));
check(attention.length === 29, "25 alarms + 3 maintenance + the pair with an alarm: " + attention.length);
check(attention.some((group) => group.length === 2), "a co-located pair with an alarm stays one stack on top");
check(quiet.length === 1202 - 29, "1,200 singles + 2 pairs, minus those on top: " + quiet.length);

state.markerLayer = fakeLayer();
state.clusterLayer = fakeLayer();
renderMarkers(sites);
check(state.markerLayer.held.size === 29, "pins on top: " + state.markerLayer.held.size);
const pins = [...state.markerLayer.held];
check(pins.every((marker) => marker.options.zIndexOffset >= 1000), "above clusters");
const critical = pins.find((marker) => locationAlerts[marker.options.locIds[0]] === "critical");
const maint = pins.find((marker) => locationAlerts[marker.options.locIds[0]] === "maintenance");
check(critical.options.zIndexOffset > maint.options.zIndexOffset, "worst on top");
check(markerByLocId["pair-q1"] === markerByLocId["pair-q2"] && state.clusterLayer.hasLayer(markerByLocId["pair-q1"]), "a quiet pair is one marker in a cluster");

// A cluster counts sites (a stack is one marker) and is never an alarm colour.
check(JSON.stringify(quietCluster([["a"], ["b", "c"]], () => "no_data")) === '{"count":3,"level":"no_data"}', "grey, 3 sites");
check(quietCluster([["a"], ["b"]], (id) => (id === "b" ? "ok" : "no_data")).level === "ok", "green with an OK site");

// Search results are all pins: a search finds few.
renderMarkers(sites.slice(0, 5), { lat: 40, lon: -5 });
check(state.clusterLayer.held.size === 0, "no clusters in a search");
"""
        )

    def test_level_changes_keep_the_search_and_the_order(self):
        """Review on #336: an OK search result must not redraw the map; a pin's order follows its level."""
        run_js(
            MAP_FAKES
            + """
import { onRedrawNeeded, refreshMarkerIcon, renderMarkers, updateMarkerForAlert } from "@js/map/markers.js";
import { locationAlerts, markerByLocId, state } from "@js/map/state.js";

state.markerLayer = fakeLayer();
state.clusterLayer = fakeLayer();
let redraws = 0;
onRedrawNeeded(() => { redraws += 1; });
const sites = [site("a", 1, 1), site("b", 2, 2), site("c", 3, 3)];
Object.assign(locationAlerts, { a: "critical", b: "medium" });

// A search: opening an OK result (its panel reports "ok") keeps the search on the map.
renderMarkers(sites, { lat: 1, lon: 1 });
updateMarkerForAlert("c", "ok");
locationAlerts.a = "ok";
refreshMarkerIcon("a");
check(redraws === 0, "no redraw in a search: " + redraws);

// The map: critical -> low moves the pin below the medium one.
Object.assign(locationAlerts, { a: "critical", b: "medium", c: undefined });
renderMarkers(sites);
check(markerByLocId.a.options.zIndexOffset > markerByLocId.b.options.zIndexOffset, "critical above medium");
locationAlerts.a = "low";
refreshMarkerIcon("a");
check(redraws === 0, "still a pin: no redraw");
check(markerByLocId.a.options.zIndexOffset < markerByLocId.b.options.zIndexOffset, "low below medium now");
"""
        )


class TestMapAlarmListInTheBrowser:
    """The sidebar's "In alarm (N)" list and "Alarms only" (#334)."""

    SETUP = """
import { alarmItemHtml, alarmSites, renderAlarmList, toggleAlarmList } from "@js/map/alarms.js";
import { applyFilters, clearFilters, setAlarmsOnly } from "@js/map/filters.js";
import { els, locationAlertInfo, locationAlerts, state } from "@js/map/state.js";

setGlobal("localStorage", { getItem: () => null, setItem() {}, removeItem() {} });
state.markerLayer = fakeLayer();
state.clusterLayer = fakeLayer();
for (const name of ["alarmList", "alarmCount", "alarmMore", "alarmBreakdown", "alarmsOnlyBtn", "locationCount",
  "filterStatus", "filterType", "filterParent", "filterTenant", "filterTenantGroup"]) {
  els[name] = element(name);
}
// 40 sites: 25 in alarm (9 critical, 8 medium, 8 low), 2 in maintenance, the rest OK.
state.allLocations = Array.from({ length: 40 }, (_, i) => ({ ...site(`s${i}`, i, i), name: `Site ${String(i).padStart(2, "0")}`, tenant: i % 2 ? "Odd" : "Even" }));
for (let i = 0; i < 40; i += 1) {
  const level = i < 25 ? ["critical", "medium", "low"][i % 3] : i < 27 ? "maintenance" : "ok";
  const since = i < 25 ? new Date(Date.UTC(2026, 9, 12, 8, i)).toISOString() : undefined;
  locationAlertInfo[`s${i}`] = { level, since, down: 2, monitored: 8 };
  locationAlerts[`s${i}`] = level;
}
state.levelsLoaded = true;
const rows = () => (els.alarmList.innerHTML.match(/class="alarm-item"/g) || []).length;
const ids = () => [...els.alarmList.innerHTML.matchAll(/data-location-id="([^"]+)"/g)].map((m) => m[1]);
"""

    def _run(self, body):
        run_js(MAP_FAKES + self.SETUP + body, tz="UTC")

    def test_worst_first_then_newest_and_eight_at_a_time(self):
        self._run("""
applyFilters();
check(els.alarmCount.textContent === "25 in alarm", "maintenance is not an alarm: " + els.alarmCount.textContent);
// The count per level (#392).
check(els.alarmBreakdown.textContent === "9 critical · 8 medium · 8 low", els.alarmBreakdown.textContent);
check(rows() === 8 && !els.alarmMore.hidden && els.alarmMore.textContent === "Show all 25", els.alarmMore.textContent);
// Critical first (s0, s3, ... s24), newest first within it.
check(ids().slice(0, 3).join() === "s24,s21,s18", ids().join());
toggleAlarmList();
check(rows() === 25 && els.alarmMore.textContent === "Show fewer", "all of them");
check(ids().indexOf("s1") > ids().indexOf("s0") && ids().at(-1) === "s2", "medium after critical, low last: " + ids().join());
toggleAlarmList();
check(rows() === 8, "back to eight");
""")

    def test_rows_say_what_and_since_when(self):
        self._run("""
const html = alarmItemHtml({ loc: { id: "x&1", name: "Bank <HQ>" }, level: "critical", down: 3, monitored: 7, since: "2026-10-12T08:05:00Z" },
  new Date("2026-10-12T12:00:00Z"));
check(html.includes('data-location-id="x&amp;1"') && html.includes("Bank &lt;HQ&gt;") && !html.includes("<HQ>"), html);
// The time is in the viewer's locale ("08:05" or "08:05 AM").
check(html.includes("dot-critical") && html.includes(">3/7 down · since 08:05"), html);
check(html.includes('aria-label="Critical: Bank &lt;HQ&gt;, 3/7 down · since 08:05'), html);
// Without a time (not a board row): no "since".
check(!alarmItemHtml({ loc: { id: "y", name: "Y" }, level: "low", down: 1, monitored: 4 }).includes("since"), "no time");
check(alarmSites([{ id: "q", name: "Q" }], { q: { level: "ok" } }).length === 0, "OK is not in the list");
""")

    def test_alarms_only_and_the_filters(self):
        self._run("""
setAlarmsOnly(true, { remember: false });
applyFilters();
check(state.markerLayer.held.size === 25 && state.clusterLayer.held.size === 0, "only the 25 alarms on the map");
check(els.locationCount.textContent === "25 sites in alarm", els.locationCount.textContent);
check(els.alarmsOnlyBtn.attrs["aria-pressed"] === "true", "pressed");
// The list follows the sidebar's filters.
els.filterTenant.value = "Odd";
applyFilters();
check(els.alarmCount.textContent === "12 in alarm" && ids().every((id) => Number(id.slice(1)) % 2), els.alarmCount.textContent);
// Clear Filters also turns Alarms only off.
clearFilters();
check(!state.alarmsOnly && els.alarmCount.textContent === "25 in alarm" && state.clusterLayer.held.size > 0, "cleared");
""")

    def test_alarms_only_waits_for_the_levels_and_keeps_a_search(self):
        """Review on #337: no empty map before the levels; no redraw over a search."""
        self._run("""
import { loadLocationAlerts } from "@js/map/refresh.js";
import { onRedrawNeeded, renderMarkers } from "@js/map/markers.js";
// Alarms only remembered, levels not in yet (or failed): every site, not an empty map.
state.levelsLoaded = false;
setAlarmsOnly(true, { remember: false });
applyFilters();
check(state.markerLayer.held.size + state.clusterLayer.held.size > 25, "every site is drawn");
check(!els.locationCount.textContent.includes("in alarm"), els.locationCount.textContent);

// A search is shown; a level changes on the minute refresh: the search stays.
state.levelsLoaded = true;
let redraws = 0;
onRedrawNeeded(() => { redraws += 1; });
renderMarkers(state.allLocations.slice(0, 3), { lat: 0, lon: 0 });
const levels = Object.fromEntries(Object.entries(locationAlertInfo).map(([id, info]) => [id, { ...info }]));
levels.s0.level = "ok";
globalThis.fetch = async () => new Response(JSON.stringify({ levels }));
await loadLocationAlerts();
check(locationAlerts.s0 === "ok" && redraws === 0, "no redraw over a search: " + redraws);
""")

    def test_before_the_levels_and_without_alarms(self):
        self._run("""
state.levelsLoaded = false;
renderAlarmList();
check(els.alarmList.innerHTML.includes("not loaded yet") && els.alarmMore.hidden, "not yet");
state.levelsLoaded = true;
// Filtered to sites without alarms: not "All sites OK", other sites may have them (#392).
state.filteredLocations = state.allLocations.slice(27);
renderAlarmList();
check(els.alarmCount.textContent === "No site in alarm" && els.alarmList.innerHTML === "" && els.alarmBreakdown.textContent === "", els.alarmCount.textContent);
// Every site, none in alarm: all OK.
state.allLocations = state.allLocations.slice(27);
state.filteredLocations = state.allLocations;
renderAlarmList();
check(els.alarmCount.textContent === "All sites OK", els.alarmCount.textContent);
""")


class TestMapCircuitsInTheBrowser:
    """The site panel shows each circuit at the site (#235)."""

    def test_circuit_card(self):
        run_js("""
import { buildCircuitCard } from "@js/map/panel.js";
import { config } from "@js/map/state.js";
config.nautobotUrl = "https://nautobot.example";

let html = buildCircuitCard({
  id: "cir-1", cid: "TEL<1>", provider: "Telia", circuit_type: "Transit", status: "Offline",
  term_side: "A", port_speed: "1 Gbps", upstream_speed: "500 Mbps", commit_rate: "", xconnect_id: "XC-1",
  pp_info: "", tenant: "", description: "",
});
check(html.includes('href="https://nautobot.example/circuits/circuits/cir-1/"'), html);
check(html.includes("TEL&lt;1&gt;"), "circuit ID is escaped");
check(html.includes("device-status-offline"), "an offline circuit is red");
check(html.includes("Telia · Transit"), html);
check(html.includes("Side A · 1 Gbps down / 500 Mbps up"), html);
check(html.includes("Cross-connect: XC-1") && !html.includes("Patch panel"), html);

config.nautobotUrl = "";
html = buildCircuitCard({ id: "cir-2", cid: "C2", status: "Active", port_speed: "10 Gbps", upstream_speed: "10 Gbps" });
check(!html.includes("<a "), "no link without a Nautobot URL");
check(html.includes("device-status-active") && html.includes(">10 Gbps<"), html);
""")


class TestSiteTenantsInTheBrowser:
    """The tenant line under the site name, on the board and the wall, and Copy for ITSM show every tenant of a site (#238, #351)."""

    def test_tenant_cell_and_report(self):
        run_js("""
import { siteReport } from "@js/alerts/panels.js";
import { siteRowsHtml, tenantCell, tenantLine } from "@js/alerts/rows.js";

const line = (inner) => inner;
check(tenantCell({}) === "—", "no tenant: a dash");
check(tenantCell({ tenant: "Acme" }) === line("Acme"), "a board cached before #238 has only tenant");
check(tenantCell({ tenant: "Acme", tenants: ["Acme"] }) === line("Acme"), "one tenant");

// The first one, and "+N" that shows the rest in place, each with its (i) (#279).
let cell = tenantCell({ tenants: ["Acme", "B&B"] });
check(cell === line('Acme<span class="tenant-rest" hidden>, B&amp;B</span> <button class="tenant-more-btn" type="button" title="B&amp;B">+1</button>'), cell);
cell = tenantCell({ tenants: ["A", "B", "C", "Hidden"], tenant_descriptions: { Hidden: "Reach me" } });
check(cell.includes(">+3</button>") && cell.includes('hidden>, B, C, Hidden<span class="info-tip"') && cell.includes('data-info-tip="Reach me"'), cell);

let report = siteReport({ name: "LON", tenant: "Acme", tenants: ["Acme", "Nordic"], alert_level: "low" });
check(report.includes("Tenants (2): Acme, Nordic") && !report.includes("Tenant: "), report);
report = siteReport({ name: "STO", tenant: "Acme", alert_level: "ok" });
check(report.includes("Tenant: Acme"), report);

// The (i) only for tenants with a description (#263), escaped.
cell = tenantCell({ tenant: "Acme", tenants: ["Acme"], tenant_descriptions: { Acme: 'Bank <"VIP">' } });
check(cell.startsWith('Acme<span class="info-tip"'), cell);
check(cell.includes('data-info-tip="Bank &lt;&quot;VIP&quot;&gt;"') && !cell.includes('<"VIP">'), cell);
check(cell.includes('aria-label="Acme: Bank') && cell.includes('tabindex="0"'), cell);
cell = tenantCell({ tenants: ["Acme", "Nordic"], tenant_descriptions: { Nordic: "Shared rack" } });
check(cell.includes("Acme<span") && cell.includes(", Nordic<span") && (cell.match(/info-tip"/g) || []).length === 1, cell);
check(tenantCell({ tenant: "Acme", tenant_descriptions: {} }) === line("Acme"), "no description: no (i)");
// Inherited object properties are not descriptions (Copilot review on #264).
check(tenantCell({ tenant: "constructor", tenant_descriptions: {} }) === line("constructor"), "constructor");
check(tenantCell({ tenants: ["toString", "hasOwnProperty"] }).indexOf("info-tip") === -1, "toString");

// Under the site's name (#351), with the (i) on the board and on the wall;
// no tenant, no line; five cells, the down-device rows lined up.
check(tenantLine({}) === "", "no tenant: no line");
check(tenantLine({ tenant: "Acme" }) === '<div class="site-tenants">Acme</div>', tenantLine({ tenant: "Acme" }));
const site = { id: "s1", name: "Oslo", tenants: ["Acme", "Nordic"], tenant_descriptions: { Acme: "Gold" },
  alert_level: "critical", down_device_count: 1, down_devices: [{ device_id: "d1", device_name: "sw1" }] };
for (const wallView of [false, true]) {
  const html = siteRowsHtml([site], { wallView, isExpanded: () => true });
  const siteRow = html.slice(html.indexOf('<tr class="site-row'), html.indexOf("</tr>"));
  check(siteRow.match(/<td[ >]/g).length === 5, `five cells (wall: ${wallView})`);
  const name = siteRow.indexOf("Oslo");
  const line = siteRow.indexOf('<div class="site-tenants">Acme<span class="info-tip"');
  check(line > name && line < siteRow.indexOf("alert-badge") && siteRow.includes('data-info-tip="Gold"'), siteRow);
  check(siteRow.includes(">+1</button>") && (html.match(/class="site-tenants"/g) || []).length === 1, "+N, once");
  const deviceRow = html.slice(html.indexOf('<tr class="down-device-row'));
  check(deviceRow.slice(0, deviceRow.indexOf("</tr>")).match(/<td[ >]/g).length === 5, deviceRow);
}
""")

    def test_full_width_rows_span_every_column(self, integration_client):
        # One column count for the empty, error and heading rows (#340, #351).
        html = integration_client.get("/alerts").get_data(as_text=True)
        thead = html[html.index("<thead>") : html.index("</thead>")]
        columns = len(re.findall(r"<th[ >]", thead))
        assert f'<td colspan="{columns}" class="empty-state">Loading' in html
        run_js(f"""
import {{ fullWidthRow }} from "@js/alerts/rows.js";
const row = fullWidthRow("No <b>sites</b>", "empty-state", "maintenance-heading");
check(row === '<tr class="maintenance-heading"><td colspan="{columns}" class="empty-state">No <b>sites</b></td></tr>', row);
check(fullWidthRow("x") === '<tr><td colspan="{columns}">x</td></tr>', fullWidthRow("x"));
""")


class TestSimplerBoardInTheBrowser:
    """Only what is needed to decide (#311)."""

    def _run(self, body):
        run_js(
            """
import { formatDuration } from "@js/lib/format.js";
import { alertBadge, summaryText } from "@js/alerts/rows.js";
"""
            + body
        )

    def test_one_summary_line_only_with_what_is_wrong(self):
        self._run("""
check(summaryText({ non_ok: 2, critical: 1, medium: 1, low: 0, ok: 7 }) === "2 alarms · 1 critical · 1 medium", "levels at 0 left out");
check(summaryText({ non_ok: 1, low: 1 }) === "1 alarm · 1 low", "singular");
check(summaryText({ non_ok: 0, ok: 9, no_data: 4 }) === "No alarms", "nothing wrong");
""")

    def test_long_times_in_days(self):
        self._run("""
check(formatDuration(6787 * 3600 + 44 * 60) === "282 days", formatDuration(6787 * 3600));
check(formatDuration(47 * 3600 + 5 * 60) === "47h 5m", "under two days: hours");
check(formatDuration(48 * 3600) === "2 days", "two days");
check(formatDuration(600) === "10m", "minutes");
""")

    def test_the_reason_is_the_badge_hover_text(self):
        self._run("""
const html = alertBadge("medium", "2/7 devices <offline>");
// Hover and keyboard focus (the shared tooltip), and the accessible name.
check(html === '<span class="alert-badge alert-medium" tabindex="0" role="img" aria-label="MEDIUM: 2/7 devices &lt;offline&gt;" data-info-tip="2/7 devices &lt;offline&gt;">MEDIUM</span>', html);
check(!alertBadge("ok").includes("tabindex"), "no reason: nothing to focus");
""")


class TestMapPanelSameLevelInTheBrowser:
    """The site panel shows the board's level and count, and why a device isn't counted (#316)."""

    def _run(self, body):
        run_js(
            """
import { applyLevelToOpenPanel } from "@js/map/inspector.js";
import { deviceCountNote } from "@js/map/panel.js";
import { detailCache, els, state } from "@js/map/state.js";

// The panel's elements; every redraw of its content is counted.
for (const name of ["inspector", "inspectorTitle", "inspectorSubtitle", "inspectorTabs", "inspectorState"]) {
  els[name] = element(name);
}
let renders = 0;
let content = "";
els.inspectorContent = element("content");
Object.defineProperty(els.inspectorContent, "innerHTML", { get: () => content, set: (html) => { renders += 1; content = html; } });
setGlobal("L", { divIcon: (options) => options });
state.allLocations = [{ id: "loc-lon", name: "London" }];
Object.assign(state, {
  selectedLocationId: "loc-lon", inspectorLoading: false, inspectorError: "",
  selectedDetail: { devices: [], alert: { level: "medium", reason: "2/7" }, counts: { down: 2, monitored: 7 } },
});
detailCache.set("loc-lon", { stale: true });
const loads = [];
globalThis.fetch = async (url) => {
  loads.push(url);
  return new Response(JSON.stringify({ devices: [{ name: "sw1", maintenance_until: "2026-10-10T21:00:00Z" }], alert: { level: "critical" }, counts: { down: 3, monitored: 7 } }));
};
"""
            + body
        )

    def test_device_notes(self):
        self._run("""
check(deviceCountNote({}) === "", "counted: no note");
check(deviceCountNote({ not_monitored: "no primary IP" }).includes(">Not monitored: no primary IP<"), "no IP");
const m = deviceCountNote({ maintenance_until: "2026-10-10T21:43:00Z" });
check(m.includes("In maintenance until ") && m.includes(": not counted"), m);
check(deviceCountNote({ alert_pending: true }).includes("not an alarm yet"), "pending");
check(deviceCountNote({ not_monitored: "<x>" }).includes("&lt;x&gt;"), "escaped");
""")

    def test_the_open_panel_follows_a_refresh_only_when_it_changes(self):
        self._run("""
applyLevelToOpenPanel({ "loc-lon": { level: "medium", reason: "2/7", down: 2, monitored: 7 } });
check(renders === 0 && loads.length === 0, "nothing changed: not re-drawn or reloaded (a search being typed stays)");
applyLevelToOpenPanel({ "loc-lon": { level: "critical", reason: "Core down", down: 3, monitored: 7 } });
check(renders === 1 && state.selectedDetail.alert.level === "critical" && state.selectedDetail.counts.down === 3, JSON.stringify(state.selectedDetail));
check(content.includes("Critical · 3 of 7 down"), "the panel shows it");
// ...and the devices' notes are reloaded (not the stale cache).
check(loads.join() === "/api/locations/loc-lon/detail" && !detailCache.has("loc-lon"), "reloaded: " + loads);
await new Promise((resolve) => setTimeout(resolve, 0));
check(renders === 2 && state.selectedDetail.devices[0].maintenance_until, JSON.stringify(state.selectedDetail));
check(content.includes(">Maintenance<"), "the device says why it isn't counted");
""")


class TestMapFirstViewInTheBrowser:
    """The map opens on the sites, or on MAP_DEFAULT_VIEW, not the whole world (#362)."""

    def test_fit_and_fixed_view(self):
        run_js(
            """
import { fitToSites } from "@js/map/view.js";

setGlobal("L", { latLngBounds: (points) => ({ points }) });
const fits = [];
const map = { fitBounds: (bounds, options) => fits.push({ bounds, options }) };
const sites = [
  { id: "a", latitude: 55.68, longitude: 12.57 },
  { id: "no-coordinates", latitude: null, longitude: null },
  { id: "never-set", latitude: 0, longitude: 0 },
  { id: "b", latitude: 51.5, longitude: 0 },
];

fitToSites(map, sites);
check(fits.length === 1, "one fit");
check(JSON.stringify(fits[0].bounds.points) === "[[55.68,12.57],[51.5,0]]", "0,0 and no coordinates left out: " + JSON.stringify(fits[0].bounds));
check(fits[0].options.maxZoom === 10, "no closer than a city: " + fits[0].options.maxZoom);

fitToSites(map, [sites[1], sites[2]]);
check(fits.length === 1, "no site to fit: the view stays");
"""
        )


# The board's elements loadAlertBoard draws into (#364 tests).
BOARD_ELS = """
const boardEls = ["boardStatus", "alertsTableBody", "refreshBtn", "filterSite", "filterStatus", "filterType",
  "filterTenant", "sortBy", "summaryAlarms", "summaryNoData", "summaryMaintenance", "summaryTotal", "wallUpdatedEl"];
boardEls.forEach((name) => { els[name] = element(name); });
els.sortBy.value = "newest";
"""


class TestScopedBoardInTheBrowser:
    """?tenant= (repeatable) and ?q=: a board or wall for some sites only (#364)."""

    def test_scope_from_the_link(self):
        run_js(
            """
import { inScope, readScope, scopeLabel, scopeSummary } from "@js/alerts/scope.js";

check(readScope("") === null && readScope("?view=wall") === null && readScope("?tenant=+&q=") === null, "no scope");
const scope = readScope("?view=wall&tenant=acme%20corp&tenant=Nordic%20Net&q=DNK");
check(JSON.stringify(scope.tenants) === '["acme corp","Nordic Net"]' && JSON.stringify(scope.texts) === '["DNK"]', JSON.stringify(scope));
// Several q= (#366): every one must match.
const two = readScope("?q=EMEA&q=%20DNK%20");
check(JSON.stringify(two.texts) === '["EMEA","DNK"]' && scopeLabel(two) === "“EMEA” · “DNK”", scopeLabel(two));
check(scopeLabel(scope) === "acme corp, Nordic Net · “DNK”", "as typed before the sites are in: " + scopeLabel(scope));
check(scopeLabel(readScope("?q=EMEA")) === "“EMEA”", scopeLabel(readScope("?q=EMEA")));

const aarhus = { name: "Aarhus", ancestor_path: "EMEA › DNK", tenant: "Acme Corp", alert_level: "critical" };
const oslo = { name: "Oslo", ancestor_path: "EMEA › NOR", tenant: "Nordic Net", alert_level: "low" };
const lyon = { name: "Lyon", ancestor_path: "EMEA › FRA", tenant: "EuroIX", alert_level: "maintenance" };
check(inScope(aarhus, scope) && !inScope(oslo, scope) && !inScope(lyon, scope), "tenants (any, any case) and the path");
check(inScope(aarhus, two) && !inScope(lyon, two), "every q= word");
const tenants = readScope("?tenant=ACME%20CORP&tenant=nordic%20net");
check(inScope(aarhus, tenants) && inScope(oslo, tenants) && !inScope(lyon, tenants), "either tenant");
check(inScope(lyon, null), "no scope: every site");
check(scopeLabel(scope, [aarhus, oslo]) === "Acme Corp, Nordic Net · “DNK”", "as Nautobot spells them: " + scopeLabel(scope, [aarhus, oslo]));

const summary = scopeSummary([aarhus, oslo, lyon, { name: "x" }]);
check(JSON.stringify(summary) === '{"total":4,"critical":1,"low":1,"maintenance":1,"no_data":1}', JSON.stringify(summary));
"""
        )

    def test_board_and_wall_show_only_those_sites(self):
        run_js(
            """
import { loadAlertBoard } from "@js/alerts/board.js";
import { readScope } from "@js/alerts/scope.js";
import { config, els, state } from "@js/alerts/state.js";
import { renderWallStatus } from "@js/alerts/wall.js";
"""
            + BOARD_ELS
            + """const sites = [
  { id: "a", name: "Aarhus", tenant: "Acme Corp", alert_level: "critical", down_device_count: 1, down_devices: [{ device_name: "a1" }] },
  { id: "c", name: "Copenhagen", tenant: "Acme Corp", alert_level: "ok" },
  { id: "o", name: "Oslo", tenant: "Nordic Net", alert_level: "medium", down_device_count: 2, down_devices: [{ device_name: "o1" }] },
];
// The server's summary counts every site; the scoped board counts its own.
const payload = { alerts: sites, summary: { total: 3, critical: 1, medium: 1, non_ok: 2 } };
globalThis.fetch = async () => new Response(JSON.stringify(payload));

config.wallView = true;
config.scope = readScope("?view=wall&tenant=Acme%20Corp");
await loadAlertBoard(false, { background: true });
check(state.allAlerts.map((s) => s.id).join() === "a,c", state.allAlerts.map((s) => s.id).join());
check(els.summaryAlarms.textContent === 1 && els.summaryTotal.textContent === 2,
  `alarms ${els.summaryAlarms.textContent}, total ${els.summaryTotal.textContent}`);
check(els.alertsTableBody.innerHTML.includes("Aarhus") && !els.alertsTableBody.innerHTML.includes("Oslo"), "rows");
renderWallStatus(state.lastLoadedAt);
check(els.wallUpdatedEl.textContent.startsWith("Acme Corp · Updated ")
  && els.wallUpdatedEl.textContent.includes("1 site with alarms"), els.wallUpdatedEl.textContent);

// A tenant with nothing in alarm: the all-clear, for whom.
config.scope = readScope("?view=wall&tenant=acme%20corp");
payload.alerts = sites.filter((s) => s.id !== "a");
await loadAlertBoard(false, { background: true });
check(els.alertsTableBody.innerHTML.includes("No active alarms for Acme Corp.")
  && els.alertsTableBody.innerHTML.includes("all-clear"), els.alertsTableBody.innerHTML);

// A link that matches no site (a typo, a renamed tenant) is a warning, never the all-clear.
config.scope = readScope("?view=wall&tenant=Acme%20Crop");
await loadAlertBoard(false, { background: true });
const empty = els.alertsTableBody.innerHTML;
check(empty.includes("No site matches Acme Crop. Check the link") && empty.includes("scope-no-match") && !empty.includes("all-clear"), empty);

// The link's text is the visitor's: shown, never markup.
config.scope = readScope("?view=wall&q=" + encodeURIComponent('<img src=x onerror="alert(1)">'));
await loadAlertBoard(false, { background: true });
const escaped = els.alertsTableBody.innerHTML;
check(!escaped.includes("<img") && escaped.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"), escaped);
""",
            tz="UTC",
        )

    def test_at_scale(self):
        """1,200 sites, 30 tenants, ~100 down devices: one tenant's wall, quickly."""
        run_js(
            """
import { loadAlertBoard } from "@js/alerts/board.js";
import { readScope } from "@js/alerts/scope.js";
import { config, els, state } from "@js/alerts/state.js";
"""
            + BOARD_ELS
            + """const levels = ["critical", "medium", "low"];
const sites = Array.from({ length: 1200 }, (_, i) => {
  const down = i % 40 === 0 ? 3 : 0; // 30 sites in alarm, ~90 devices down
  return {
    id: `s${i}`, name: `Site ${i}`, ancestor_path: `EMEA › C${i % 12}`, tenant: `Tenant ${i % 30}`,
    tenants: [`Tenant ${i % 30}`, `Tenant ${(i + 7) % 30}`],
    alert_level: down ? levels[i % 3] : "ok", down_device_count: down,
    down_devices: Array.from({ length: down }, (_, d) => ({ device_name: `s${i}-sw${d}`, device_id: `d${i}-${d}` })),
  };
});
sites[0].down_devices = Array.from({ length: 34 }, (_, d) => ({ device_name: `s0-sw${d}`, device_id: `d0-${d}` }));
sites[0].down_device_count = 34;
globalThis.fetch = async () => new Response(JSON.stringify({ alerts: sites, summary: {} }));
config.wallView = true;
config.scope = readScope("?view=wall&tenant=tenant%200");
const started = performance.now();
await loadAlertBoard(false, { background: true });
const took = performance.now() - started;
// Tenant 0: its own sites (i % 30 == 0) and those it is linked to ((i + 7) % 30 == 0).
check(state.allAlerts.length === 80, "sites of tenant 0: " + state.allAlerts.length);
check(state.allAlerts.every((s) => s.tenants.includes("Tenant 0")), "only its sites");
check(els.summaryTotal.textContent === 80, "tiles count its sites: " + els.summaryTotal.textContent);
check(took < 1000, `${took.toFixed(0)} ms for 1,200 sites`);
"""
        )


class TestScopedLinksInTheBrowser:
    """Copy wall link (#366) and the map for some sites only (#367)."""

    def test_wall_link_from_the_board_filters(self):
        run_js(
            """
import { wallLinkFor } from "@js/alerts/filters.js";
import { readScope } from "@js/lib/scope.js";

check(wallLinkFor({ tenant: "", text: "  ", scope: null }) === null, "nothing narrows the board: no link");
check(wallLinkFor({ tenant: "Acme Corp", text: "", scope: null }) === "/alerts?view=wall&tenant=Acme+Corp", "the tenant menu");
check(wallLinkFor({ tenant: "", text: " DNK ", scope: null }) === "/alerts?view=wall&q=DNK", "the filter box");
const scope = readScope("?tenant=Acme%20Corp&tenant=Nordic%20Net&q=EMEA");
check(wallLinkFor({ tenant: "", text: "", scope }) === "/alerts?view=wall&tenant=Acme+Corp&tenant=Nordic+Net&q=EMEA", "the link's scope");
// The box narrows the link's q=, never replaces it: the wall is no wider than the board.
check(wallLinkFor({ tenant: "Nordic Net", text: "Oslo", scope }) === "/alerts?view=wall&tenant=Nordic+Net&q=EMEA&q=Oslo",
  wallLinkFor({ tenant: "Nordic Net", text: "Oslo", scope }));
check(wallLinkFor({ tenant: "", text: "a&b=<c>", scope: null }) === "/alerts?view=wall&q=a%26b%3D%3Cc%3E", "encoded");
"""
        )

    def test_map_scope_note_and_search(self):
        run_js(
            MAP_FAKES
            + """
import { readScope } from "@js/lib/scope.js";
import { linkedSiteOutOfScope, showScope } from "@js/map/filters.js";
import { doSearch } from "@js/map/search.js";
import { config, els, state } from "@js/map/state.js";

state.markerLayer = fakeLayer();
state.clusterLayer = fakeLayer();
els.mapScope = element("map-scope", { hidden: true });
document.createElement = (tag) => element(tag);
els.mapScope.replaceChildren = (...children) => {
  els.mapScope.children = children;
  els.mapScope.text = children.map((child) => (typeof child === "string" ? child : child.textContent)).join("");
};

config.scope = readScope("?tenant=acme%20corp");
state.allLocations = [{ id: "a", name: "Aarhus", tenant: "Acme Corp", latitude: 56, longitude: 10 }];
showScope(1200);
check(!els.mapScope.hidden && els.mapScope.text === "Only Acme Corp · Show all sites", els.mapScope.text);
check(!els.mapScope.classList.contains("scope-no-match"), "sites match");

state.allLocations = [];
showScope(1200);
check(els.mapScope.classList.contains("scope-no-match")
  && els.mapScope.text === "No site matches acme corp. Check the link's tenant= and q=. Show all sites"
  && els.mapScope.children.at(-1).href === "/", els.mapScope.text);
// Shown again after a no-match: the note is whole again, not a stale warning.
state.allLocations = [{ id: "a", name: "Aarhus", tenant: "Acme Corp", latitude: 56, longitude: 10 }];
showScope(1200);
check(els.mapScope.text === "Only Acme Corp · Show all sites" && !els.mapScope.classList.contains("scope-no-match"), els.mapScope.text);

// A link to a site the scope leaves out says why it doesn't open.
state.initialLocationId = "n1";
check(linkedSiteOutOfScope([{ id: "a" }, { id: "n1" }]), "outside the scope");
check(!linkedSiteOutOfScope([{ id: "a" }]), "gone altogether: nothing to say");
state.initialLocationId = "a";
check(!linkedSiteOutOfScope([{ id: "a" }]), "in the scope");
state.initialLocationId = null;

// Search on a scoped map: the nearby sites among them only.
const toast = element("error-toast");
document.getElementById = (id) => (id === "error-toast" ? toast : null);
Object.assign(els, {
  searchInput: element("q", { value: "Copenhagen" }),
  searchBtn: element("go", { style: {} }),
  searchResults: element("results"),
  searchResultsHeader: element("header"),
  searchResultsList: element("list", { appendChild() {} }),
});
state.map = { flyTo() {} };
globalThis.fetch = async () => new Response(JSON.stringify({ search_lat: 55.7, search_lon: 12.6, count: 2, locations: [
  { id: "c1", name: "CPH DC", tenant: "Acme Corp", latitude: 55.7, longitude: 12.6, distance_km: 0.1 },
  { id: "c2", name: "CPH Colo", tenant: "Nordic Net", latitude: 55.7, longitude: 12.6, distance_km: 1.2 },
] }));
await doSearch();
check(els.searchResultsHeader.textContent === "1 location within 5 km", els.searchResultsHeader.textContent);
"""
        )
