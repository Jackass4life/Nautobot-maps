"""One top bar on every staff page (#419): a site search, how fresh the data
is, who is signed in, the theme and the pages."""

from datetime import UTC, datetime

from nautobot_maps import metrics, settings
from tests.js_modules import run_js
from tests.test_app import auth_config

NOW = datetime(2026, 10, 22, 14, 0, tzinfo=UTC).timestamp()
INTERVALS = {"INVENTORY_SYNC_INTERVAL_SECONDS": 300, "LIBRENMS_SYNC_INTERVAL_SECONDS": 60}


def row(minutes_ago, status="idle"):
    return {"status": status, "last_succeeded_at": datetime.fromtimestamp(NOW - minutes_ago * 60, UTC)}


def test_fresh_data_is_ok_and_says_when():
    status = metrics.app_status(
        {"nautobot_inventory": row(4), "librenms_inventory": row(1)}, librenms=True, intervals=INTERVALS, now=NOW
    )
    assert status["state"] == "ok"
    # Only as fresh as its oldest source.
    assert status["updated_at"] == "2026-10-22T13:56:00Z"
    assert [source["name"] for source in status["sources"]] == ["Nautobot", "LibreNMS"]


def test_old_data_is_stale_and_says_how_old():
    # LibreNMS every minute: stale after the 15-minute floor, not 3 minutes.
    status = metrics.app_status(
        {"nautobot_inventory": row(4), "librenms_inventory": row(14)}, librenms=True, intervals=INTERVALS, now=NOW
    )
    assert status["state"] == "ok"
    status = metrics.app_status(
        {"nautobot_inventory": row(4), "librenms_inventory": row(45)}, librenms=True, intervals=INTERVALS, now=NOW
    )
    assert status["state"] == "stale"
    assert status["sources"][1]["message"] == "LibreNMS data is 45 min old"


def test_a_failing_sync_is_down_and_wins():
    status = metrics.app_status(
        {"nautobot_inventory": row(90), "librenms_inventory": row(2, status="error")},
        librenms=True,
        intervals=INTERVALS,
        now=NOW,
    )
    assert status["state"] == "down"
    assert status["sources"][1]["message"] == "Can't reach LibreNMS"
    assert status["sources"][1]["last_success"] == "2026-10-22T13:58:00Z"


def test_without_librenms_only_nautobot_counts():
    status = metrics.app_status({"nautobot_inventory": row(4)}, librenms=False, intervals=INTERVALS, now=NOW)
    assert status["state"] == "ok" and [source["name"] for source in status["sources"]] == ["Nautobot"]
    never = metrics.app_status({}, librenms=False, intervals=INTERVALS, now=NOW)
    assert never["state"] == "stale" and never["updated_at"] is None
    assert never["sources"][0]["message"] == "No data from Nautobot yet"


def test_no_database_answer_is_down():
    status = metrics.app_status(None, librenms=True, intervals=INTERVALS, now=NOW)
    assert status["state"] == "down" and status["sources"][0]["message"] == "Can't reach the database"


def test_the_endpoint_never_calls_upstream(client, monkeypatch):
    called = []
    monkeypatch.setattr("nautobot_maps.nautobot.fetch_all_pages", lambda *a, **k: called.append(a))
    monkeypatch.setattr("nautobot_maps.librenms.get", lambda *a, **k: called.append(a))
    monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
    response = client.get("/api/app-status")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["state"] in {"ok", "stale"}
    assert called == []


def test_the_bar_says_who_is_signed_in(client):
    with auth_config(mode="header", operator_groups={"noc-operators"}):
        html = client.get(
            "/docs", headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "noc-operators"}
        ).get_data(as_text=True)
    assert '<span class="app-user-name">olga</span><span class="app-user-role"> · operator</span>' in html
    # Disabled: nobody to name.
    assert 'class="app-user"' not in client.get("/docs").get_data(as_text=True)


def test_a_name_is_escaped(client):
    with auth_config(mode="header", viewer_groups={"v"}):
        html = client.get("/docs", headers={"X-Forwarded-User": "<b>x</b>", "X-Forwarded-Groups": "v"}).get_data(
            as_text=True
        )
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


def test_the_search_is_on_every_page(client):
    # The server-rendered page; the Nuxt ones (the map, the board, /circuits, /maintenance,
    # /availability, /help, #463) have AppBar.vue, without the search on the map, which has its own (e2e).
    for path in ("/docs",):
        html = client.get(path).get_data(as_text=True)
        assert 'id="bar-search"' in html and 'id="app-status"' in html, path


def test_no_bar_on_the_wall_or_the_status_page(client):
    for path in ("/alerts?view=wall", "/status/Acme%20Corp"):
        html = client.get(path).get_data(as_text=True)
        assert 'class="app-bar"' not in html and "app-user" not in html and "bar-search" not in html, path


def test_the_status_line_names_the_worst_source():
    run_js(
        """
import { appStatusView } from "@js/lib/nav.js";
const now = new Date("2026-10-22T14:00:00Z");
const ok = appStatusView({ state: "ok", updated_at: "2026-10-22T13:56:00Z", sources: [
  { name: "Nautobot", state: "ok", message: "Nautobot is up to date", last_success: "2026-10-22T13:56:00Z" },
] }, now);
check(ok.state === "ok" && ok.text.startsWith("Updated "), ok.text);
check(ok.detail.includes("up to date") && ok.detail.includes("Nautobot") && ok.detail.includes("synced "), ok.detail);
const bad = appStatusView({ state: "down", updated_at: null, sources: [
  { name: "Nautobot", state: "stale", message: "Nautobot data is 45 min old", last_success: "2026-10-22T13:15:00Z" },
  { name: "LibreNMS", state: "down", message: "Can't reach <LibreNMS>", last_success: "2026-10-22T13:50:00Z" },
] }, now);
check(bad.state === "down" && bad.text === "Can't reach <LibreNMS>", bad.text);
check(bad.detail.includes("Can&#39;t reach &lt;LibreNMS&gt;") || bad.detail.includes("Can't reach &lt;LibreNMS&gt;"), "escaped");
check(bad.detail.includes("last good"), "when it was last good");
check(appStatusView(null).text === "Can't reach Nautobot Maps", "the app itself");
"""
    )


def test_the_search_rows_at_scale():
    run_js(
        """
import { barSearchHtml, isSearchShortcut } from "@js/lib/nav.js";
import { matchSites } from "@js/map/sitesearch.js";
const sites = Array.from({ length: 1200 }, (_, i) => ({ id: `s${i}`, name: `Site ${i}`, ancestor_path: "EMEA › DNK" }));
sites.push({ id: "x&1", name: "Bank <HQ>", ancestor_path: "" });
const html = barSearchHtml(matchSites(sites, "site"));
check((html.match(/class="bar-hit"/g) || []).length === 8, "eight rows");
check(html.includes("1,192 more: keep typing"), "and how many more");
check(html.includes('href="/?location_id=s0"') && html.includes('href="/alerts?q=Site%200"'), "map and board");
const bank = barSearchHtml(matchSites(sites, "bank"));
check(bank.includes("Bank &lt;HQ&gt;") && !bank.includes("<HQ>") && bank.includes("location_id=x%261"), "escaped");
check(barSearchHtml({ matches: [], more: 0 }).includes("No site matches"), "none");
const key = (target, extra = {}) => isSearchShortcut({ key: "/", target, ...extra });
check(key(element("page")), "/ on the page");
check(!key(element("input", { closest: () => ({}) })), "typing");
check(!key(element("page"), { ctrlKey: true }), "Ctrl+/");
"""
    )


class TestPageConfig:
    """GET /api/page-config (#463): what the Nuxt pages' top bar shows."""

    def test_the_menu_is_the_server_pages_menu(self, client):
        from nautobot_maps import navigation

        config = client.get("/api/page-config").get_json()
        assert config["pages"] == navigation.pages()
        assert [page["key"] for page in config["pages"]] == [key for key, *_ in navigation.PAGES]
        assert client.get("/api/page-config").headers["Cache-Control"] == "no-store"

    def test_no_one_signed_in_without_sign_in(self, client, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_MODE", "disabled")
        assert client.get("/api/page-config").get_json()["user"] is None

    def test_who_is_signed_in(self, client):
        with auth_config(mode="header", viewer_groups={"noc"}, operator_groups={"ops"}):
            config = client.get(
                "/api/page-config", headers={"X-Forwarded-User": "olga", "X-Forwarded-Groups": "ops"}
            ).get_json()
        assert config["user"] == {"name": "olga", "role": "operator"}

    def test_the_boards_case_links_and_levels(self, client, monkeypatch):
        from nautobot_maps import alerts

        monkeypatch.setattr(settings, "CASE_URL_TEMPLATE", "https://itsm.example/t/{case}")
        board = client.get("/api/page-config").get_json()["board"]
        assert board == {
            "case_url_template": "https://itsm.example/t/{case}",
            "levels": alerts.alert_level_definitions(),
        }

    def test_the_maps_tiles_and_first_view(self, client, monkeypatch):
        monkeypatch.setattr(settings, "MAP_TILE_URL", "https://tiles.example/{z}/{x}/{y}.png")
        monkeypatch.setattr(settings, "MAP_TILE_ATTRIBUTION", "Tiles")
        monkeypatch.setattr(settings, "MAP_DEFAULT_VIEW", None)
        assert client.get("/api/page-config").get_json()["map"] == {
            "tile_url": "https://tiles.example/{z}/{x}/{y}.png",
            "tile_attribution": "Tiles",
            "view": None,
        }
        # MAP_DEFAULT_VIEW (#362): the map opens there instead of framing the sites.
        monkeypatch.setattr(settings, "MAP_DEFAULT_VIEW", (55.7, 12.6, 6))
        view = client.get("/api/page-config").get_json()["map"]["view"]
        assert view == {"latitude": 55.7, "longitude": 12.6, "zoom": 6}

    def test_the_scope_is_kept_on_the_map_board_and_wall(self):
        from nautobot_maps import navigation

        pages = {page["key"]: page["href"] for page in navigation.pages("tenant=Acme%20Corp&q=DNK")}
        assert pages["map"] == "/?tenant=Acme%20Corp&q=DNK"
        assert pages["wall"] == "/alerts?view=wall&tenant=Acme%20Corp&q=DNK"
        assert pages["circuits"] == "/circuits"
