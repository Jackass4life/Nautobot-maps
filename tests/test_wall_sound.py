"""Wall view: a chime and a notification for a new Critical (#290)."""

import app as flask_app
from tests import http_client
from tests.js_modules import run_js


def run(body):
    run_js('import { alarmText, newAlarms } from "@js/alerts/wall.js";\n' + body)


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
    with http_client.FlaskStyleClient() as client:
        wall = client.get("/alerts?view=wall").get_data(as_text=True)
        board = client.get("/alerts").get_data(as_text=True)
    assert 'id="wall-sound"' in wall and 'aria-pressed="false">Sound off<' in wall
    assert 'id="wall-sound"' not in board


def test_at_most_five_notifications_open_the_rest_wait():
    run_js("""
import { announceNewAlarms, setWallSound, showQueuedNotifications } from "@js/alerts/wall.js";
const shown = [];
class FakeNotification {
  constructor(title, options) { this.body = options.body; this.listeners = []; this.open = true; shown.push(this); }
  addEventListener(kind, fn) { this.listeners.push(fn); }
  close() { this.open = false; this.listeners.forEach((fn) => fn()); }
}
FakeNotification.permission = "granted";
setGlobal("window", { Notification: FakeNotification });
setGlobal("Notification", FakeNotification);
const closeAll = () => { while (shown.some((n) => n.open)) shown.find((n) => n.open).close(); };
// Sites that go Critical with someone on them: one notification each.
const sites = (prefix, count, level) => Array.from({ length: count }, (_, i) => ({
  id: prefix + (i + 1), name: `${prefix.toUpperCase()}${i + 1}`, alert_level: level,
  down_devices: [{ device_id: "d", case_numbers: ["INC-1"] }],
}));
const goCritical = (prefix, count) => {
  announceNewAlarms(sites(prefix, count, "ok"));
  announceNewAlarms(sites(prefix, count, "critical"));
};
setWallSound(true);

goCritical("s", 7);
check(shown.length === 5, "five open, the rest wait: " + shown.length);
shown[0].close();
check(shown.length === 6 && shown[5].body === "S6 is Critical", "a slot frees: the next one");
shown[1].close();
check(shown.length === 7, "none dropped");
closeAll();
check(shown.length === 7, "nothing else was waiting");

// Not answered yet: they wait (the newest 50).
FakeNotification.permission = "default";
goCritical("w", 60);
check(shown.length === 7, "nothing shown before the answer");
FakeNotification.permission = "granted";
showQueuedNotifications();
check(shown[7].body === "W11 is Critical", "the newest 50 waited: " + shown[7].body);
closeAll();
check(shown.length === 7 + 50, "all 50 shown: " + (shown.length - 7));

// Denied or unsupported: dropped.
FakeNotification.permission = "default";
goCritical("x", 3);
FakeNotification.permission = "denied";
showQueuedNotifications();
FakeNotification.permission = "granted";
showQueuedNotifications();
check(shown.length === 57, "denied: nothing kept");
""")
