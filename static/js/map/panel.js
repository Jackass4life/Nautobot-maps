// Pieces of the site panel, as HTML: no page access, so the tests run them as they are.
import { nautobotLocationUrl } from "../lib/format.js";
import { escHtml, infoTipHtml, ownDescription } from "../lib/html.js";
import { maintenanceHistoryHref } from "../lib/maintenance-form.js";
import { ALERT_RANK } from "./levels.js";
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

// One card per circuit termination at the site (#235).
export function buildCircuitCard(circuit) {
  const { nautobotUrl } = config;
  const title = nautobotUrl && circuit.id
    ? `<a class="circuit-link" href="${escHtml(nautobotUrl)}/circuits/circuits/${encodeURIComponent(circuit.id)}/" target="_blank" rel="noopener noreferrer">${escHtml(circuit.cid)}</a>`
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

export function buildAlertBanner(alert) {
  if (!alert || !ALERT_RANK[alert.level]) return "";
  const lvl = alert.level;
  const icon = { critical: "🔴", medium: "🟠", low: "🟡" }[lvl];
  return `<div class="alert-banner alert-${escHtml(lvl)}">
    <span class="alert-banner-icon">${icon}</span>
    <div>
      <div class="alert-banner-level">${escHtml(lvl.toUpperCase())}</div>
      ${alert.reason ? `<div class="alert-banner-reason">${escHtml(alert.reason)}</div>` : ""}
    </div>
  </div>`;
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

export function deviceCard(device) {
  const hardware = [device.manufacturer, device.device_type].filter(Boolean).map(escHtml).join(" · ");
  return `<li class="device-card">
          <div class="device-card-header">
            <div class="device-card-title">${escHtml(device.name || "Unnamed device")}</div>
            <span class="device-status-badge ${deviceStatusClass(device.status)}">${escHtml(device.status || "Unknown")}</span>
          </div>
          <div class="device-card-body">
            ${hardware ? `<div class="device-meta">${hardware}</div>` : ""}
            ${device.role ? `<div class="device-meta device-role">Role: ${escHtml(device.role)}</div>` : ""}
            ${device.platform ? `<div class="device-meta device-platform">Software: ${escHtml(device.platform)}</div>` : ""}
            ${device.serial ? `<div class="device-meta">Serial: ${escHtml(device.serial)}</div>` : ""}
            ${device.tenant ? `<div class="device-meta">Tenant: ${escHtml(device.tenant)}</div>` : ""}
            ${deviceCountNote(device)}
          </div>
        </li>`;
}
