// Pieces of the site panel, as HTML: no page access, so the tests run them as they are.
import { nautobotCircuitUrl, nautobotLocationUrl } from "../lib/format.js";
import { escHtml, infoTipHtml, ownDescription } from "../lib/html.js";
import { maintenanceHistoryHref } from "../lib/maintenance-form.js";
import { ALERT_RANK, LEVEL_NAMES, knownLevel } from "./levels.js";
import { config } from "./state.js";

export function badgeClass(status) {
  const s = (status || "").toLowerCase();
  if (s === "active") return "badge-active";
  if (s === "planned") return "badge-planned";
  return "badge-other";
}

// The Tenant row, with an (i) for the tenant's Nautobot description.
export function tenantRow(loc) {
  if (!loc.tenant) return "";
  const description = ownDescription(loc.tenant_descriptions, loc.tenant);
  const tip = description ? infoTipHtml(description, `${loc.tenant}: ${description}`) : "";
  return `<div class="inspector-row">
    <span class="inspector-row-label">Tenant</span>
    <span class="inspector-row-value">${escHtml(loc.tenant)}${tip}</span>
  </div>`;
}

export function inspectorRow(label, value) {
  if (!value && value !== 0) return "";
  return `<div class="inspector-row">
    <span class="inspector-row-label">${escHtml(label)}</span>
    <span class="inspector-row-value">${escHtml(String(value))}</span>
  </div>`;
}

export function normalizeDeviceHealth(status) {
  const value = (status || "").trim().toLowerCase();
  if (["active", "online", "up"].includes(value)) return "active";
  if (value.includes("offline") || ["down", "failed", "inactive"].includes(value)) return "down";
  return "unknown";
}

export function deviceFilterLabel(filterKey) {
  if (filterKey === "active") return "Active";
  if (filterKey === "down") return "Down";
  if (filterKey === "unknown") return "Unknown";
  return "All";
}

export function buildNautobotLink(loc) {
  if (!config.nautobotUrl || !loc.id) return "";
  return `<a class="nautobot-link" href="${escHtml(nautobotLocationUrl(config.nautobotUrl, loc.id))}" target="_blank" rel="noopener noreferrer">Open in Nautobot ↗</a>`;
}

// The site's maintenance history (#353), as on the board's ⋯ menu.
export function buildMaintenanceHistoryLink(loc) {
  if (!loc.id) return "";
  return `<a class="nautobot-link" href="${escHtml(maintenanceHistoryHref(loc.id))}">Maintenance history</a>`;
}

export function deviceStatusClass(status) {
  const health = normalizeDeviceHealth(status);
  if (health === "active") return "device-status-active";
  if (health === "down") return "device-status-offline";
  return "device-status-other";
}

// Found by its description being the site's address, not a termination (#383).
export function circuitMatchNote(circuit) {
  if (circuit.match !== "address") return "";
  const others = Number(circuit.shared_with) || 0;
  const shared = others > 0 ? ` · address shared with ${others} other site${others === 1 ? "" : "s"}` : "";
  return `<div class="device-meta circuit-match" title="The circuit's description is this site's address; it has no termination here in Nautobot">By address${shared}</div>`;
}

// One card per circuit termination at the site (#235), then those found by address (#383).
export function buildCircuitCard(circuit) {
  const { nautobotUrl } = config;
  const title = nautobotUrl && circuit.id
    ? `<a class="circuit-link" href="${escHtml(nautobotCircuitUrl(nautobotUrl, circuit.id))}" target="_blank" rel="noopener noreferrer">${escHtml(circuit.cid)}</a>`
    : escHtml(circuit.cid);
  const kind = [circuit.provider, circuit.circuit_type].filter(Boolean).map(escHtml).join(" · ");
  let speed = circuit.port_speed || "";
  if (circuit.upstream_speed && circuit.upstream_speed !== circuit.port_speed) {
    speed = `${speed || "?"} down / ${circuit.upstream_speed} up`;
  }
  const termination = [circuit.term_side ? `Side ${circuit.term_side}` : "", speed].filter(Boolean).map(escHtml).join(" · ");
  return `<li class="device-card circuit-card">
    <div class="device-card-header">
      <div class="device-card-title">${title}</div>
      <span class="device-status-badge ${deviceStatusClass(circuit.status)}">${escHtml(circuit.status || "Unknown")}</span>
    </div>
    <div class="device-card-body">
      ${circuitMatchNote(circuit)}
      ${kind ? `<div class="device-meta">${kind}</div>` : ""}
      ${termination ? `<div class="device-meta">${termination}</div>` : ""}
      ${circuit.commit_rate ? `<div class="device-meta">Commit rate: ${escHtml(circuit.commit_rate)}</div>` : ""}
      ${circuit.xconnect_id ? `<div class="device-meta">Cross-connect: ${escHtml(circuit.xconnect_id)}</div>` : ""}
      ${circuit.pp_info ? `<div class="device-meta">Patch panel: ${escHtml(circuit.pp_info)}</div>` : ""}
      ${circuit.tenant ? `<div class="device-meta">Tenant: ${escHtml(circuit.tenant)}</div>` : ""}
      ${circuit.description ? `<div class="device-meta">${escHtml(circuit.description)}</div>` : ""}
    </div>
  </li>`;
}

// The site's state in one line (#394): its level's colour and name, how many
// are down, since when, and the reason under it.  Replaces a pill and a banner.
export function statusLineHtml({ alert, counts, since = "", loading = false, error = "" }) {
  if (loading) return `<div class="site-status is-loading">Loading alert status…</div>`;
  if (error) return `<div class="site-status is-no_data">Alert status unavailable</div>`;
  const level = knownLevel(alert?.level || "no_data");
  const monitored = counts && Number.isFinite(counts.monitored) ? counts.monitored : null;
  let what;
  if (ALERT_RANK[level] && monitored !== null) what = `${counts.down || 0} of ${monitored} down`;
  else if (level === "ok" && monitored !== null) what = `${monitored} device${monitored === 1 ? "" : "s"}`;
  else if (level === "no_data") what = "no monitored devices";
  const parts = [LEVEL_NAMES[level], what, ALERT_RANK[level] && since ? `since ${since}` : ""].filter(Boolean);
  // The reason says more than the line only for an alarm or maintenance.
  const reason = ALERT_RANK[level] || level === "maintenance" ? alert?.reason || "" : "";
  return `<div class="site-status is-${level}">`
    + `<div class="site-status-line"><span class="legend-dot dot-${level.replace("_", "-")}" aria-hidden="true"></span>${escHtml(parts.join(" · "))}</div>`
    + `${reason ? `<div class="site-status-reason">${escHtml(reason)}</div>` : ""}</div>`;
}

// A device's key in the panel's open/closed state: its id, else what tells
// it apart (a map without the database has no ids).
export function deviceKey(device) {
  return String(device.id || [device.name, device.serial, device.primary_ip].join("|"));
}

// Whether a down device counts towards the site's level: not in maintenance,
// within the alert delay or unmonitored (#316).
function counts(device) {
  return !device.not_monitored && !device.maintenance_until && !device.alert_pending;
}

// The down devices that count, in the board's order (#394): the level each
// gives the site (its Nautobot field, the override, the tier or a core role,
// #377), core devices first within it (the likely cause, #285), then by name.
// Those that don't count are under All devices, saying why.
const DEVICE_RANK = { critical: 0, medium: 1, low: 2 };
export function sortDownDevices(devices) {
  const rank = (device) => DEVICE_RANK[device.level] ?? 3;
  return devices
    .filter((device) => normalizeDeviceHealth(device.status) === "down" && counts(device))
    .sort((a, b) => rank(a) - rank(b) || Number(Boolean(b.core)) - Number(Boolean(a.core))
      || (a.name || "").localeCompare(b.name || ""));
}

// Stacks (#400): a virtual chassis of two or more as one entry, led by the
// member that stands for it on the board (#405): its monitored master, else its
// first monitored member, else its master, else the first by position; the
// others are its *members*, every other device on its own.  In the given order.
export function groupStacks(devices) {
  const chassis = new Map();
  for (const device of devices) {
    if (!device.vc_id) continue;
    if (!chassis.has(device.vc_id)) chassis.set(device.vc_id, []);
    chassis.get(device.vc_id).push(device);
  }
  const stacks = new Map();
  for (const [id, all] of chassis) {
    if (all.length < 2) continue;
    const byPosition = [...all].sort((a, b) => (a.vc_position ?? Infinity) - (b.vc_position ?? Infinity));
    const monitored = byPosition.filter((d) => d.primary_ip && !d.not_monitored);
    const lead = monitored.find((d) => d.vc_master) || monitored[0] || byPosition.find((d) => d.vc_master) || byPosition[0];
    stacks.set(id, { lead, members: byPosition.filter((d) => d !== lead) });
  }
  const entries = [];
  for (const device of devices) {
    const stack = device.vc_id ? stacks.get(device.vc_id) : null;
    if (!stack) entries.push({ device, members: [] });
    else if (stack.lead === device) entries.push({ device, members: stack.members });
  }
  return entries;
}

// A stack's other members, in its details: place, name, model, serial.  Their
// own status isn't shown: the stack is one device, up or down as one (#405).
function memberHtml(member) {
  const facts = [member.device_type, member.serial ? `Serial ${member.serial}` : ""].filter(Boolean).join(" · ");
  const place = member.vc_position !== null && member.vc_position !== undefined ? `${member.vc_position}. ` : "";
  return `<div class="device-meta stack-member">${escHtml(place + (member.name || "Unnamed device"))}${facts ? ` · ${escHtml(facts)}` : ""}</div>`;
}

// One line per device: its status dot, name and role; *open* shows the rest
// (hardware, IP, serial, software, tenant, why it isn't counted) in place.
// A stack's lead with its *members* (#400) says "Stack of 4" and lists them;
// *size* is the whole stack's when some members are listed elsewhere.
export function deviceRowHtml(device, open = false, members = [], size = members.length + 1) {
  const health = normalizeDeviceHealth(device.status);
  // A stack is one device, its lead (#405): the server makes the lead down
  // when any of the stack's IPs is.
  const dot = health === "down" ? "critical" : health === "active" ? "ok" : "no-data";
  const key = deviceKey(device);
  const stack = members.length ? `Stack of ${size}` : "";
  // Why it doesn't count, where the role would be: clear without opening it (#316);
  // a stack says so as well.
  const note = device.not_monitored ? "Not monitored" : device.maintenance_until ? "Maintenance"
    : device.alert_pending ? "Alert delay" : "";
  const aside = note ? [stack, note].filter(Boolean).join(" · ") : stack || device.role;
  const facts = [
    aside !== device.role ? device.role || "" : "",
    [device.manufacturer, device.device_type].filter(Boolean).join(" "),
    device.primary_ip ? `IP ${String(device.primary_ip).split("/")[0]}` : "",
    device.platform ? `Software ${device.platform}` : "",
    device.serial ? `Serial ${device.serial}` : "",
    device.tenant ? `Tenant ${device.tenant}` : "",
    device.status && health !== "active" ? `Status ${device.status}` : "",
  ].filter(Boolean);
  // Counted down, the whole stack is (not in maintenance or the alert delay).
  // No number: this site may not have every member.
  const whole = health === "down" && !note;
  const details = open
    ? `<div class="device-details">${facts.map((fact) => `<div class="device-meta">${escHtml(fact)}</div>`).join("")}${deviceCountNote(device)}`
      + `${members.length ? `<div class="stack-members"><div class="device-meta">${escHtml(whole
        ? "The whole stack is down, all its members:" : `Stack of ${size}:`)}</div>${members.map(memberHtml).join("")}</div>` : ""}</div>`
    : "";
  return `<li class="device-row-item${open ? " open" : ""}">`
    + `<button class="device-row" type="button" data-device-key="${escHtml(key)}" aria-expanded="${open ? "true" : "false"}">`
    + `<span class="legend-dot dot-${dot}" aria-hidden="true"></span>`
    + `<span class="device-row-name">${escHtml(device.name || "Unnamed device")}</span>`
    + `<span class="device-row-role${note ? " device-row-note" : ""}">${escHtml(aside || "")}</span></button>${details}</li>`;
}

// A folded section with its count (#394): "Circuits · 3".
export function foldHtml(key, title, count, body, open = false) {
  const label = count === null || count === undefined ? title : `${title} · ${count}`;
  return `<details class="panel-fold" data-fold="${escHtml(key)}"${open ? " open" : ""}>`
    + `<summary class="panel-fold-title">${escHtml(label)}</summary><div class="panel-fold-body">${body}</div></details>`;
}

export function buildHealthSummary(devices) {
  const counts = { total: devices.length, active: 0, down: 0, unknown: 0 };
  for (const device of devices) {
    counts[normalizeDeviceHealth(device.status)] += 1;
  }
  return counts;
}

export function filterDevicesForInspector(devices, filterKey, searchTerm) {
  const query = (searchTerm || "").trim().toLowerCase();
  return devices.filter((device) => {
    if (filterKey !== "all" && normalizeDeviceHealth(device.status) !== filterKey) return false;
    if (!query) return true;
    const haystack = [
      device.name,
      device.status,
      device.manufacturer,
      device.device_type,
      device.role,
      device.platform,
      device.serial,
      device.tenant,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
}

// Why a device isn't in the site's "N down · M monitored", if it isn't (#316).
export function deviceCountNote(device) {
  let text = "";
  if (device.not_monitored) {
    text = `Not monitored: ${device.not_monitored}`;
  } else if (device.maintenance_until) {
    const until = new Date(device.maintenance_until);
    const when = Number.isNaN(until.valueOf())
      ? ""
      : ` until ${until.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
    text = `In maintenance${when}: not counted`;
  } else if (device.alert_pending) {
    text = "Down less than the alert delay: not an alarm yet";
  }
  return text ? `<div class="device-meta device-not-monitored">${escHtml(text)}</div>` : "";
}
