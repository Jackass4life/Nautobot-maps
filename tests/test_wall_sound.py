"""Wall view: a chime and a notification for a new Critical (#290)."""

import re
import shutil
import subprocess

import pytest

import app as flask_app
from tests.test_integration import REPO_ROOT, _extract_js_function


def run(body):
    if shutil.which("node") is None:
        pytest.skip("node is required")
    js = (REPO_ROOT / "static" / "js" / "alerts.js").read_text(encoding="utf-8")
    levels = re.search(r"const ALARM_LEVELS = \[[^\]]*\];", js).group(0)
    functions = "\n".join(
        _extract_js_function(js, n) for n in ("downDeviceList", "siteCaseState", "newAlarms", "alarmText")
    )
    script = f"function check(c, m) {{ if (!c) throw new Error(m); }}\n{levels}\n{functions}\n{body}"
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_once_per_new_critical_or_site_nobody_is_on():
    run("""
const down = (extra = {}) => [{ device_id: "d1", ...extra }];
let sites = [
  { id: "a", name: "Aarhus", alert_level: "critical", down_devices: down({ case_numbers: ["INC-1"] }) },
  { id: "o", name: "Oslo", alert_level: "ok", down_devices: [] },
];
// The first load records what is there: no alert for what was already wrong.
let result = newAlarms(null, sites);
check(result.events.length === 0, "first load: nothing");
let seen = result.seen;

// Oslo goes Critical with nobody on it: two things, once each.
sites[1] = { id: "o", name: "Oslo", alert_level: "critical", down_devices: down() };
result = newAlarms(seen, sites);
check(JSON.stringify(result.events.map((e) => e.kind)) === '["critical","nobody"]', JSON.stringify(result.events));
check(alarmText(result.events[0]) === "Oslo is Critical", alarmText(result.events[0]));
check(alarmText(result.events[1]) === "Oslo: in alarm, nobody on it", alarmText(result.events[1]));
seen = result.seen;

// The next refresh: nothing new.
check(newAlarms(seen, sites).events.length === 0, "not on every refresh");

// Someone acknowledges; then it drops to Medium and goes Critical again: alert again.
sites[1] = { id: "o", name: "Oslo", alert_level: "medium", down_devices: down({ acknowledged: { by: "olga" } }) };
seen = newAlarms(seen, sites).seen;
sites[1] = { ...sites[1], alert_level: "critical" };
result = newAlarms(seen, sites);
check(JSON.stringify(result.events.map((e) => e.kind)) === '["critical"]', "a new Critical, but someone is on it");

// A site that recovers and comes back with nobody on it is announced again.
sites[1] = { id: "o", name: "Oslo", alert_level: "ok", down_devices: [] };
seen = newAlarms(seen, sites).seen;
sites[1] = { id: "o", name: "Oslo", alert_level: "low", down_devices: down() };
check(newAlarms(seen, sites).events.map((e) => e.kind).join() === "nobody", "back in alarm, nobody on it");
""")


def test_the_button_is_on_the_wall_only():
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        wall = client.get("/alerts?view=wall").get_data(as_text=True)
        board = client.get("/alerts").get_data(as_text=True)
    assert 'id="wall-sound"' in wall and 'aria-pressed="false">Sound off<' in wall
    assert 'id="wall-sound"' not in board


def test_at_most_five_notifications_open_the_rest_wait():
    if shutil.which("node") is None:
        pytest.skip("node is required")
    js = (REPO_ROOT / "static" / "js" / "alerts.js").read_text(encoding="utf-8")
    functions = "\n".join(_extract_js_function(js, n) for n in ("alarmText", "showQueuedNotifications"))
    script = f"""
function check(c, m) {{ if (!c) throw new Error(m); }}
const shown = [];
globalThis.window = {{}};
class FakeNotification {{
  constructor(title, options) {{ this.body = options.body; this.listeners = []; shown.push(this); }}
  addEventListener(kind, fn) {{ this.listeners.push(fn); }}
  close() {{ this.listeners.forEach((fn) => fn()); }}
}}
FakeNotification.permission = "granted";
window.Notification = FakeNotification;
globalThis.Notification = FakeNotification;
const MAX_OPEN_NOTIFICATIONS = 5;
const openNotifications = new Set();
const queuedNotifications = [];
{functions}
for (let n = 1; n <= 7; n += 1) queuedNotifications.push({{ id: "s" + n, name: "Site " + n, kind: "critical" }});
showQueuedNotifications();
check(shown.length === 5 && queuedNotifications.length === 2, "five open, two waiting");
shown[0].close();
check(shown.length === 6 && shown[5].body === "Site 6 is Critical", "a slot frees: the next one");
shown[1].close();
check(shown.length === 7 && queuedNotifications.length === 0, "none dropped");
"""
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr or completed.stdout
