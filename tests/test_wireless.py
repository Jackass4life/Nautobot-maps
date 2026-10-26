"""Access point status from the wireless controllers (#508): Catalyst Center
and New Central, any number of instances; on the board without a primary IP,
at WIRELESS_AP_LEVEL under the Wireless chip, never notified (#521)."""

import os

import pytest
import requests

from nautobot_maps import __main__ as cli
from nautobot_maps import (
    alerts,
    arubacentral,
    availability,
    caching,
    catalyst,
    contract,
    db,
    inventory,
    metrics,
    notify,
    scheduler,
    settings,
    status,
    wireless,
)
from nautobot_maps.repositories import board as board_store
from tests import test_maintenance
from tests.test_app import _snapshot_device, _snapshot_location

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


def ap(name: str, up: bool, serial: str = "", ip: str = "") -> dict:
    return {"serial": serial, "mac": "", "name": name, "up": up, "ip": ip}


def ups(keys: dict) -> dict:
    """An index's keys and whether each AP is up."""
    return {key: found["up"] for key, found in keys.items()}


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
            ("catalyst_center: [{name: dk, url: 'ftp://x', username: u, password: p}]", "absolute https"),
            # Credentials travel over it: https, but for this machine (the demo's mock).
            ("catalyst_center: [{name: dk, url: 'http://dnac.example', username: u, password: p}]", "must be https"),
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
            ("{name: eu, region: de1, token_url: 'http://sso.x', client_id: c, client_secret: s}", "must be https"),
        ],
    )
    def test_aruba_central_mistakes(self, tmp_path, entry, message):
        with pytest.raises(RuntimeError) as caught:
            settings._wireless_controllers(self.write(tmp_path, f"aruba_central: [{entry}]"))
        assert message in str(caught.value)
        assert "s}" not in str(caught.value)

    def test_localhost_may_be_http(self, tmp_path):
        path = self.write(
            tmp_path, "catalyst_center: [{name: demo, url: 'http://127.0.0.1:8080', username: u, password: p}]"
        )
        assert settings._wireless_controllers(path)[0]["url"] == "http://127.0.0.1:8080"

    @pytest.mark.skipif(os.getuid() == 0, reason="root reads any file")
    def test_an_unreadable_file_says_what_to_do(self, tmp_path, monkeypatch):
        # In the container the app is uid/gid 10001, not the file's owner on the host (#517).
        path = self.write(tmp_path, "catalyst_center: []")
        os.chmod(path, 0)
        with pytest.raises(RuntimeError, match="can't be read by this process"):
            settings._wireless_controllers(path)
        monkeypatch.setattr(os, "getgid", lambda: settings.CONTAINER_GID)
        with pytest.raises(RuntimeError, match="sudo chgrp 10001 config/<file> && chmod 640"):
            settings._wireless_controllers(path)

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
            {
                "serialNumber": " FGL1 ",
                "hostname": "ap1",
                "macAddress": "AA:BB",
                "managementIpAddress": "10.20.31.47",
                "reachabilityStatus": "Unreachable",
            }
        ) == {"serial": "FGL1", "mac": "aa:bb", "name": "ap1", "ip": "10.20.31.47", "up": False}
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

    def test_hpe_sign_in_is_always_verified(self, fake_central):
        # verify_ssl is for a Central behind a private CA or in a lab, never for the secret.
        fake_central.total = 1
        verified = []
        original = fake_central.request

        def request(method, url, timeout=None, verify=None, **kwargs):
            verified.append((url.rsplit("/", 1)[-1], verify))
            return original(method, url, timeout=timeout, verify=verify, **kwargs)

        fake_central.request = request
        arubacentral.fetch_aps({**EU, "verify_ssl": False})
        assert verified == [("token.oauth2", True), ("aps", False)]

    @pytest.mark.parametrize("body", [b"[]", b'{"token_type": "Bearer"}'])
    def test_a_sign_in_without_a_token_says_so(self, fake_central, body):
        original = fake_central.request

        def request(method, url, **kwargs):
            response = original(method, url, **kwargs)
            if url.endswith("token.oauth2"):
                response._content = body
            return response

        fake_central.request = request
        with pytest.raises(RuntimeError, match="sign-in answered without an access token"):
            arubacentral.fetch_aps(EU)

    def test_a_cursor_that_repeats_stops(self, fake_central, monkeypatch):
        monkeypatch.setattr(arubacentral, "get", lambda controller, path, params: {"items": [{}], "next": "2"})
        with pytest.raises(RuntimeError, match="cursor repeated"):
            arubacentral.fetch_aps(EU)
        monkeypatch.setattr(arubacentral, "get", lambda controller, path, params: [])
        with pytest.raises(RuntimeError, match="something other than an object"):
            arubacentral.fetch_aps(EU)

    def test_what_is_kept_of_an_ap(self):
        assert arubacentral.ap_record(
            {
                "serialNumber": "CNF1",
                "deviceName": "ap1",
                "macAddress": "20:4C:03:AA",
                "ipv4": "10.9.0.4",
                "status": "OFFLINE",
            }
        ) == {"serial": "CNF1", "mac": "20:4c:03:aa", "name": "ap1", "ip": "10.9.0.4", "up": False}
        assert arubacentral.ap_record({"ipAddress": "10.9.0.5"})["ip"] == "10.9.0.5"
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
        assert ups(index["serial"]) == {"FGL1": True, "FGL2": False}
        assert ups(index["name"]) == {"dk-ap1": True, "dk-ap2": False, "se-ap1": False}
        assert index["serial"]["FGL2"] == {"up": False, "ip": "", "controller": "Catalyst Center dk", "stale": False}
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
        assert ups(read_index()["serial"]) == {"FGL1": False, "FGL9": False}

    def test_an_empty_answer_keeps_the_last_one(self, controllers):
        controllers["dk"] = [ap("dk-ap1", False, "FGL1")]
        wireless.sync_due()
        controllers["dk"] = []
        wireless.sync_controller(DK)
        assert sync_states()["dk"]["status"] == "error"
        assert ups(read_index()["serial"]) == {"FGL1": False}

    def test_stale_kept_as_last_known_removed_ignored(self, controllers, monkeypatch):
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
        # se's answer is old: kept as its last known (#521), so it never clears the board.
        index = read_index()
        assert ups(index["serial"]) == {"FGL1": False, "FGL9": False}
        assert (index["serial"]["FGL1"]["stale"], index["serial"]["FGL9"]["stale"]) == (False, True)
        assert wireless.reason(index["serial"]["FGL9"]) == "last known per Catalyst Center se, which can't be reached"
        assert wireless.reason(index["serial"]["FGL1"]) == "per Catalyst Center dk"
        monkeypatch.setattr(settings, "WIRELESS_CONTROLLERS", (SE,))
        assert ups(read_index()["serial"]) == {"FGL9": False}, "dk is no longer configured"

    def test_the_same_ap_in_two_instances(self, controllers):
        controllers["dk"] = [ap("ap1", False, "FGL1")]
        controllers["se"] = [ap("ap1", True, "FGL1")]
        wireless.sync_due()
        conn = db.get_conn()
        try:
            # Read at the same moment: up wins, a stale copy never shows it down.
            conn.execute("UPDATE wireless_ap_status SET seen_at = '2026-10-09T12:00:00Z'")
            assert ups(wireless.read_index(conn, now=_utc("2026-10-09T12:01:00Z"))["serial"]) == {"FGL1": True}
            # The newest answer wins.
            conn.execute(
                "UPDATE wireless_ap_status SET seen_at = '2026-10-09T12:00:30Z' WHERE controller = 'catalyst_center:dk'"
            )
            assert ups(wireless.read_index(conn, now=_utc("2026-10-09T12:01:00Z"))["serial"]) == {"FGL1": False}
        finally:
            conn.close()

    def test_matching_by_serial_then_name(self):
        def found(up: bool) -> dict:
            return {"up": up, "ip": "", "controller": "Catalyst Center dk", "stale": False}

        index = {
            "serial": {"FGL1": found(False), "FGL2": found(True)},
            "name": {"ap7": found(False), "ap8": found(True)},
        }
        ap_role = {"role": "Access Point"}

        def up(device):
            return (wireless.lookup(index, device) or {}).get("up")

        assert up({"serial": "fgl1", "name": "ap8"}) is False
        assert up({"serial": "FGL2", "name": "ap7", **ap_role}) is True, "the serial decides"
        assert up({"serial": "FGL3", "name": "AP7.corp.example", **ap_role}) is False
        # By name only for an access point: a switch called ap7 isn't one.
        assert wireless.lookup(index, {"serial": "", "name": "ap7", "role": "Access Switch"}) is None
        assert wireless.lookup(index, {"serial": "", "name": "ap9", **ap_role}) is None
        assert wireless.lookup(None, {"name": "ap7", **ap_role}) is None
        # Attached as new dicts, only to those a controller lists.
        devices = [{"serial": "FGL1", "name": "x"}, {"serial": "", "name": "sw1"}]
        attached = wireless.attach(devices, index)
        assert attached[0]["wireless"]["up"] is False and "wireless" not in devices[0]
        assert attached[1] is devices[1]

    def test_one_sites_devices_read_only_theirs(self, controllers):
        controllers["dk"] = [
            ap("ap-01", False, "FGL1", "10.0.0.1"),
            ap("ap-02", True, "FGL2"),
            ap("ap-01", True, "FGL3"),
        ]
        wireless.sync_due()
        conn = db.get_conn()
        try:
            index = wireless.read_index(
                conn, devices=[{"serial": "fgl2", "name": "x"}, {"name": "ap-01", "role": "Access Point"}]
            )
        finally:
            conn.close()
        assert sorted(index["serial"]) == ["FGL1", "FGL2", "FGL3"], "FGL1 and FGL3 by their name"
        assert "ap-01" not in index["name"], "two APs carry it: still left out"

    def test_the_board_is_rebuilt_for_anything_it_shows(self, controllers, monkeypatch):
        controllers["dk"] = [ap("ap1", True, "FGL1", "10.0.0.1")]
        assert wireless.sync_due() is True, "a newly listed AP is monitored now"
        monkeypatch.setattr(inventory, "state_due", lambda state, interval: True)
        assert wireless.sync_due() is False, "nothing changed"
        controllers["dk"] = [ap("ap1", True, "FGL1", "10.0.0.2")]
        assert wireless.sync_due() is True, "its address changed"

    def test_a_name_two_aps_carry_is_not_matched(self, controllers):
        # ap-01 at two sites, in one instance or two: neither is matched by name.
        controllers["dk"] = [ap("ap-01", False, "FGL1"), ap("ap-02", False), ap("ap-02", True, "FGL3")]
        controllers["se"] = [ap("ap-01", True, "FGL2"), ap("ap-04", False, "FGL1")]
        wireless.sync_due()
        index = read_index()
        assert "ap-01" not in index["name"] and "ap-02" not in index["name"]
        assert index["name"]["ap-04"]["up"] is False, "FGL1 in dk and se is one AP"
        assert index["serial"]["FGL1"]["up"] in (True, False)

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
            assert ups(wireless.read_index(conn, now=_utc("2026-10-09T12:01:00Z"))["serial"]) == {"FGL1": True}
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


SWITCHES = [f"sw{n}" for n in range(1, 7)]
APS = [f"ap{n:02d}" for n in range(1, 41)]


def write_sites(sites: dict[str, dict[str, str]]) -> None:
    """*sites*: ``{site_id: {device: Nautobot status}}``; ``sw*`` are switches
    with a primary IP, ``ap*`` access points without one (DHCP), serial FGL-<site>-<ap>."""
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            inventory.write_locations(conn, [_snapshot_location(site_id, site_id.title()) for site_id in sites])
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_completed_at=test_maintenance.NOW,
                last_successful_sync=test_maintenance.NOW,
                cache_version=inventory.CACHE_VERSION,
            )
            devices = []
            for site_id, statuses in sites.items():
                for name, state in statuses.items():
                    device = _snapshot_device(f"{site_id}-{name}", site_id, "Access Switch", state)
                    if name.startswith("ap"):
                        device.update(role="Access Point", primary_ip="", serial=f"FGL-{site_id}-{name}")
                    devices.append(device)
            inventory.write_devices(conn, devices)
    finally:
        conn.close()


def controller_says(monkeypatch, down: dict[str, list[str]], stale: bool = False, every: dict | None = None) -> None:
    """The controller lists every AP of *every* (``{site: [ap]}``), those in *down* down."""
    every = every or {site_id: APS for site_id in down}
    index = {"serial": {}, "name": {}}
    for site_id, names in every.items():
        for number, name in enumerate(names, start=1):
            index["serial"][f"FGL-{site_id}-{name}".upper()] = {
                "up": name not in down.get(site_id, []),
                "ip": f"10.20.31.{number}",
                "controller": "Catalyst Center dk",
                "stale": stale,
            }
    monkeypatch.setattr(wireless, "read_index", lambda conn, now=None, devices=None: index)


def one_site(**statuses) -> dict[str, dict[str, str]]:
    """Copenhagen: six switches and 40 access points, all Active in Nautobot unless given."""
    return {"cph": {name: statuses.get(name, "Active") for name in [*SWITCHES, *APS]}}


@pytest.fixture
def board(pg_database, monkeypatch, clock):
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    monkeypatch.setattr(inventory, "ensure_snapshot", lambda *args, **kwargs: False)
    monkeypatch.setattr(settings, "WIRELESS_CONTROLLERS", (DK,))
    notified = []
    monkeypatch.setattr(notify, "enqueue_change", lambda conn, site_id, before, after, *a, **k: notified.append(after))
    return notified


def build_board() -> dict:
    caching.cache.clear()
    return alerts.build_alert_board_payload(snapshot_only=True)


class TestOnTheBoard:
    """Access points get their address by DHCP, so a controller watches them (#521)."""

    def test_monitored_without_a_primary_ip_with_the_controllers_address(self, board, monkeypatch):
        write_sites(one_site())
        controller_says(monkeypatch, {"cph": []})
        (row,) = build_board()["alerts"]
        assert row["alert_level"] == "ok"
        assert (row["device_count"], row["access_point_count"], row["network_device_count"]) == (46, 40, 6)
        controller_says(monkeypatch, {"cph": ["ap03", "ap07"]})
        payload = build_board()
        (row,) = payload["alerts"]
        assert (row["alert_level"], row["alert_reason"], row.get("wireless_only")) == (
            "low",
            "2 access points offline",
            True,
        )
        assert (row["down_access_point_count"], row["down_network_device_count"]) == (2, 0)
        assert payload["summary"]["wireless"] == 1 and payload["summary"]["low"] == 0
        assert payload["summary"]["non_ok"] == 0, "not an alarm"
        ap03 = next(d for d in row["down_devices"] if d["device_name"] == "cph-ap03")
        assert ap03["device_ip"] == "10.20.31.3", "the controller's address; Nautobot has none"
        assert ap03["down_reason"] == "per Catalyst Center dk"
        assert board == [], "never notified"

    def test_access_points_never_dilute_the_share(self, board, monkeypatch):
        write_sites(one_site(sw1="Offline"))
        controller_says(monkeypatch, {"cph": ["ap01"]})
        (row,) = build_board()["alerts"]
        # One switch of six, not one of 46: as without the APs.
        assert row["alert_reason"] == "1/6 devices offline (17%)"
        assert "wireless_only" not in row, "a switch is down: an alarm"
        write_sites(one_site(**dict.fromkeys(SWITCHES, "Offline")))
        controller_says(monkeypatch, {"cph": []})
        (row,) = build_board()["alerts"]
        assert (row["alert_level"], row["alert_reason"]) == ("critical", "All 6 monitored devices offline")

    def test_a_site_already_in_alarm_just_counts_them(self, board, monkeypatch):
        monkeypatch.setattr(settings, "WIRELESS_AP_LEVEL", "critical")
        write_sites(one_site(sw1="Offline"))
        controller_says(monkeypatch, {"cph": APS[:30]})
        (row,) = build_board()["alerts"]
        # The switch's level and reason; 30 APs at the wireless level never raise it.
        assert (row["alert_level"], row["alert_reason"]) == ("low", "1/6 devices offline (17%)")
        assert row["down_access_point_count"] == 30 and "wireless_only" not in row

    def test_a_reason_names_only_the_access_points_set_in_nautobot(self, board, monkeypatch):
        write_sites(one_site())
        conn = db.get_conn()
        try:
            conn.execute("UPDATE nautobot_device_cache SET criticality = 'low' WHERE device_id = 'cph-ap40'")
        finally:
            conn.close()
        controller_says(monkeypatch, {"cph": APS})
        (row,) = build_board()["alerts"]
        assert (row["alert_level"], row["alert_reason"]) == ("low", "Low device(s) offline: cph-ap40 (set in Nautobot)")
        assert "wireless_only" not in row, "set in Nautobot: an alarm"

    def test_every_access_point_down_is_still_the_wireless_level(self, board, monkeypatch):
        write_sites(one_site())
        controller_says(monkeypatch, {"cph": APS})
        (row,) = build_board()["alerts"]
        assert (row["alert_level"], row["alert_reason"], row["wireless_only"]) == (
            "low",
            "40 access points offline",
            True,
        )
        monkeypatch.setattr(settings, "WIRELESS_AP_LEVEL", "medium")
        (row,) = build_board()["alerts"]
        assert (row["alert_level"], row["wireless_only"]) == ("medium", True)

    def test_a_level_set_in_nautobot_is_an_alarm(self, board, monkeypatch):
        write_sites(one_site())
        conn = db.get_conn()
        try:
            conn.execute("UPDATE nautobot_device_cache SET criticality = 'critical' WHERE device_id = 'cph-ap05'")
        finally:
            conn.close()
        controller_says(monkeypatch, {"cph": []})
        build_board()  # a new site's first level is stored, not notified
        controller_says(monkeypatch, {"cph": ["ap05", "ap06"]})
        payload = build_board()
        (row,) = payload["alerts"]
        assert row["alert_level"] == "critical" and "wireless_only" not in row
        assert row["alert_reason"] == "Critical device(s) offline: cph-ap05 (set in Nautobot)"
        assert payload["summary"]["non_ok"] == 1 and board == ["critical"]

    def test_down_until_it_comes_back_or_its_status_changes(self, board, monkeypatch):
        write_sites(one_site())
        controller_says(monkeypatch, {"cph": ["ap09"]})
        build_board()
        assert test_maintenance.open_alerts() == {"cph-ap09": "open"}
        # The controller can't be reached: its last answer stands, and says so.
        controller_says(monkeypatch, {"cph": ["ap09"]}, stale=True)
        (row,) = build_board()["alerts"]
        assert row["down_devices"][0]["down_reason"] == "last known per Catalyst Center dk, which can't be reached"
        assert test_maintenance.open_alerts() == {"cph-ap09": "open"}
        # Decommissioning in Nautobot, a status the board leaves out: no longer counted, its alert resolved.
        monkeypatch.setattr(settings, "ALERT_BOARD_EXCLUDED_DEVICE_STATUSES", {"decommissioning"})
        write_sites(one_site(ap09="Decommissioning"))
        (row,) = build_board()["alerts"]
        assert row["alert_level"] == "ok" and row["device_count"] == 45
        assert test_maintenance.open_alerts() == {"cph-ap09": "resolved"}

    def test_unreadable_controllers_write_no_history(self, board, monkeypatch):
        write_sites(one_site())
        controller_says(monkeypatch, {"cph": ["ap09"]})
        build_board()
        monkeypatch.setattr(wireless, "read_index", lambda conn, now=None, devices=None: 1 / 0)
        build_board()
        assert test_maintenance.open_alerts() == {"cph-ap09": "open"}, "not resolved by a failed read"

    def test_out_of_availability_and_the_feed_but_in_the_history(self, board, monkeypatch):
        write_sites(one_site(sw2="Offline"))
        controller_says(monkeypatch, {"cph": ["ap01"]})
        build_board()
        conn = db.get_conn()
        try:
            flags = {
                r["device_id"]: r["access_point"]
                for r in conn.execute("SELECT device_id, access_point FROM alert_instances")
            }
            feed = board_store.read_alert_feed(conn, 50)
            incidents = availability.read_incidents(
                conn, _utc("2026-10-01T00:00:00Z"), _utc("2026-11-01T00:00:00Z"), _utc(test_maintenance.NOW)
            )
        finally:
            conn.close()
        assert flags == {"cph-sw2": False, "cph-ap01": True}
        assert [event["device_id"] for event in feed if event["kind"] == "down"] == ["cph-sw2"]
        assert list(incidents["cph"]["devices"]) == ["cph-sw2"]

    def test_a_scoped_tenant_sees_wireless_only_too(self):
        devices = [
            {"id": "ap1", "name": "ap1", "role": "Access Point", "status": "offline", "wireless": {"up": False}},
            {"id": "ap2", "name": "ap2", "role": "Access Point", "status": "Active", "wireless": {"up": True}},
        ]
        scoped = alerts.tenant_scoped_alerts(
            {"acme": ["access point"]}, ["Acme"], devices, [], [], None, "2026-10-09T12:00:00Z", None, {}, ""
        )
        assert (scoped["acme"]["alert_level"], scoped["acme"]["wireless_only"]) == ("low", True)
        assert (scoped["acme"]["access_point_count"], scoped["acme"]["down_access_point_count"]) == (2, 1)

    def test_an_access_point_no_controller_lists_says_so(self, board, monkeypatch):
        device = {"role": "Access Point", "primary_ip": ""}
        assert alerts.not_monitored_reason(device) == "no primary IP, not in a wireless controller"
        assert alerts.not_monitored_reason({**device, "role": "Access Switch"}) == "no primary IP"
        monkeypatch.setattr(settings, "WIRELESS_CONTROLLERS", ())
        assert alerts.not_monitored_reason(device) == "no primary IP"

    def test_the_map_and_the_site_panel(self, board, monkeypatch):
        write_sites(one_site())
        controller_says(monkeypatch, {"cph": ["ap02"]})
        build_board()
        caching.cache.clear()
        level = alerts.build_location_alert_levels()["levels"]["cph"]
        assert (level["level"], level["wireless_only"], level["down"], level["monitored"]) == ("low", True, 1, 46)
        monkeypatch.setattr(alerts, "get_location_alert_levels", lambda: {"levels": {"cph": level}})
        monkeypatch.setattr(alerts.nautobot, "fetch_all_pages", lambda *a, **k: [])
        monkeypatch.setattr(alerts, "location_circuits", lambda location_id: ([], ""))
        detail = alerts.get_location_detail("cph")
        ap02 = next(d for d in detail["devices"] if d["name"] == "cph-ap02")
        assert (ap02["status"], ap02["not_monitored"], ap02["level"]) == ("offline", "", "low")
        assert ap02["wireless"] == {"up": False, "ip": "10.20.31.2", "controller": "Catalyst Center dk", "stale": False}
        # A maintenance window can pick it, as any monitored device.
        assert "cph-ap02" in {d["id"] for d in alerts.site_monitored_devices("cph")}

    def test_at_scale_alarms_stay_in_front(self, board, monkeypatch):
        """120 sites: 30 with only APs down (290 APs in all), 5 with a switch down too, as on a normal day."""
        sites, down = {}, {}
        for n in range(120):
            site_id = f"s{n:03d}"
            sites[site_id] = {name: "Offline" if n < 5 and name == "sw1" else "Active" for name in [*SWITCHES, *APS]}
            if n < 35:
                down[site_id] = APS[: 2 + n % 15]
        write_sites(sites)
        controller_says(monkeypatch, down, every={site_id: APS for site_id in sites})
        payload = build_board()
        summary = payload["summary"]
        assert sum(len(names) for names in down.values()) == 290
        assert (summary["non_ok"], summary["wireless"], summary["ok"]) == (5, 30, 85)
        assert sum(row["down_access_point_count"] for row in payload["alerts"]) == 290
        # The alarms first; the wireless-only sites after them.
        assert [row.get("wireless_only", False) for row in payload["alerts"][:5]] == [False] * 5
        assert board == [], "nothing notified: the first build only stores the levels"


class TestStatusPage:
    def test_an_operational_site_says_its_access_points_down(self):
        down = {"device_count": 40, "access_point_count": 40, "down_access_point_count": 2}
        board = {
            "alerts": [
                {"name": "Fine", "tenant": "Acme", "alert_level": "low", "wireless_only": True, **down},
                {"name": "Work", "tenant": "Acme", "alert_level": "maintenance", **down},
            ]
        }
        result = status.tenant_status(board, "Acme")
        sites = {site["name"]: site for site in result["sites"]}
        assert sites["Fine"]["state"] == "operational", "only access points down (#521)"
        assert result["summary"] == {"sites": 2, "affected": 0, "devices_down": 2}
        assert sites["Work"]["access_points_down"] == 2


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
