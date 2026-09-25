"""
Integration tests for the Nautobot Maps application.

These tests start a *real* mock-Nautobot HTTP server (werkzeug) in a background
thread, configure the Flask app to point to it, and exercise every API endpoint
end-to-end – no mocking of app internals.

Run with:
    python -m pytest tests/test_integration.py -v
"""

import pathlib
import re
import shutil
import subprocess
import threading

import mock_nautobot  # provided via tests/conftest.py path injection
import pytest
from werkzeug.serving import make_server

import app as flask_app

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _extract_js_function(source, name):
    token = f"function {name}("
    start = source.index(token)
    brace_start = source.index("{", start)
    depth = 0
    for idx in range(brace_start, len(source)):
        char = source[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    raise ValueError(f"Could not extract function {name}")


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
    flask_app.NAUTOBOT_URL = mock_nautobot_server
    flask_app.NAUTOBOT_TOKEN = "demo-token"
    flask_app.cache.clear()

    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "integration-test-secret"

    with flask_app.app.test_client() as client:
        yield client

    # Restore to blank so other test modules don't accidentally hit the server
    flask_app.NAUTOBOT_URL = ""
    flask_app.NAUTOBOT_TOKEN = ""


# ---------------------------------------------------------------------------
# Helper – clear the cache between tests so each test gets a fresh request
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def clear_cache():
    flask_app.cache.clear()
    yield
    flask_app.cache.clear()


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
        resp = integration_client.get("/static/js/map.js")
        assert resp.status_code == 200

    def test_js_contains_location_inspector_flow(self, integration_client):
        """The JS bundle includes the persistent location inspector logic."""
        resp = integration_client.get("/static/js/map.js")
        js = resp.data.decode()
        assert "openInspectorForLocation" in js
        assert "loadLocationDetail" in js
        assert "detailCache" in js
        assert "inspector-device-search" in js
        assert "Loading alert status…" in js

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
        if shutil.which("node") is None:
            pytest.skip("node is required for the inspector runtime helper regression test")
        js = (REPO_ROOT / "static" / "js" / "map.js").read_text(encoding="utf-8")
        schedule_map_resize = _extract_js_function(js, "scheduleMapResize")
        wire_marker_accessibility = _extract_js_function(js, "wireMarkerAccessibility")

        script = f"""
{schedule_map_resize}
{wire_marker_accessibility}
const rafCallbacks = [];
global.window = {{
  requestAnimationFrame(callback) {{
    rafCallbacks.push(callback);
    return rafCallbacks.length;
  }},
}};
let invalidations = 0;
const initialMap = {{
  invalidateSize() {{
    invalidations += 1;
  }},
  getContainer() {{
    return {{ id: "map" }};
  }},
}};
let map = initialMap;
scheduleMapResize();
if (rafCallbacks.length !== 1) {{
  throw new Error(`expected one queued animation frame, got ${{rafCallbacks.length}}`);
}}
map = {{
  invalidateSize() {{
    throw new Error("resize should use the originally scheduled map instance");
  }},
  getContainer() {{
    return {{ id: "replacement-map" }};
  }},
}};
rafCallbacks.shift()();
if (invalidations !== 0 || rafCallbacks.length !== 1) {{
  throw new Error("resize should wait for the second animation frame");
}}
rafCallbacks.shift()();
if (invalidations !== 1) {{
  throw new Error(`expected one resize invalidation, got ${{invalidations}}`);
}}
map = {{
  invalidateSize() {{
    throw new Error("guard should skip resize when no container is available");
  }},
  getContainer() {{
    return null;
  }},
}};
scheduleMapResize();
if (rafCallbacks.length !== 0) {{
  throw new Error("guarded resize should not queue animation frames");
}}
const listeners = {{}};
const element = {{
  dataset: {{}},
  setAttribute(name, value) {{
    this[name] = value;
  }},
  addEventListener(name, handler) {{
    listeners[name] = handler;
  }},
}};
let activations = 0;
const marker = {{
  on(name, handler) {{
    if (name === "add") this._onAdd = handler;
  }},
  getElement() {{
    return element;
  }},
}};
wireMarkerAccessibility(marker, "Zoom to 3 clustered locations", () => {{
  activations += 1;
}});
marker._onAdd();
if (element.role !== "button" || element.tabindex !== "0" || element["aria-label"] !== "Zoom to 3 clustered locations") {{
  throw new Error("marker accessibility attributes were not applied");
}}
let prevented = false;
listeners.keydown({{
  key: "Enter",
  preventDefault() {{
    prevented = true;
  }},
}});
listeners.keydown({{
  key: " ",
  preventDefault() {{
    prevented = true;
  }},
}});
listeners.keydown({{
  key: "Escape",
  preventDefault() {{
    throw new Error("non-activation keys should not be prevented");
  }},
}});
if (!prevented || activations !== 2) {{
  throw new Error(`expected two keyboard activations, got ${{activations}}`);
}}
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout


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
        assert page.rstrip().count("<th>Action</th>") == 1


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
    monkeypatch.setattr(flask_app, "LIBRENMS_URL", "")
    monkeypatch.setattr(flask_app, "LIBRENMS_API_TOKEN", "")
    assert flask_app._ensure_inventory_snapshot(force=True, wait=True)
    return integration_client


class TestAlertBoardWithPersistence:
    def _alerts_by_site(self, client):
        resp = client.get("/api/alerts")
        assert resp.status_code == 200
        return {site["id"]: site for site in resp.get_json()["alerts"]}

    def test_cached_devices_are_linked_to_their_location(self, persisted_integration_client):
        devices = flask_app._read_cached_devices()
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

        monkeypatch.setattr(flask_app, "LIBRENMS_URL", "")
        monkeypatch.setattr(flask_app, "LIBRENMS_API_TOKEN", "")

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
        js = integration_client.get("/static/js/alerts.js").get_data(as_text=True)
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
    """Runtime checks of the next-update countdown in alerts.js (#152)."""

    def test_countdown_formats_and_triggers_one_background_update(self):
        if shutil.which("node") is None:
            pytest.skip("node is required for the countdown runtime test")
        js = (REPO_ROOT / "static" / "js" / "alerts.js").read_text(encoding="utf-8")
        functions = "\n".join(
            _extract_js_function(js, name) for name in ("formatCountdown", "setNextUpdate", "renderNextUpdate")
        )
        script = f"""
const AUTO_UPDATE_MIN_GAP_MS = 30000;
let nextUpdateDueAt = null;
let lastAutoUpdateAt = 0;
let syncPollTimer = null;
let latestPayload = {{ sync_pending: false }};
const refreshBtn = {{ disabled: false }};
const nextUpdateEl = {{ textContent: "", hidden: true }};
const loads = [];
function loadAlertBoard(force, options) {{ loads.push([force, options]); }}
function check(condition, message) {{ if (!condition) throw new Error(message); }}
{functions}

check(formatCountdown(0) === "0:00", formatCountdown(0));
check(formatCountdown(59.2) === "1:00", formatCountdown(59.2));
check(formatCountdown(222) === "3:42", formatCountdown(222));
check(formatCountdown(3725) === "1:02:05", formatCountdown(3725));
check(formatCountdown(-5) === "0:00", formatCountdown(-5));

// Counting down.
setNextUpdate({{ next_update_in_seconds: 222 }}, 1000000);
check(!nextUpdateEl.hidden && nextUpdateEl.textContent === "Next update in 3:42", nextUpdateEl.textContent);
renderNextUpdate(1000000 + 22000);
check(nextUpdateEl.textContent === "Next update in 3:20", nextUpdateEl.textContent);
check(loads.length === 0, "no update before zero");

// At zero: exactly one background reload, then none until the gap has passed.
renderNextUpdate(1000000 + 222000);
check(nextUpdateEl.textContent === "Updating…", nextUpdateEl.textContent);
check(loads.length === 1 && loads[0][0] === false && loads[0][1].background === true, JSON.stringify(loads));
renderNextUpdate(1000000 + 223000);
renderNextUpdate(1000000 + 240000);
check(loads.length === 1, "reloaded again inside the gap");
renderNextUpdate(1000000 + 252001);
check(loads.length === 2, "should retry after the gap");

// Not while sync polling is already running.
syncPollTimer = 1;
renderNextUpdate(1000000 + 400000);
check(loads.length === 2, "must not reload while sync polling runs");
syncPollTimer = null;

// A running sync shows Updating…; an unknown schedule hides the countdown.
latestPayload = {{ sync_pending: true }};
setNextUpdate({{ next_update_in_seconds: null }}, 2000000);
check(!nextUpdateEl.hidden && nextUpdateEl.textContent === "Updating…", "sync pending");
latestPayload = {{ sync_pending: false }};
renderNextUpdate(2000000);
check(nextUpdateEl.hidden, "hidden when unknown");
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout

    def test_board_template_has_countdown_element(self, integration_client):
        html = integration_client.get("/alerts").data.decode()
        assert 'id="next-update"' in html
