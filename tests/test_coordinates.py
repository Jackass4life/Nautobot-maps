"""Sites with missing or wrong coordinates (#368)."""

import csv
import io
import random

import pytest

import app as flask_app
from nautobot_maps import alerts, coordinates, settings
from tests.js_modules import run_js


def site(site_id, lat, lon, **extra):
    return {"id": site_id, "name": f"Site {site_id}", "latitude": lat, "longitude": lon, **extra}


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
        ]
        found = coordinates.check(rows)
        assert [(item["site_id"], item["problem"]) for item in found] == [
            ("null-island", "zero"),
            ("swapped", "swapped"),
            ("new-york", "far"),
            ("nowhere", "missing"),
            ("text", "missing"),
        ]
        far = found[2]
        assert far["km_away"] > coordinates.FAR_MIN_KM and far["reason"].startswith("Far from the other sites")
        nowhere = found[3]
        assert nowhere["path"] == "EMEA › DNK" and nowhere["tenants"] == ["Acme Corp"] and nowhere["latitude"] is None
        assert coordinates.counts(found) == {"zero": 1, "swapped": 1, "far": 1, "missing": 2}

    def test_far_scales_with_the_network(self):
        """A world-wide network: New York is one of the sites, not far from them."""
        rnd = random.Random(1)
        world = [site(f"w{n}", rnd.uniform(-40, 60), rnd.uniform(-120, 140)) for n in range(300)]
        assert [item["problem"] for item in coordinates.check([*world, site("ny", 40.71, -74.0)])] == []

    def test_too_few_sites_to_be_far_from(self):
        assert coordinates.check([site("a", 55.7, 12.6), site("b", -33.9, 151.2)]) == []

    def test_zero_sites_do_not_move_the_centre(self):
        rows = europe(50) + [site(f"z{n}", 0, 0) for n in range(200)]
        assert {item["problem"] for item in coordinates.check(rows)} == {"zero"}


class TestApi:
    @pytest.fixture
    def client(self, monkeypatch):
        rows = europe(100) + [site("nowhere", None, None), site("swapped", 12.57, 55.68)]
        monkeypatch.setattr(alerts, "board", lambda trigger="request", **kwargs: {"alerts": rows})
        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as test_client:
            yield test_client

    def test_json(self, client):
        data = client.get("/api/locations/coordinate-check").get_json()
        assert data["counts"] == {"zero": 0, "swapped": 1, "far": 0, "missing": 1}
        assert [item["site_id"] for item in data["sites"]] == ["swapped", "nowhere"]

    def test_csv(self, client):
        resp = client.get("/api/locations/coordinate-check.csv")
        assert resp.mimetype == "text/csv" and "coordinate-check.csv" in resp.headers["Content-Disposition"]
        rows = list(csv.DictReader(io.StringIO(resp.get_data(as_text=True).lstrip("﻿"))))
        assert [row["site_id"] for row in rows] == ["swapped", "nowhere"]
        assert rows[1]["latitude"] == "" and rows[0]["problem"] == "swapped"

    def test_metrics(self, client, monkeypatch):
        monkeypatch.setattr(settings, "METRICS_ENABLED", True)
        body = client.get("/metrics").get_data(as_text=True)
        assert 'nautobot_maps_sites_coordinate_problems{problem="missing"} 1' in body
        assert 'nautobot_maps_sites_coordinate_problems{problem="zero"} 0' in body


class TestMapInTheBrowser:
    def test_line_and_list(self):
        run_js(
            """
import { COORDINATE_LIST_LIMIT, coordinateListHtml, coordinateSummary, renderCoordinateCheck } from "@js/map/coordinates.js";
import { config, els } from "@js/map/state.js";

const missing = (n) => ({ site_id: `m${n}`, site: `Site <${n}>`, path: "EMEA › DNK", problem: "missing",
  reason: "No coordinates: not on the map", latitude: null, longitude: null });
const swapped = { site_id: "s1", site: "Copenhagen", path: "", problem: "swapped",
  reason: "Far from the other sites, but not with latitude and longitude swapped", latitude: 12.57, longitude: 55.68 };

check(coordinateSummary([]) === "", "all well: nothing");
check(coordinateSummary([missing(1)]) === "1 site isn't on the map", coordinateSummary([missing(1)]));
check(coordinateSummary([missing(1), missing(2), swapped]) === "2 sites aren't on the map · 1 may be misplaced",
  coordinateSummary([missing(1), missing(2), swapped]));

config.nautobotUrl = "https://nautobot.example.com";
const many = [swapped, ...Array.from({ length: 80 }, (_, n) => missing(n))];
const html = coordinateListHtml(many);
check((html.match(/<li>/g) || []).length === COORDINATE_LIST_LIMIT, "the first 50");
check(html.includes("+31 more in the CSV") && html.includes('href="/api/locations/coordinate-check.csv"'), "the rest in the CSV");
check(html.includes("Site &lt;1&gt;") && !html.includes("Site <1>"), "escaped");
check(html.includes('href="https://nautobot.example.com/dcim/locations/s1/"'), "fix it in Nautobot");
check(html.includes('data-site-id="s1"') && !html.includes('data-site-id="m1"'), "Show only for a site with coordinates");

Object.assign(els, { coordinateNote: element("note", { hidden: true }), coordinateText: element("text"), coordinateList: element("list") });
renderCoordinateCheck([]);
check(els.coordinateNote.hidden, "hidden when all is well");
renderCoordinateCheck(many);
check(!els.coordinateNote.hidden && els.coordinateText.textContent === "80 sites aren't on the map · 1 may be misplaced",
  els.coordinateText.textContent);
"""
        )

    def test_a_scoped_map_lists_its_sites_only(self):
        run_js(
            """
import { loadCoordinateCheck } from "@js/map/coordinates.js";
import { readScope } from "@js/lib/scope.js";
import { config, els } from "@js/map/state.js";

Object.assign(els, { coordinateNote: element("note", { hidden: true }), coordinateText: element("text"), coordinateList: element("list") });
config.scope = readScope("?tenant=Acme%20Corp");
globalThis.fetch = async () => new Response(JSON.stringify({ sites: [
  { site_id: "a", site: "Aarhus", problem: "missing", reason: "x", tenants: ["Acme Corp"], latitude: null },
  { site_id: "o", site: "Oslo", problem: "missing", reason: "x", tenants: ["Nordic Net"], latitude: null },
] }));
await loadCoordinateCheck();
check(els.coordinateText.textContent === "1 site isn't on the map", els.coordinateText.textContent);
"""
        )
