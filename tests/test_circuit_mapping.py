"""Circuit mapping page (#385): circuits without a termination and their likely site."""

import pytest

from nautobot_maps import caching, inventory, nautobot, settings
from tests.js_modules import run_js
from tests.test_app import auth_config
from tests.test_circuits_by_address import CIRCUITS, LOCATIONS, _circuit, client  # noqa: F401


@pytest.fixture
def synced(pg_database, client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
    circuits = [*CIRCUITS, _circuit("c-formula", "=HYPERLINK(1)", "@Roersvej 33, 5000 Odense")]

    def fetch(endpoint, params=None, **kwargs):
        if endpoint == "dcim/locations/":
            return LOCATIONS
        if endpoint == "circuits/circuits/":
            return circuits
        return []

    monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
    inventory.sync_nautobot(force=True)
    caching.cache.clear()


def test_groups_worst_first_with_their_sites(client, synced):  # noqa: F811
    data = client.get("/api/circuit-mapping").get_json()
    rows = [(c["group"], c["cid"], [s["name"] for s in c["sites"]]) for c in data["circuits"]]
    assert rows == [
        # Odense and Odense colo; Odense's building B is part of Odense.
        ("several", "tdc-0002", ["Odense", "Odense colo"]),
        ("none", "=HYPERLINK(1)", []),  # more than the address
        ("none", "GTT-0003", []),  # 33A: no site there
        ("none", "TDC-0005", []),  # more than the address
        ("none", "TDC-0007", []),  # a town alone is no address
        ("one", "TDC-0004", ["Aarhus"]),
    ]
    # Terminated circuits (TDC-0001 at Odense, TDC-0006 at its building) aren't on it.
    assert data["summary"] == {"total": 6, "several": 1, "none": 4, "one": 1}
    aarhus = data["circuits"][-1]
    assert aarhus["sites"] == [{"id": "loc-aar", "name": "Aarhus", "on_map": True}]
    assert (aarhus["provider"], aarhus["commit_rate"], aarhus["status"]) == ("Telia", "1 Gbps", "Active")


def test_csv_one_row_per_circuit_formula_safe(client, synced):  # noqa: F811
    resp = client.get("/api/circuit-mapping.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert 'filename="circuit-mapping.csv"' in resp.headers["Content-Disposition"]
    lines = resp.get_data(as_text=True).splitlines()
    assert lines[0] == "\ufeff" + (
        "group,cid,provider,type,status,tenant,commit_rate,description,suggested_sites,suggested_site_ids,circuit_id"
    )
    assert len(lines) == 7
    assert lines[1] == (
        'Several sites,tdc-0002,Telia,MPLS,Active,Acme Corp,1 Gbps,"Roersvej 33, 5000 Odense",'
        "Odense; Odense colo,loc-ode; loc-ode2,c-b"
    )
    assert lines[2].startswith("No site,'=HYPERLINK(1),")  # a spreadsheet won't run it
    assert ',"\'@Roersvej 33, 5000 Odense",' in lines[2]


def test_left_out_and_sorted(client, monkeypatch, pg_database):  # noqa: F811
    """Decommissioned circuits aren't work; an unnamed one sorts as "Unnamed circuit"."""
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
    circuits = [
        _circuit("c-old", "OLD-1", "", status={"label": "Decommissioned"}),
        _circuit("c-x", "", ""),
        _circuit("c-a", "AAA-1", ""),
        _circuit("c-z", "ZZZ-1", ""),
    ]
    monkeypatch.setattr(
        nautobot, "fetch_all_pages", lambda endpoint, *a, **k: circuits if endpoint == "circuits/circuits/" else []
    )
    inventory.sync_nautobot(force=True)
    data = client.get("/api/circuit-mapping").get_json()
    assert [c["cid"] for c in data["circuits"]] == ["AAA-1", "Unnamed circuit", "ZZZ-1"]
    assert data["summary"]["total"] == 3


def test_a_site_off_the_map_links_through_its_building(client, monkeypatch, pg_database):  # noqa: F811
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
    address = "Roersvej 33, 5000 Odense C"
    locations = [
        {**LOCATIONS[0], "latitude": None, "longitude": None},  # Odense: no coordinates
        LOCATIONS[1],  # its building B, at the address, on the map
        {**LOCATIONS[2], "id": "loc-x", "physical_address": "Vestergade 1, 8000 Aarhus C"},
        {**LOCATIONS[3], "physical_address": "Vestergade 1, 8000 Aarhus C", "latitude": None, "longitude": None},
    ]
    circuits = [_circuit("c1", "C-1", address), _circuit("c2", "C-2", "Vestergade 1, 8000 Aarhus")]

    def fetch(endpoint, params=None, **kwargs):
        return locations if endpoint == "dcim/locations/" else circuits if endpoint == "circuits/circuits/" else []

    monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
    inventory.sync_nautobot(force=True)
    by_cid = {c["cid"]: c["sites"] for c in client.get("/api/circuit-mapping").get_json()["circuits"]}
    assert by_cid["C-1"] == [{"id": "loc-ode-b", "name": "Odense", "on_map": True}]
    assert by_cid["C-2"] == [
        {"id": "loc-aar", "name": "Aarhus", "on_map": False},
        {"id": "loc-x", "name": "Odense colo", "on_map": True},
    ]


def test_page_and_nav(client, synced):  # noqa: F811
    page = client.get("/circuits")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'href="/api/circuit-mapping.csv"' in html
    assert 'data-nautobot-url="https://nautobot.example.com"' in html
    for path in ("/", "/alerts", "/availability", "/maintenance", "/docs"):
        assert 'href="/circuits"' in client.get(path).get_data(as_text=True), path


def test_without_a_database(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(inventory.db, "get_conn", lambda *args, **kwargs: None)
    assert client.get("/api/circuit-mapping").status_code == 503
    assert client.get("/api/circuit-mapping.csv").status_code == 503


def test_viewers_only_when_required(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
    with auth_config(mode="header", viewer_groups={"noc"}):
        for path in ("/circuits", "/api/circuit-mapping", "/api/circuit-mapping.csv"):
            assert client.get(path).status_code == 401, path


def test_list_renders_escapes_and_searches():
    run_js(
        """
        import { groupsHtml, matches, prepare, summaryText } from "@js/circuits/list.js";
        import { searchWords } from "@js/lib/format.js";
        const circuits = prepare([
          { id: "c1", cid: "TDC<1>", group: "one", provider: "Telia", status: "Active",
            description: "Roersvej 33, 5000 Odense", sites: [{ id: "loc 1", name: "Odense <b>", on_map: true }] },
          { id: "c2", cid: "GTT-2", group: "several",
            sites: [{ id: "a", name: "A", on_map: true }, { id: "b", name: "B", on_map: false }] },
          ...Array.from({ length: 150 }, (_, i) => ({ id: `n${i}`, cid: `N-${i}`, group: "none", sites: [] })),
        ]);
        const html = groupsHtml(circuits, { nautobotUrl: "https://nb.example/", open: new Set(["one"]) });
        check(html.indexOf('data-group="several"') < html.indexOf('data-group="none"'), "several first");
        check(html.indexOf('data-group="none"') < html.indexOf('data-group="one"'), "one last");
        check(html.includes('<details class="table-shell maint-group" data-group="one" open>'), "one open");
        check(!html.includes('data-group="none" open'), "others folded");
        check(html.includes("TDC&lt;1&gt;") && html.includes("Odense &lt;b&gt;"), "escaped");
        check(html.includes('href="https://nb.example/circuits/circuits/c1/"'), "circuit in Nautobot");
        check(html.includes('href="/?location_id=loc%201"'), "site on the map");
        check(html.includes('href="https://nb.example/dcim/locations/b/"'), "site off the map: in Nautobot");
        check(!groupsHtml(circuits).includes("/dcim/locations/"), "no Nautobot URL: the name only");
        check(html.includes("50 more") && html.includes("Show 50 more"), "first 100 of 150");
        check(!html.includes("N-100<"), "101st not shown");
        const more = groupsHtml(circuits, { limits: { none: 200 } });
        check(more.includes("N-149") && !more.includes("Show 50 more"), "all after Show more");

        check(matches(circuits[0], searchWords("odense telia")), "searches site and provider");
        check(!matches(circuits[0], searchWords("odense gtt")), "every word");

        const summary = { total: 152, several: 1, none: 150, one: 1 };
        check(summaryText(summary) === "152 circuits without a termination: 1 several sites · 150 no site · 1 one site",
          summaryText(summary));
        check(summaryText(summary, 3).endsWith(" · 3 match the search"), summaryText(summary, 3));
        check(summaryText({ total: 0 }) === "Every circuit has a termination at a location.", "empty");
        """
    )
