"""Access point status from the wireless controllers (#508): Catalyst Center,
any number of instances, counted and never alarmed."""

import pytest
import requests

from nautobot_maps import __main__ as cli
from nautobot_maps import (
    alerts,
    arubacentral,
    catalyst,
    contract,
    db,
    inventory,
    metrics,
    scheduler,
    settings,
    status,
    wireless,
)
from tests import test_maintenance
from tests.test_maintenance import build

clock = test_maintenance.clock
site = test_maintenance.site

DK = {
    "kind": "catalyst_center",
    "name": "dk",
    "url": "https://dnac-dk.example.com",
    "username": "maps-ro",
    "password": "s3cret-pw",
    "verify_ssl": True,
}
SE = {**DK, "name": "se", "url": "https://dnac-se.example.com"}


def ap(name: str, up: bool, serial: str = "") -> dict:
    return {"serial": serial, "mac": "", "name": name, "up": up}


class TestSettings:
    def write(self, tmp_path, text: str) -> str:
        path = tmp_path / "wireless.yml"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_any_number_of_instances(self, tmp_path):
        path = self.write(
            tmp_path,
            """
catalyst_center:
  - name: DK
    url: https://dnac-dk.example.com/
    username: maps-ro
    password: one
  - name: se
    url: https://dnac-se.example.com
    username: maps-ro
    password: two
    verify_ssl: false
""",
        )
        dk, se = settings._wireless_controllers(path)
        assert (dk["name"], dk["url"], dk["verify_ssl"]) == ("dk", "https://dnac-dk.example.com", True)
        assert (se["kind"], se["password"], se["verify_ssl"]) == ("catalyst_center", "two", False)
        assert settings._wireless_controllers("") == ()

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("aruba_classic: []", "unknown aruba_classic"),
            ("catalyst_center: {name: dk}", "must be a list"),
            ("catalyst_center: [{name: 'D K', url: 'https://x', username: u, password: p}]", "name must be"),
            ("catalyst_center: [{name: dk, url: 'https://x', username: u}]", "'dk': missing password"),
            ("catalyst_center: [{name: dk, url: 'ftp://x', username: u, password: p}]", "absolute http(s)"),
            ("catalyst_center: [{name: dk, url: 'https://x', username: u, password: p, tokn: 1}]", "unknown tokn"),
            (
                "catalyst_center: [{name: dk, url: 'https://x', username: u, password: p},"
                " {name: dk, url: 'https://y', username: u, password: p}]",
                "listed twice",
            ),
            ("catalyst_center: [", "can't be read: ParserError"),
        ],
    )
    def test_a_mistake_stops_startup_and_names_it(self, tmp_path, text, message):
        with pytest.raises(RuntimeError, match=None) as caught:
            settings._wireless_controllers(self.write(tmp_path, text))
        assert message in str(caught.value)

    def test_a_password_is_never_in_the_message(self, tmp_path):
        path = self.write(tmp_path, "catalyst_center: [{name: dk, url: 'nope', username: u, password: hunter2}]")
        with pytest.raises(RuntimeError) as caught:
            settings._wireless_controllers(path)
        assert "hunter2" not in str(caught.value)

    def test_aruba_central_by_region_or_base_url(self, tmp_path):
        path = self.write(
            tmp_path,
            """
aruba_central:
  - {name: eu, region: DE1, client_id: c1, client_secret: s1}
  - {name: cn, region: cn1, client_id: c2, client_secret: s2}
  - {name: lab, base_url: "https://central.lab.example/", token_url: "https://sso.lab.example/token",
     client_id: c3, client_secret: s3, verify_ssl: false}
catalyst_center:
  - {name: eu, url: "https://dnac.example.com", username: u, password: p}
""",
        )
        dnac, eu, cn, lab = settings._wireless_controllers(path)
        assert dnac["kind"] == "catalyst_center", "the same name in two kinds is two instances"
        assert (eu["url"], eu["token_url"]) == (
            "https://de1.api.central.arubanetworks.com",
            "https://sso.common.cloud.hpe.com/as/token.oauth2",
        )
        assert cn["url"] == "https://cn1.api.central.arubanetworks.com.cn"
        assert (lab["url"], lab["token_url"], lab["verify_ssl"]) == (
            "https://central.lab.example",
            "https://sso.lab.example/token",
            False,
        )
        assert (eu["client_id"], eu["client_secret"]) == ("c1", "s1")

    @pytest.mark.parametrize(
        ("entry", "message"),
        [
            ("{name: eu, client_id: c, client_secret: s}", "give region (e.g. de1) or base_url"),
            ("{name: eu, region: de1, base_url: 'https://x', client_id: c, client_secret: s}", "one of them"),
            ("{name: eu, region: europe, client_id: c, client_secret: s}", "region must be like"),
            ("{name: eu, region: de1, client_id: c}", "'eu': missing client_secret"),
            ("{name: eu, base_url: 'x', client_id: c, client_secret: s}", "base_url must be an absolute"),
            ("{name: eu, region: de1, token_url: 'x', client_id: c, client_secret: s}", "token_url must be an"),
        ],
    )
    def test_aruba_central_mistakes(self, tmp_path, entry, message):
        with pytest.raises(RuntimeError) as caught:
            settings._wireless_controllers(self.write(tmp_path, f"aruba_central: [{entry}]"))
        assert message in str(caught.value)
        assert "s}" not in str(caught.value)

    def test_a_missing_file_says_so(self, tmp_path):
        with pytest.raises(RuntimeError, match="can't be read: FileNotFoundError"):
            settings._wireless_controllers(str(tmp_path / "none.yml"))


class FakeSession:
    """Answers Catalyst Center's sign-in and device pages; records each request."""

    def __init__(self, total_aps: int = 3, expired_once: bool = False):
        self.calls = []
        self.total = total_aps
        self.expired_once = expired_once
        self.tokens = 0

    def request(self, method, url, timeout=None, verify=None, **kwargs):
        self.calls.append((method, url, kwargs))
        response = requests.Response()
        response.url = url
        if url.endswith("/auth/token"):
            self.tokens += 1
            response.status_code = 200
            response._content = b'{"Token": "tok-%d"}' % self.tokens
            return response
        if self.expired_once:
            self.expired_once = False
            response.status_code = 401
            return response
        offset, limit = kwargs["params"]["offset"], kwargs["params"]["limit"]
        page = [
            {"serialNumber": f"FGL{n:05d}", "hostname": f"ap{n}", "reachabilityStatus": "Reachable"}
            for n in range(offset - 1, min(self.total, offset - 1 + limit))
        ]
        response.status_code = 200
        response._content = __import__("json").dumps({"response": page}).encode()
        return response


@pytest.fixture
def fake_catalyst(monkeypatch):
    catalyst._tokens.clear()
    fake = FakeSession(total_aps=503)
    monkeypatch.setattr(catalyst.http, "session", lambda: fake)
    yield fake
    catalyst._tokens.clear()


class TestCatalystCenter:
    def test_pages_of_500_one_sign_in(self, fake_catalyst):
        devices = catalyst.fetch_devices(DK)
        assert len(devices) == 503
        pages = [kwargs["params"] for method, url, kwargs in fake_catalyst.calls if method == "GET"]
        assert pages == [
            {"family": "Unified AP", "offset": 1, "limit": 500},
            {"family": "Unified AP", "offset": 501, "limit": 500},
        ]
        assert fake_catalyst.tokens == 1
        sign_in = fake_catalyst.calls[0]
        assert sign_in[2]["auth"] == ("maps-ro", "s3cret-pw")
        assert fake_catalyst.calls[1][2]["headers"]["X-Auth-Token"] == "tok-1"
        # The token is kept per instance: the next read signs in again only for another one.
        catalyst.fetch_devices(DK)
        catalyst.fetch_devices(SE)
        assert fake_catalyst.tokens == 2

    def test_a_refused_token_signs_in_again_once(self, fake_catalyst):
        fake_catalyst.total, fake_catalyst.expired_once = 2, True
        catalyst._tokens[DK["url"]] = ("old", 10**12)
        assert len(catalyst.fetch_devices(DK)) == 2
        assert fake_catalyst.tokens == 1

    def test_what_is_kept_of_an_ap(self):
        assert catalyst.ap_record(
            {"serialNumber": " FGL1 ", "hostname": "ap1", "macAddress": "AA:BB", "reachabilityStatus": "Unreachable"}
        ) == {"serial": "FGL1", "mac": "aa:bb", "name": "ap1", "up": False}
        assert catalyst.ap_record({"reachabilityStatus": "Reachable"})["up"] is True
        assert catalyst.ap_record({})["up"] is False


EU = {
    "kind": "aruba_central",
    "name": "eu",
    "url": "https://de1.api.central.arubanetworks.com",
    "token_url": "https://sso.common.cloud.hpe.com/as/token.oauth2",
    "client_id": "maps-ro",
    "client_secret": "s3cret-client",
    "verify_ssl": True,
}


class FakeCentral:
    """Answers HPE's sign-in and New Central's AP pages; records each request."""

    def __init__(self, total_aps: int = 2500, expired_once: bool = False):
        self.calls = []
        self.total = total_aps
        self.expired_once = expired_once
        self.tokens = 0

    def request(self, method, url, timeout=None, verify=None, **kwargs):
        import json

        self.calls.append((method, url, kwargs))
        response = requests.Response()
        response.url = url
        response.status_code = 200
        if url.endswith("/token.oauth2"):
            self.tokens += 1
            response._content = json.dumps({"access_token": f"tok-{self.tokens}", "expires_in": 7200}).encode()
            return response
        if self.expired_once:
            self.expired_once = False
            response.status_code = 401
            return response
        page, limit = int(kwargs["params"].get("next") or 1), kwargs["params"]["limit"]
        items = [
            {"serialNumber": f"CNF{n:05d}", "deviceName": f"ap{n}", "status": "ONLINE"}
            for n in range((page - 1) * limit, min(self.total, page * limit))
        ]
        more = page * limit < self.total
        response._content = json.dumps({"items": items, "next": str(page + 1) if more else None}).encode()
        return response


@pytest.fixture
def fake_central(monkeypatch):
    arubacentral._tokens.clear()
    fake = FakeCentral()
    monkeypatch.setattr(arubacentral.http, "session", lambda: fake)
    yield fake
    arubacentral._tokens.clear()


class TestArubaCentral:
    def test_pages_by_cursor_one_sign_in(self, fake_central):
        items = arubacentral.fetch_aps(EU)
        assert len(items) == 2500
        pages = [kwargs["params"] for method, url, kwargs in fake_central.calls if method == "GET"]
        assert pages == [{"limit": 1000}, {"limit": 1000, "next": "2"}, {"limit": 1000, "next": "3"}]
        sign_in = fake_central.calls[0]
        assert sign_in[1] == EU["token_url"]
        assert sign_in[2]["data"] == {
            "grant_type": "client_credentials",
            "client_id": "maps-ro",
            "client_secret": "s3cret-client",
        }
        get = fake_central.calls[1]
        assert get[1] == "https://de1.api.central.arubanetworks.com/network-monitoring/v1/aps"
        assert get[2]["headers"]["Authorization"] == "Bearer tok-1"
        # Kept until shortly before it expires; per API client.
        arubacentral.fetch_aps(EU)
        assert fake_central.tokens == 1
        arubacentral.fetch_aps({**EU, "name": "us", "client_id": "other"})
        assert fake_central.tokens == 2

    def test_a_refused_token_asks_again_once(self, fake_central):
        fake_central.total, fake_central.expired_once = 3, True
        arubacentral._tokens[(EU["token_url"], EU["client_id"])] = ("old", 10**12)
        assert len(arubacentral.fetch_aps(EU)) == 3
        assert fake_central.tokens == 1

    def test_what_is_kept_of_an_ap(self):
        assert arubacentral.ap_record(
            {"serialNumber": "CNF1", "deviceName": "ap1", "macAddress": "20:4C:03:AA", "status": "OFFLINE"}
        ) == {"serial": "CNF1", "mac": "20:4c:03:aa", "name": "ap1", "up": False}
        assert arubacentral.ap_record({"status": "online"})["up"] is True
        assert arubacentral.ap_record({})["up"] is False

    def test_read_through_the_wireless_module(self, fake_central, monkeypatch):
        checked = []
        monkeypatch.setattr(contract, "check_and_record", lambda source, path, records: checked.append((source, path)))
        fake_central.total = 2
        aps = wireless.fetch_aps(EU)
        assert [ap["serial"] for ap in aps] == ["CNF00000", "CNF00001"]
        assert checked == [("aruba_central", "network-monitoring/v1/aps")]
        assert wireless.display_name(EU) == "Aruba Central eu"


@pytest.fixture
def controllers(pg_database, monkeypatch):
    """Two instances, dk and se; each test says what they list."""
    monkeypatch.setattr(settings, "WIRELESS_CONTROLLERS", (DK, SE))
    answers = {"dk": [], "se": []}

    def fetch(controller):
        answer = answers[controller["name"]]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(wireless, "fetch_aps", fetch)
    return answers


def read_index() -> dict:
    conn = db.get_conn()
    try:
        return wireless.read_index(conn)
    finally:
        conn.close()


def sync_states() -> dict:
    conn = db.get_conn()
    try:
        return {c["name"]: inventory.get_sync_state(wireless.source(c), conn=conn) for c in (DK, SE)}
    finally:
        conn.close()


class TestSync:
    def test_each_instance_stored_on_its_own(self, controllers):
        controllers["dk"] = [ap("dk-ap1", True, "FGL1"), ap("dk-ap2", False, "FGL2")]
        controllers["se"] = [ap("SE-AP1.corp.example", False)]
        assert wireless.sync_due() is True, "APs went down: rebuild"
        index = read_index()
        assert index["serial"] == {"FGL1": True, "FGL2": False}
        assert index["name"] == {"dk-ap1": True, "dk-ap2": False, "se-ap1": False}
        assert {name: state["status"] for name, state in sync_states().items()} == {"dk": "idle", "se": "idle"}
        # Not due again within the interval; nothing changed, so no rebuild.
        assert wireless.sync_due() is False

    def test_one_unreachable_instance_wipes_nobody_else(self, controllers, monkeypatch):
        controllers["dk"] = [ap("dk-ap1", False, "FGL1")]
        controllers["se"] = [ap("se-ap1", False, "FGL9")]
        wireless.sync_due()
        controllers["se"] = requests.ConnectionError("https://dnac-se.example.com: connection refused")
        monkeypatch.setattr(settings, "WIRELESS_SYNC_INTERVAL_SECONDS", 60)
        monkeypatch.setattr(inventory, "state_due", lambda state, interval: True)
        wireless.sync_due()
        states = sync_states()
        assert states["se"]["status"] == "error" and "connection refused" in states["se"]["error_message"]
        assert states["dk"]["status"] == "idle"
        # se's last answer stays until it is stale; dk's is fresh.
        assert read_index()["serial"] == {"FGL1": False, "FGL9": False}

    def test_an_empty_answer_keeps_the_last_one(self, controllers):
        controllers["dk"] = [ap("dk-ap1", False, "FGL1")]
        wireless.sync_due()
        controllers["dk"] = []
        wireless.sync_controller(DK)
        assert sync_states()["dk"]["status"] == "error"
        assert read_index()["serial"] == {"FGL1": False}

    def test_stale_and_removed_instances_are_ignored(self, controllers, monkeypatch):
        controllers["dk"] = [ap("dk-ap1", False, "FGL1")]
        controllers["se"] = [ap("se-ap1", False, "FGL9")]
        wireless.sync_due()
        conn = db.get_conn()
        try:
            conn.execute(
                "UPDATE wireless_ap_status SET seen_at = seen_at - make_interval(secs => %s) WHERE controller = %s",
                (wireless.stale_seconds() + 60, "catalyst_center:se"),
            )
        finally:
            conn.close()
        assert read_index()["serial"] == {"FGL1": False}, "se's answer is too old"
        monkeypatch.setattr(settings, "WIRELESS_CONTROLLERS", (SE,))
        assert read_index()["serial"] == {}, "dk is no longer configured"

    def test_the_same_ap_in_two_instances(self, controllers):
        controllers["dk"] = [ap("ap1", False, "FGL1")]
        controllers["se"] = [ap("ap1", True, "FGL1")]
        wireless.sync_due()
        conn = db.get_conn()
        try:
            # Read at the same moment: up wins, a stale copy never shows it down.
            conn.execute("UPDATE wireless_ap_status SET seen_at = '2026-10-09T12:00:00Z'")
            assert wireless.read_index(conn, now=_utc("2026-10-09T12:01:00Z"))["serial"] == {"FGL1": True}
            # The newest answer wins.
            conn.execute(
                "UPDATE wireless_ap_status SET seen_at = '2026-10-09T12:00:30Z' WHERE controller = 'catalyst_center:dk'"
            )
            assert wireless.read_index(conn, now=_utc("2026-10-09T12:01:00Z"))["serial"] == {"FGL1": False}
        finally:
            conn.close()

    def test_matching_by_serial_then_name(self):
        index = {"serial": {"FGL1": False, "FGL2": True}, "name": {"ap7": False, "ap8": True}}
        ap_role = {"role": "Access Point"}
        assert wireless.is_down(index, {"serial": "fgl1", "name": "ap8"}) is True
        assert wireless.is_down(index, {"serial": "FGL2", "name": "ap7", **ap_role}) is False, "the serial decides"
        assert wireless.is_down(index, {"serial": "FGL3", "name": "AP7.corp.example", **ap_role}) is True
        # By name only for an access point: a switch called ap7 isn't one.
        assert wireless.is_down(index, {"serial": "", "name": "ap7", "role": "Access Switch"}) is False
        assert wireless.is_down(index, {"serial": "", "name": "ap9", **ap_role}) is False
        assert wireless.is_down(None, {"name": "ap7", **ap_role}) is False

    def test_a_name_two_aps_carry_is_not_matched(self, controllers):
        # ap-01 at two sites, in one instance or two: neither is matched by name.
        controllers["dk"] = [ap("ap-01", False, "FGL1"), ap("ap-02", False), ap("ap-02", True, "FGL3")]
        controllers["se"] = [ap("ap-01", True, "FGL2"), ap("ap-04", False, "FGL1")]
        wireless.sync_due()
        index = read_index()
        assert "ap-01" not in index["name"] and "ap-02" not in index["name"]
        assert index["name"]["ap-04"] is False, "FGL1 in dk and se is one AP"
        assert index["serial"]["FGL1"] in (True, False)

    def test_newest_by_time_not_text(self, controllers):
        controllers["dk"] = [ap("ap1", False, "FGL1")]
        controllers["se"] = [ap("ap1", True, "FGL1")]
        wireless.sync_due()
        conn = db.get_conn()
        try:
            # As text "...T12:00:00Z" sorts after "...T12:00:00.500000Z": the older one would win.
            conn.execute(
                "UPDATE wireless_ap_status SET seen_at = '2026-10-09T12:00:00Z' WHERE controller = 'catalyst_center:dk'"
            )
            conn.execute(
                "UPDATE wireless_ap_status SET seen_at = '2026-10-09T12:00:00.5Z' WHERE controller = 'catalyst_center:se'"
            )
            assert wireless.read_index(conn, now=_utc("2026-10-09T12:01:00Z"))["serial"] == {"FGL1": True}
        finally:
            conn.close()

    def test_a_removed_instance_is_forgotten(self, controllers, monkeypatch):
        controllers["dk"] = [ap("dk-ap1", False, "FGL1")]
        controllers["se"] = [ap("se-ap1", False, "FGL9")]
        wireless.sync_due()
        monkeypatch.setattr(settings, "WIRELESS_CONTROLLERS", (DK,))
        wireless.sync_due()
        conn = db.get_conn()
        try:
            assert [r["controller"] for r in conn.execute("SELECT DISTINCT controller FROM wireless_ap_status")] == [
                "catalyst_center:dk"
            ]
            assert inventory.get_sync_state("wireless:catalyst_center:se", conn=conn) == {}
            assert 'source="wireless:catalyst_center:se"' not in metrics.collect(conn)
        finally:
            conn.close()

    def test_a_sync_failure_never_stops_the_tick(self, pg_database, monkeypatch):
        monkeypatch.setattr(wireless, "sync_due", lambda: 1 / 0)
        assert scheduler.sync_wireless() is False

    def test_a_change_rebuilds_the_board_as_any_change(self, pg_database, monkeypatch):
        rebuilds = []
        monkeypatch.setattr(scheduler, "board_changed", lambda: rebuilds.append(1))
        monkeypatch.setattr(wireless, "sync_due", lambda: False)
        assert scheduler.sync_wireless() is False and rebuilds == []
        monkeypatch.setattr(wireless, "sync_due", lambda: True)
        assert scheduler.sync_wireless() is True and rebuilds == [1]


def _utc(text: str):
    from nautobot_maps import timeutil

    return timeutil.parse_iso_datetime(text)


class TestTheBoard:
    """Counted as an access point down, never in the site's level (#508)."""

    def test_counted_not_alarmed(self, site, monkeypatch):
        test_maintenance.set_devices(core="Active", acc1="Active", acc2="Active")
        monkeypatch.setattr(settings, "ACCESS_POINT_ROLES", {"access switch"})
        monkeypatch.setattr(settings, "WIRELESS_CONTROLLERS", (DK,))
        before = build()
        assert before["alert_level"] == "ok" and before["down_access_point_count"] == 0
        monkeypatch.setattr(wireless, "read_index", lambda conn: {"serial": {}, "name": {"acc2": False, "acc1": True}})
        row = build()
        assert row["alert_level"] == "ok", "never raises the level"
        assert row["down_device_count"] == 0 and row["down_devices"] == []
        assert (row["access_point_count"], row["down_access_point_count"]) == (2, 1)
        assert row["summary"]["non_ok"] == 0

    def test_down_in_nautobot_counts_once(self, site, monkeypatch):
        # acc1 is Offline in Nautobot and down per the controller: one AP down.
        monkeypatch.setattr(settings, "ACCESS_POINT_ROLES", {"access switch"})
        monkeypatch.setattr(settings, "WIRELESS_CONTROLLERS", (DK,))
        monkeypatch.setattr(wireless, "read_index", lambda conn: {"serial": {}, "name": {"acc1": False}})
        assert build()["down_access_point_count"] == 1

    def test_unreadable_controllers_leave_the_board_as_it_was(self, site, monkeypatch):
        monkeypatch.setattr(settings, "WIRELESS_CONTROLLERS", (DK,))
        monkeypatch.setattr(wireless, "read_index", lambda conn: 1 / 0)
        assert build()["down_device_count"] == 2

    def test_a_scoped_tenant_counts_its_own(self):
        devices = [
            {"id": "ap1", "name": "ap1", "role": "Access Point", "status": "Active"},
            {"id": "ap2", "name": "ap2", "role": "Access Point", "status": "Active"},
        ]
        scoped = alerts.tenant_scoped_alerts(
            {"acme": ["access point"]},
            ["Acme"],
            devices,
            [],
            [],
            None,
            "2026-10-09T12:00:00Z",
            None,
            {},
            "",
            controller_down=lambda device: device["name"] == "ap2",
        )
        assert scoped["acme"]["alert_level"] == "ok"
        assert (scoped["acme"]["access_point_count"], scoped["acme"]["down_access_point_count"]) == (2, 1)


class TestStatusPage:
    def test_an_operational_site_says_its_access_points_down(self):
        down = {"device_count": 40, "access_point_count": 40, "down_access_point_count": 2}
        board = {
            "alerts": [
                {"name": "Fine", "tenant": "Acme", "alert_level": "ok", **down},
                {"name": "Work", "tenant": "Acme", "alert_level": "maintenance", **down},
            ]
        }
        result = status.tenant_status(board, "Acme")
        assert result["summary"] == {"sites": 2, "affected": 0, "devices_down": 2}
        assert result["sites"][1]["access_points_down"] == 2


class TestTopBarAndMetrics:
    def test_each_instance_in_the_top_bar(self):
        rows = {
            "nautobot_inventory": {"status": "idle", "last_succeeded_at": "2026-10-09T12:00:00Z"},
            "wireless:catalyst_center:dk": {"status": "error", "last_succeeded_at": "2026-10-09T11:00:00Z"},
            "wireless:catalyst_center:se": {"status": "idle", "last_succeeded_at": "2026-10-09T12:00:00Z"},
        }
        now = _utc("2026-10-09T12:01:00Z").timestamp()
        body = metrics.app_status(
            rows,
            librenms=False,
            intervals={"INVENTORY_SYNC_INTERVAL_SECONDS": 300, "WIRELESS_SYNC_INTERVAL_SECONDS": 300},
            now=now,
            wireless=(
                ("wireless:catalyst_center:dk", "Catalyst Center dk"),
                ("wireless:catalyst_center:se", "Catalyst Center se"),
            ),
        )
        # Amber, not red: a controller only adds counted access points; the
        # board's freshness is Nautobot's.
        assert body["state"] == "stale"
        assert body["updated_at"] == "2026-10-09T12:00:00Z"
        assert [(s["name"], s["state"], s["message"]) for s in body["sources"]] == [
            ("Nautobot", "ok", "Nautobot is up to date"),
            ("Catalyst Center dk", "stale", "Can't reach Catalyst Center dk"),
            ("Catalyst Center se", "ok", "Catalyst Center se is up to date"),
        ]

    def test_metrics_per_instance(self, controllers, monkeypatch):
        controllers["dk"] = [ap("dk-ap1", True, "FGL1"), ap("dk-ap2", False, "FGL2")]
        controllers["se"] = [ap("se-ap1", False)]
        conn = db.get_conn()
        try:
            inventory.write_devices(conn, [{"id": "d1", "location_id": "x", "name": "x", "serial": "fgl2"}])
        finally:
            conn.close()
        wireless.sync_due()
        conn = db.get_conn()
        try:
            text = metrics.collect(conn)
        finally:
            conn.close()
        assert 'nautobot_maps_wireless_access_points{controller="catalyst_center:dk",state="down"} 1' in text
        assert 'nautobot_maps_wireless_access_points{controller="catalyst_center:se",state="down"} 1' in text
        assert 'nautobot_maps_wireless_access_points_matched{controller="catalyst_center:dk"} 1' in text
        assert 'nautobot_maps_wireless_access_points_matched{controller="catalyst_center:se"} 0' in text
        assert 'nautobot_maps_sync_failing{source="wireless:catalyst_center:dk"} 0' in text


class TestCheck:
    def test_each_instance_matched_and_duplicates(self, controllers, monkeypatch):
        monkeypatch.setattr(
            inventory,
            "read_devices",
            lambda: [{"serial": "FGL1", "name": "x"}, {"serial": "", "name": "se-ap1", "role": "Access Point"}],
        )
        controllers["dk"] = [ap("dk-ap1", True, "FGL1"), ap("lab-ap", False, "FGL7")]
        controllers["se"] = [ap("se-ap1", False, "FGL7")]
        dk, se, duplicates = wireless.check()
        assert (dk["access_points"], dk["down"], dk["matched"], dk["unmatched_examples"]) == (2, 1, 1, ["lab-ap"])
        assert se["matched"] == 1
        assert duplicates["duplicates"] == {"FGL7": ["Catalyst Center dk", "Catalyst Center se"]}

    def test_the_command(self, controllers, monkeypatch, capsys):
        monkeypatch.setattr(inventory, "read_devices", lambda: [])
        controllers["dk"] = [ap("dk-ap1", True, "FGL1")]
        controllers["se"] = requests.ConnectionError("refused")
        assert cli.main(["wireless-check"]) == 1
        out = capsys.readouterr().out
        assert "Catalyst Center dk (https://dnac-dk.example.com): 1 access points, 0 down, 0 match" in out
        assert "Catalyst Center se (https://dnac-se.example.com): can't read it: refused" in out
        assert "s3cret" not in out
        monkeypatch.setattr(settings, "WIRELESS_CONTROLLERS", ())
        assert cli.main(["wireless-check"]) == 0
