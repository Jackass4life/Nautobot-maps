"""The map's calmer site panel (#394): what is wrong first, details on demand."""

from tests.js_modules import run_js


def test_down_devices_core_first_and_one_line_each():
    run_js(
        """
import { deviceKey, deviceRowHtml, foldHtml, sortDownDevices, statusLineHtml } from "@js/map/panel.js";
const devices = [
  { id: "a", name: "sw-b", status: "Offline", role: "Access Switch" },
  { id: "b", name: "sw-a", status: "Offline", role: "Access Switch", level: "low" },
  { id: "c", name: "fw", status: "Offline", role: "Firewall", level: "critical" },
  { id: "d", name: "core", status: "Offline", role: "Core Router", core: true, level: "critical" },
  // A core role set Low in Nautobot: the board weighs it Low (#377).
  { id: "f", name: "core-lab", status: "Offline", role: "Core Router", core: true, level: "low" },
  { id: "e", name: "up", status: "Active", role: "Access Switch" },
  // Down but not counted: under All devices, not in the Down list (#316).
  { id: "m", name: "maint", status: "Offline", level: "critical", maintenance_until: "2026-10-10T21:00:00Z" },
  { id: "p", name: "pending", status: "Offline", alert_pending: true },
  { id: "n", name: "unmonitored", status: "Offline", not_monitored: "no primary IP" },
];
// The board's level first, core first within it, then by name; up and uncounted devices left out.
const order = sortDownDevices(devices).map((d) => d.name).join();
check(order === "core,fw,core-lab,sw-a,sw-b", order);

// One line: dot, name, role; details only when open.
const closed = deviceRowHtml({ id: "x<1", name: "sw <1>", status: "Offline", role: "Access", primary_ip: "10.0.0.1/32", serial: "S1" });
check(closed.includes('data-device-key="x&lt;1"') && closed.includes("sw &lt;1&gt;") && !closed.includes("<1>"), "escaped");
check(closed.includes("dot-critical") && closed.includes('aria-expanded="false"') && !closed.includes("device-details"), closed);
const open = deviceRowHtml({ id: "x", name: "sw", status: "Offline", role: "Access", primary_ip: "10.0.0.1/32", serial: "S1", manufacturer: "Cisco", device_type: "C9200" }, true);
check(open.includes('aria-expanded="true"') && open.includes("Cisco C9200") && open.includes("IP 10.0.0.1<") && open.includes("Serial S1"), open);
check(!open.includes(">Access<\\/div>"), "the role isn't repeated in the details");
// Why it doesn't count, where the role would be.
check(deviceRowHtml({ name: "m", status: "Offline", role: "Access", maintenance_until: "2026-10-10T21:00:00Z" }).includes(">Maintenance<"), "maintenance");
check(deviceRowHtml({ name: "p", status: "Offline", alert_pending: true }).includes(">Alert delay<"), "delay");
check(deviceRowHtml({ name: "n", status: "Active", not_monitored: "no primary IP" }).includes(">Not monitored<"), "not monitored");
check(deviceRowHtml({ name: "u", status: "Active" }).includes("dot-ok"), "up: green");
check(deviceKey({ id: "d1", name: "n" }) === "d1", "by id");
check(deviceKey({ id: "", name: "Unknown", serial: "S1" }) !== deviceKey({ id: "", name: "Unknown", serial: "S2" }), "without ids, told apart");

// Folds: a count when there is one; open as asked.
check(foldHtml("circuits", "Circuits", 3, "<p>x</p>").includes(">Circuits · 3<") && !foldHtml("c", "C", 1, "").includes(" open"), "closed by default");
check(foldHtml("details", "Details", null, "", true).includes(">Details<") && foldHtml("d", "D", null, "", true).includes(" open"), "open");
check(foldHtml("<k>", "<t>", 1, "").includes('data-fold="&lt;k&gt;"') && foldHtml("k", "<t>", 1, "").includes("&lt;t&gt; · 1"), "escaped");

// The status line while loading or failed.
check(statusLineHtml({ loading: true }).includes("Loading alert status…"), "loading");
check(statusLineHtml({ error: "x" }).includes("Alert status unavailable"), "error");
check(statusLineHtml({ alert: { level: "maintenance", reason: "Core upgrade" } }).includes("In maintenance") , "maintenance");
check(statusLineHtml({ alert: { level: "<x>" } }).includes("is-no_data"), "an unknown level is No data");
"""
    )
