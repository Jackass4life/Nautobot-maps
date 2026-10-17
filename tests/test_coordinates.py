"""Sites with missing or wrong coordinates (#368)."""

import csv
import io
import json
import random

import pytest
from werkzeug.datastructures import MultiDict

import app as flask_app
from nautobot_maps import alerts, caching, coordinates, scope, settings
from tests.js_modules import run_js


def site(site_id, lat, lon, **extra):
    return {"id": site_id, "name": f"Site {site_id}", "latitude": lat, "longitude": lon, "device_count": 4, **extra}


def europe(count=1200, seed=368):
    """A network across Europe, as the scale demo."""
    rnd = random.Random(seed)
    return [site(f"s{n}", rnd.uniform(38, 62), rnd.uniform(-9, 25)) for n in range(count)]


class TestCheck:
    def test_a_normal_network_has_nothing_to_report(self):
        assert coordinates.check(europe()) == []

    def test_each_problem_worst_first(self):
        rows = europe() + [
            site("nowhere", None, None, ancestor_path="EMEA › DNK", tenant="Acme Corp"),
            site("null-island", 0, 0),
            site("swapped", 12.57, 55.68),  # Copenhagen, latitude and longitude the wrong way round
            site("new-york", 40.71, -74.0),  # far, and swapping doesn't help
            site("text", "", "x"),  # unreadable: as good as none
            site("impossible", 512, 10),
        ]
        found = coordinates.check(rows)
        assert [(item["site_id"], item["problem"]) for item in found] == [
            ("impossible", "invalid"),
            ("null-island", "zero"),
            ("swapped", "swapped"),
            ("new-york", "far"),
            ("nowhere", "missing"),
            ("text", "missing"),
        ]
        far = found[3]
        assert far["km_away"] > coordinates.FAR_MIN_KM and far["reason"].startswith("Far from the other sites")
        nowhere = found[4]
        assert nowhere["ancestor_path"] == "EMEA › DNK" and nowhere["tenants"] == ["Acme Corp"]
        assert nowhere["latitude"] is None
        assert coordinates.counts(found) == {"invalid": 1, "zero": 1, "swapped": 1, "far": 1, "missing": 2}

    def test_regions_and_buildings_without_devices_are_not_sites(self):
        rows = [
            *europe(50),
            site("region", None, None, device_count=0),
            site("building-at-zero", 0, 0, device_count=0),
            site("in-maintenance", None, None, device_count=0, maintenance_device_count=30),
            site("all-down", 0, 0, device_count=0, down_device_count=2),
        ]
        assert [(item["site_id"], item["problem"]) for item in coordinates.check(rows)] == [
            ("all-down", "zero"),
            ("in-maintenance", "missing"),
        ]

    def test_far_scales_with_the_network(self):
        """A world-wide network: New York is one of the sites, not far from them."""
        rnd = random.Random(1)
        world = [site(f"w{n}", rnd.uniform(-40, 60), rnd.uniform(-120, 140)) for n in range(300)]
        assert coordinates.check([*world, site("ny", 40.71, -74.0)]) == []

    def test_too_few_sites_to_be_far_from(self):
        assert coordinates.check([site("a", 55.7, 12.6), site("b", -33.9, 151.2)]) == []

    def test_zero_sites_do_not_move_the_centre(self):
        rows = europe(50) + [site(f"z{n}", 0, 0) for n in range(200)]
        assert {item["problem"] for item in coordinates.check(rows)} == {"zero"}

    def test_a_site_without_a_name(self):
        assert coordinates.check([site("x", None, None, name=None)])[0]["name"] == ""

    def test_only_the_pages_sites(self):
        rows = europe(50) + [
            site("acme", None, None, tenant="Acme Corp"),
            site("nordic", None, None, tenant="Nordic Net"),
        ]
        page = scope.read_scope(MultiDict([("tenant", "acme corp")]))
        assert [item["site_id"] for item in coordinates.check(rows, page)] == ["acme"]


# The same scopes through the server's matching and the browser's (#368, #369).
SCOPE_SITES = [
    {"id": "a", "name": "Aarhus", "ancestor_path": "EMEA › DNK", "tenant": "Acme Corp", "country": "Denmark"},
    {
        "id": "o",
        "name": "Oslo",
        "parent": "NOR",
        "tenants": ["Nordic Net", "Acme Corp"],
        "physical_address": "Storgata 1",
    },
    {"id": "l", "name": "Lyon", "facility": "Lyon DC2", "tenant": "EuroIX"},
    {"id": "n", "name": None},
]
SCOPE_CASES = [
    "",
    "?tenant=acme%20corp",
    "?tenant=EUROIX&tenant=Nordic%20Net",
    "?q=EMEA",
    "?q=dnk&q=aarhus",
    "?q=storgata",
    "?q=dc2",
    "?tenant=Acme%20Corp&q=oslo",
    "?q=nowhere",
]


class TestScopeParity:
    def test_server_and_browser_match_the_same_sites(self):
        from urllib.parse import parse_qsl

        server = {
            case: [
                row["id"]
                for row in SCOPE_SITES
                if scope.in_scope(row, scope.read_scope(MultiDict(parse_qsl(case[1:]))))
            ]
            for case in SCOPE_CASES
        }
        assert server["?tenant=acme%20corp"] == ["a", "o"] and server["?q=dnk&q=aarhus"] == ["a"]
        run_js(
            f"""
import {{ inScope, readScope }} from "@js/lib/scope.js";
const sites = {json.dumps(SCOPE_SITES)};
const expected = {json.dumps(server)};
for (const [search, ids] of Object.entries(expected)) {{
  const got = sites.filter((site) => inScope(site, readScope(search))).map((site) => site.id);
  check(JSON.stringify(got) === JSON.stringify(ids), `${{search}}: browser ${{got}} != server ${{ids}}`);
}}
"""
        )


class TestApi:
    @pytest.fixture
    def rows(self):
        return europe(100) + [site("nowhere", None, None, tenant="Acme Corp"), site("swapped", 12.57, 55.68)]

    @pytest.fixture
    def client(self, monkeypatch, rows):
        monkeypatch.setattr(alerts, "board", lambda trigger="request", **kwargs: {"alerts": rows})
        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as test_client:
            yield test_client

    def test_json(self, client):
        data = client.get("/api/locations/coordinate-check").get_json()
        assert data["counts"] == {"invalid": 0, "zero": 0, "swapped": 1, "far": 0, "missing": 1}
        assert [item["site_id"] for item in data["sites"]] == ["swapped", "nowhere"]
        scoped = client.get("/api/locations/coordinate-check?tenant=acme%20corp").get_json()
        assert [item["site_id"] for item in scoped["sites"]] == ["nowhere"]

    def test_csv(self, client):
        resp = client.get("/api/locations/coordinate-check.csv")
        assert resp.mimetype == "text/csv" and "coordinate-check.csv" in resp.headers["Content-Disposition"]
        rows = list(csv.DictReader(io.StringIO(resp.get_data(as_text=True).lstrip("﻿"))))
        assert [row["site_id"] for row in rows] == ["swapped", "nowhere"]
        assert rows[1]["latitude"] == "" and rows[0]["problem"] == "swapped" and rows[1]["site"] == "Site nowhere"
        scoped = client.get("/api/locations/coordinate-check.csv?tenant=Acme%20Corp").get_data(as_text=True)
        assert "nowhere" in scoped and "swapped" not in scoped

    def test_metrics_read_the_stored_board_only(self, client, monkeypatch, rows):
        """A scrape never builds the board (#368 review): no stored board, no gauge."""
        monkeypatch.setattr(settings, "METRICS_ENABLED", True)
        monkeypatch.setattr(alerts, "board", lambda *args, **kwargs: pytest.fail("a scrape built the board"))
        monkeypatch.setattr(caching, "get", lambda key: None)
        assert "coordinate_problems" not in client.get("/metrics").get_data(as_text=True)
        monkeypatch.setattr(caching, "get", lambda key: {"alerts": rows} if key == alerts.BOARD_CACHE_KEY else None)
        body = client.get("/metrics").get_data(as_text=True)
        assert 'nautobot_maps_sites_coordinate_problems{problem="missing"} 1' in body
        assert 'nautobot_maps_sites_coordinate_problems{problem="zero"} 0' in body


class TestMapInTheBrowser:
    def test_line_and_list(self):
        run_js(
            """
import { COORDINATE_LIST_LIMIT, coordinateListHtml, coordinateSummary, renderCoordinateCheck } from "@js/map/coordinates.js";
import { config, els } from "@js/map/state.js";

const missing = (n) => ({ site_id: `m${n}`, name: `Site <${n}>`, ancestor_path: "EMEA › DNK", problem: "missing",
  reason: "No coordinates: not on the map", latitude: null, longitude: null });
const swapped = { site_id: "s1", name: "Copenhagen", ancestor_path: "", problem: "swapped",
  reason: "Far from the other sites, but not with latitude and longitude swapped", latitude: 12.57, longitude: 55.68 };
const invalid = { site_id: "i1", name: "Typo", problem: "invalid", reason: "Impossible", latitude: 512, longitude: 10 };

check(coordinateSummary([]) === "", "all well: nothing");
check(coordinateSummary([missing(1)]) === "1 site isn't on the map", coordinateSummary([missing(1)]));
check(coordinateSummary([missing(1), missing(2), swapped]) === "2 sites aren't on the map · 1 may be misplaced",
  coordinateSummary([missing(1), missing(2), swapped]));

config.nautobotUrl = "https://nautobot.example.com";
const many = [invalid, swapped, ...Array.from({ length: 80 }, (_, n) => missing(n))];
const html = coordinateListHtml(many);
check((html.match(/<li>/g) || []).length === COORDINATE_LIST_LIMIT, "the first 50");
check(html.includes("+32 more in the CSV") && html.includes('href="/api/locations/coordinate-check.csv"'), "the rest in the CSV");
check(html.includes("Site &lt;1&gt;") && !html.includes("Site <1>"), "escaped");
check(html.includes('href="https://nautobot.example.com/dcim/locations/s1/"'), "fix it in Nautobot");
check(html.includes('data-site-id="s1"') && !html.includes('data-site-id="m1"') && !html.includes('data-site-id="i1"'),
  "Show only for a site on the map");

Object.assign(els, { coordinateNote: element("note", { hidden: true }), coordinateText: element("text"), coordinateList: element("list") });
renderCoordinateCheck([]);
check(els.coordinateNote.hidden, "hidden when all is well");
renderCoordinateCheck(many);
check(!els.coordinateNote.hidden && els.coordinateText.textContent === "80 sites aren't on the map · 2 may be misplaced",
  els.coordinateText.textContent);
"""
        )

    def test_a_scoped_map_asks_for_its_sites(self):
        run_js(
            """
import { loadCoordinateCheck } from "@js/map/coordinates.js";
import { readScope } from "@js/lib/scope.js";
import { config, els } from "@js/map/state.js";

Object.assign(els, { coordinateNote: element("note", { hidden: true }), coordinateText: element("text"), coordinateList: element("list") });
config.scope = readScope("?tenant=Acme%20Corp&q=DNK&q=aarhus");
const asked = [];
globalThis.fetch = async (url) => {
  asked.push(url);
  return new Response(JSON.stringify({ sites: [{ site_id: "a", name: "Aarhus", problem: "missing", reason: "x", latitude: null }] }));
};
await loadCoordinateCheck();
check(asked[0] === "/api/locations/coordinate-check?tenant=Acme+Corp&q=DNK&q=aarhus", asked[0]);
check(els.coordinateList.innerHTML.includes('href="/api/locations/coordinate-check.csv?tenant=Acme+Corp&amp;q=DNK&amp;q=aarhus"'),
  "the CSV for the same sites");
"""
        )
