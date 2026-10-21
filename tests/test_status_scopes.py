"""Tenant status scopes through the board build, at scale (#410)."""

import json

from nautobot_maps import db, settings, status
from tests.test_app import _snapshot_device
from tests.test_criticality import build, write


def test_the_board_build_at_scale(board, monkeypatch):
    """Acme on 40 sites, half scoped to its firewalls; a core outage at one
    of them (30 routers down) is not Acme's; a site without firewalls is
    hidden from Acme.  The board itself is unchanged."""
    monkeypatch.setattr(settings, "TENANT_SCOPE_FIELD", "tenant_scope")
    sites, devices = {}, []
    for n in range(40):
        sites[f"s{n}"] = ""
        devices += [_snapshot_device(f"s{n}-fw{i}", f"s{n}", "Firewall", "Active") for i in range(2)]
        devices += [_snapshot_device(f"s{n}-rt{i}", f"s{n}", "Core Router", "Active") for i in range(3)]
    # s0: the core outage, 30 routers down, the firewalls up.
    devices += [_snapshot_device(f"s0-core{i}", "s0", "Core Router", "Offline") for i in range(30)]
    # s1: one of Acme's firewalls down.
    devices = [{**d, "status": "Offline"} if d["id"] == "s1-fw0" else d for d in devices]
    # s39: no firewall at all.
    devices = [d for d in devices if not d["id"].startswith("s39-fw")]
    write(sites, devices)
    conn = db.get_conn()
    try:
        with db.transaction(conn):
            for n in range(40):
                scope = {"acme corp": ["firewall"]} if n % 2 == 0 or n in (1, 39) else {}
                conn.execute(
                    "UPDATE nautobot_location_cache SET tenant = %s, tenant_scope_json = %s WHERE location_id = %s",
                    ("Acme Corp", json.dumps(scope), f"s{n}"),
                )
    finally:
        conn.close()
    rows = build(board, "2026-10-18T12:00:00Z")
    assert rows["s0"]["alert_level"] == "critical" and rows["s39"]["alert_level"] == "ok"
    payload = {"checked_at": "2026-10-18T12:00:00Z", "alerts": list(rows.values())}
    acme = {site["name"]: site["state"] for site in status.tenant_status(payload, "Acme Corp")["sites"]}
    assert len(acme) == 39 and "Site s39" not in acme
    assert acme["Site s0"] == "operational"  # the core outage isn't Acme's
    assert acme["Site s1"] == "degraded"  # one of its two firewalls down
    assert list(acme)[0] == "Site s1"  # affected first
