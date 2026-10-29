"""Take the README's screenshots and clip (docs/images/) from a running app.

Run the mock at a NOC's scale and the app against an empty database, then this:

    MOCK_SCALE=1200 python demo/mock_nautobot.py &
    NAUTOBOT_URL=http://127.0.0.1:8080 NAUTOBOT_TOKEN=demo-token python app.py &
    python -m nautobot_maps token create shots --role admin --expires-days 1
    python scripts/screenshots.py --token nmt_... --seed

Run the app with WIRELESS_CONTROLLERS_FILE=demo/wireless.yml for the access
points.  An operator token works too, without the Admin page's picture.

Needs Playwright (requirements-e2e.txt) and Pillow (`pip install Pillow`). --seed adds
cases, acknowledgements and maintenance through the API first: run it once
per database.
"""

import argparse
import io
import json
import pathlib
import time
import urllib.error
import urllib.request

from PIL import Image
from playwright.sync_api import sync_playwright

WIDTH, HEIGHT = 1440, 900


def api(base, token, path, body=None):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{request.method} {path}: HTTP {exc.code} {exc.read().decode(errors='replace')}") from exc


def wait_for_board(base, token, timeout=300):
    """The board once the first inventory sync has filled it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        board = api(base, token, "/api/alerts").get("alerts") or []
        if any(s["alert_level"] in ("critical", "medium", "low") for s in board):
            return board
        time.sleep(3)
    raise SystemExit("The alert board has no alarms: is the mock running with MOCK_SCALE?")


def seed(base, token, board):
    """A shift in progress: some sites have a case or an ack, some are in maintenance."""
    if any(s["active_cases"] for s in board):
        raise SystemExit("This database already has cases: --seed is for an empty one")
    # Checked before writing anything: a half-seeded database refuses a second --seed.
    quiet = [s for s in board if s["alert_level"] == "ok"]
    if len(quiet) < 60:
        raise SystemExit(f"--seed needs the scale demo (MOCK_SCALE=1200): {len(quiet)} quiet sites, not 60")
    alarms = [s for s in board if s["alert_level"] in ("critical", "medium", "low")]
    alarms.sort(key=lambda s: -s["down_device_count"])
    for number, site in enumerate(alarms[:7], start=48213):
        ids = [d["device_id"] for d in site["down_devices"]]
        api(
            base, token, "/api/alert-cases", {"site_id": site["id"], "case_number": f"INC00{number}", "device_ids": ids}
        )
    for site in alarms[7:10]:
        ids = [d["device_id"] for d in site["down_devices"]]
        api(base, token, "/api/alert-acks", {"site_id": site["id"], "device_ids": ids, "note": "On it, checking power"})
    # Disjoint slices of the quiet sites: 0-2, 3, 10-49, and one from 60 on.
    ids = [s["id"] for s in quiet]
    api(base, token, "/api/maintenance", {"site_ids": ids[:3], "reason": "Firmware upgrade", "duration_minutes": 240})
    api(
        base,
        token,
        "/api/maintenance",
        {"site_id": ids[3], "reason": "Fibre move", "starts_at": "2030-01-01T22:00:00Z", "duration_minutes": 120},
    )
    # The Maintenance page at scale: one change across 40 sites, planned, and one device.
    api(
        base,
        token,
        "/api/maintenance",
        {
            "site_ids": ids[10:50],
            "reason": "Core router upgrade, wave 2",
            "starts_at": "2030-01-04T21:00:00Z",
            "duration_minutes": 360,
        },
    )
    device_site = next(s for s in quiet[60:] if s["device_count"] > 1)
    devices = api(base, token, f"/api/locations/{device_site['id']}/detail").get("devices") or []
    if devices:
        api(
            base,
            token,
            "/api/maintenance",
            {
                "site_id": device_site["id"],
                "device_ids": [devices[0]["id"]],
                "reason": "PSU swap",
                "duration_minutes": 90,
            },
        )
    # The Admin page (#530): the tokens a NOC has; an operator token can't make them.
    if (api(base, token, "/api/page-config") or {}).get("admin") is not None:
        for name, role, tenant in (
            ("librenms", "operator", None),
            ("alertmanager", "operator", None),
            ("mcp-claude", "viewer", None),
            ("tidal-ports-status", "status", STATUS_TENANT),
        ):
            body = {"name": name, "role": role, "expires_in_days": 365}
            api(base, token, "/api/tokens", {**body, "tenant": tenant} if tenant else body)


def with_token(page, base, token):
    """The token on the app's own requests only, never on the map tiles."""

    def add(route):
        route.continue_(headers={**route.request.headers, "authorization": f"Bearer {token}"})

    page.route(f"{base}/**", add)


def shoot(page, out, name):
    """A PNG of 256 colours: a third of the size, the same to the eye.  Octree,
    not median cut: that dropped small, rare colours such as the top bar's
    green status dot."""
    path = out / f"{name}.png"
    shot = Image.open(io.BytesIO(page.screenshot()))
    shot.convert("RGB").quantize(colors=256, method=Image.Quantize.FASTOCTREE).save(path, optimize=True)
    print("wrote", path, f"{path.stat().st_size // 1024} KB")


def open_map(page, base):
    page.goto(base + "/")
    page.wait_for_selector("#alarm-list li", timeout=60000)
    page.wait_for_load_state("networkidle")  # the tiles


def open_site(page, name):
    page.locator("#alarm-list li", has_text=name).first.click()
    page.wait_for_selector("#location-inspector #inspector-down-title", timeout=20000)
    page.wait_for_timeout(1500)


# (colour scheme, page, what shows it has loaded, file); the map and site panel come first.
PAGES = [
    ("light", "/availability", "table tbody tr", "availability"),
    ("light", "/alerts", "#alerts-table-body tr", "alert-board"),
    ("dark", "/alerts", "#alerts-table-body tr", "alert-board-dark"),
    ("dark", "/alerts?view=wall", "#alerts-table-body tr", "wall-view"),
    ("light", "/maintenance", ".maint-batch", "maintenance"),
]
# The status page: a tenant with Critical, Medium and Low sites, as its customer sees it.
STATUS_TENANT = "Tidal Ports"


def screenshots(browser, base, token, out, burst):
    pages = {}
    for scheme in ("light", "dark"):
        ctx = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, color_scheme=scheme)
        pages[scheme] = ctx.new_page()
        with_token(pages[scheme], base, token)
    page = pages["light"]
    open_map(page, base)
    shoot(page, out, "map")
    open_site(page, burst)
    shoot(page, out, "site-panel")
    for scheme, path, loaded, name in PAGES:
        page = pages[scheme]
        page.goto(base + path)
        page.wait_for_selector(loaded, timeout=60000)
        page.wait_for_timeout(1000)  # fonts and the last render
        shoot(page, out, name)
    if (api(base, token, "/api/page-config") or {}).get("admin") is not None:
        page = pages["light"]
        page.goto(base + "/admin")
        page.wait_for_selector("#admin-tokens .token-row", timeout=60000)
        page.wait_for_timeout(1000)
        shoot(page, out, "admin")
    else:
        print("not an admin token: Admin page skipped")
    status_page(browser, base, token, out)
    for page in pages.values():
        page.context.close()


def status_page(browser, base, token, out):
    """The tenant's status page (#291) at its link (#500): narrower, as it is laid out.
    The answer is the same for any viewer allowed to see it, so the operator
    token shows what the customer sees.  The link comes from Availability, the
    one place staff get it (which makes every tenant's link: a demo database)."""
    links = api(base, token, "/api/availability").get("status_links") or {}
    if STATUS_TENANT not in links:
        print("no status link for", STATUS_TENANT, "(is it the scale demo?): status page skipped")
        return
    ctx = browser.new_context(viewport={"width": 1120, "height": 580}, color_scheme="light")
    page = ctx.new_page()
    with_token(page, base, token)
    try:
        page.goto(f"{base}/status/{links[STATUS_TENANT]}")
        page.wait_for_selector(".status-devices", timeout=60000)
        page.wait_for_timeout(1000)
        shoot(page, out, "status-page")
    finally:
        ctx.close()


def clip(browser, base, token, out, burst):
    """Map → a site in alarm → the board → a case on it, as an animated GIF."""

    ctx = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, color_scheme="light")
    page = ctx.new_page()
    with_token(page, base, token)
    frames = []
    durations = []

    def hold(ms, step=250):
        # Each frame lasts as long as it really took, so the clip plays in real time.
        for _ in range(max(1, ms // step)):
            started = time.monotonic()
            shot = Image.open(io.BytesIO(page.screenshot(type="jpeg", quality=90)))
            frames.append(shot.resize((WIDTH * 2 // 3, HEIGHT * 2 // 3), Image.BILINEAR))
            page.wait_for_timeout(step)
            durations.append(round((time.monotonic() - started) * 1000))

    open_map(page, base)
    hold(1500)
    open_site(page, burst)
    hold(2500)
    page.goto(base + "/alerts")
    page.wait_for_selector("#alerts-table-body tr", timeout=60000)
    hold(1500)
    # The top row: the board puts the sites nobody is on first.
    page.locator("#alerts-table-body .case-open-btn").first.click()
    page.wait_for_selector("#case-input")
    hold(1000)
    page.locator("#case-input").press_sequentially("INC0048290", delay=60)
    hold(750)
    page.locator(".case-save-btn").click()
    page.wait_for_timeout(800)
    hold(2500)
    ctx.close()

    path = out / "tour.gif"
    palette = [f.convert("P", palette=Image.ADAPTIVE, colors=128) for f in frames]
    palette[0].save(path, save_all=True, append_images=palette[1:], duration=durations, loop=0, optimize=True)
    print("wrote", path, f"{path.stat().st_size // 1024} KB, {len(frames)} frames")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--token", required=True, help="an admin API token (operator: no Admin page)")
    parser.add_argument("--out", default="docs/images")
    parser.add_argument("--seed", action="store_true", help="add cases, acks and maintenance first")
    parser.add_argument("--no-clip", action="store_true")
    args = parser.parse_args()
    base, out = args.base_url.rstrip("/"), pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    board = wait_for_board(base, args.token)
    if args.seed:
        seed(base, args.token, board)
        board = api(base, args.token, "/api/alerts")["alerts"]
    burst = max(board, key=lambda s: s["down_device_count"])
    with sync_playwright() as p:
        browser = p.chromium.launch()
        screenshots(browser, base, args.token, out, burst["name"])
        if not args.no_clip:
            clip(browser, base, args.token, out, burst["name"])
        browser.close()


if __name__ == "__main__":
    main()
