// A site's row on the alert board, as HTML (#294): no page access, so the
// tests run these as they are.
import { casePill } from "../lib/cases.js";
import { formatDuration, formatFeedTime } from "../lib/format.js";
import { escHtml, infoTipHtml, ownDescription } from "../lib/html.js";
import { maintenanceHistoryHref } from "../lib/maintenance-form.js";
import { formatLocationAddress, siteTenants } from "../lib/scope.js";
import { ALARM_LEVELS, SEVERITY_ORDER } from "./state.js";

// The board's columns (templates/alerts.html), for rows that span them all.
const BOARD_COLUMNS = 5;

// A row of one cell across the whole board: empty, error or a heading.
export function fullWidthRow(html, cellClass = "", rowClass = "") {
  const rowAttr = rowClass ? ` class="${rowClass}"` : "";
  const cellAttr = cellClass ? ` class="${cellClass}"` : "";
  return `<tr${rowAttr}><td colspan="${BOARD_COLUMNS}"${cellAttr}>${html}</td></tr>`;
}

export function severityWeight(level) {
  const index = SEVERITY_ORDER.indexOf(level);
  return index === -1 ? SEVERITY_ORDER.length : index;
}

export function hasActiveAlarm(item) {
  return ALARM_LEVELS.includes(item.alert_level);
}

// "2 alarms · 1 critical · 1 medium": only what is wrong, only levels that
// aren't 0 (#311).
export function summaryText(summary) {
  const alarms = summary.non_ok ?? ALARM_LEVELS.reduce((sum, level) => sum + (summary[level] || 0), 0);
  if (!alarms) return "No alarms";
  const levels = ALARM_LEVELS.filter((level) => summary[level]).map((level) => `${summary[level]} ${level}`);
  return [`${alarms} alarm${alarms === 1 ? "" : "s"}`, ...levels].join(" · ");
}

// The site's expandable device list: down now, or with an alert still open.
// It can be non-empty while down_device_count is 0 (a failed observation
// keeps open alerts), so the toggle, row buttons and History all use this.
export function downDeviceList(item) {
  return Array.isArray(item.down_devices) ? item.down_devices : [];
}

export function caseDevices(item) {
  return downDeviceList(item).filter((d) => d.device_id);
}

// The row keeps the two triage actions (#179, #227); the site name links to
// the map, and History sits under the down devices (in the row when there
// are none to expand).
export function historyButton(item) {
  const siteId = escHtml(item.id || "");
  const siteLabel = `<span class="visually-hidden"> for ${escHtml(item.name || item.id || "site")}</span>`;
  return `<button class="action-btn history-btn" type="button" data-site-id="${siteId}" aria-haspopup="dialog">History${siteLabel}</button>`;
}

// Opens the maintenance panel (#283); on every row, also a site in maintenance (to end it).
export function maintenanceButton(item) {
  // Nothing to put in maintenance without monitored devices (still shown while a window is on).
  if (!item.device_count && !item.maintenance_device_count && !item.maintenance) return "";
  const siteLabel = `<span class="visually-hidden"> for ${escHtml(item.name || item.id || "site")}</span>`;
  return `<button class="action-btn maint-open-btn" type="button" data-site-id="${escHtml(item.id || "")}" aria-haspopup="dialog" title="Maintenance window">Maint.${siteLabel}</button>`;
}

// The site's maintenance history (#353): the Maintenance page, this site, the last 30 days.
export function maintenanceHistoryLink(item) {
  if (!item.id) return "";
  const siteLabel = `<span class="visually-hidden"> for ${escHtml(item.name || item.id)}</span>`;
  return `<a class="action-btn maint-history-link" href="${escHtml(maintenanceHistoryHref(item.id))}">Maint. history${siteLabel}</a>`;
}

// One click: someone is on it (#288); "Remove ack" once every device is.
export function ackButton(item) {
  const devices = caseDevices(item);
  if (!devices.length) return "";
  const siteId = escHtml(item.id || "");
  const siteLabel = `<span class="visually-hidden"> for ${escHtml(item.name || item.id || "site")}</span>`;
  if (devices.every((d) => d.acknowledged)) {
    return `<button class="action-btn unack-btn" type="button" data-site-id="${siteId}">Remove ack${siteLabel}</button>`;
  }
  return `<button class="action-btn ack-btn" type="button" data-site-id="${siteId}" title="Someone is on it, without a case number">Acknowledge${siteLabel}</button>`;
}

// The rest of a row's actions behind "⋯" (#311): a native <details>, so it
// opens without script; choosing an action, Escape or a click elsewhere closes it.
export function rowMenu(item, buttons) {
  const items = buttons.filter(Boolean);
  if (!items.length) return "";
  const label = `More actions for ${escHtml(item.name || item.id || "site")}`;
  return `<details class="row-menu"><summary class="row-menu-btn" aria-label="${label}" title="More actions">⋯</summary>`
    + `<div class="row-menu-items">${items.join("")}</div></details>`;
}

// One visible action per row (#311): "+ Case" when something is down; the
// rest (Copy, Maint., History) in the menu.
export function actionCell(item) {
  const siteId = escHtml(item.id || "");
  const siteLabel = `<span class="visually-hidden"> for ${escHtml(item.name || item.id || "site")}</span>`;
  if (!downDeviceList(item).length) {
    return `<div class="action-row">${rowMenu(item, [historyButton(item), maintenanceButton(item), maintenanceHistoryLink(item)])}</div>`;
  }
  const caseButton = caseDevices(item).length
    ? `<button class="action-btn case-open-btn" type="button" data-site-id="${siteId}" aria-haspopup="dialog">+ Case${siteLabel}</button>`
    : "";
  // Copy the site and its down devices as text for an ITSM ticket (#227).
  const copyButton = `<button class="action-btn copy-site-btn" type="button" data-site-id="${siteId}" title="Copy the site and its down devices as text">Copy${siteLabel}</button>`;
  return `<div class="action-row">${caseButton}${rowMenu(item, [ackButton(item), copyButton, maintenanceButton(item), historyButton(item), maintenanceHistoryLink(item)])}</div>`;
}

// The site name, linked to the site on the map when it has coordinates;
// plain text when *linked* is false (the wall view, #271).
export function siteNameHtml(item, linked) {
  const name = escHtml(item.name || item.id || "Unknown site");
  const hasCoordinates = Number.isFinite(item.latitude) && Number.isFinite(item.longitude);
  if (linked === false || !hasCoordinates || !item.id) return `<span class="site-name">${name}</span>`;
  return `<a class="site-name" href="/?location_id=${encodeURIComponent(item.id)}" title="Show on the map">${name}</a>`;
}

// Whether someone is on a site (#271): "handled" when every down device has a
// case, "partly" when some do, "none" otherwise (also with no listed devices,
// so an alarm never looks handled by accident).
export function siteCaseState(item) {
  // Devices in maintenance are planned work, not waiting for a case (#283).
  const devices = downDeviceList(item).filter((d) => !d.maintenance_until);
  // A case or an acknowledgement (#288): someone is on it.
  const handled = devices.filter(
    (d) => (Array.isArray(d.case_numbers) && d.case_numbers.length) || d.acknowledged,
  ).length;
  let state = "none";
  if (devices.length && handled === devices.length) state = "handled";
  else if (handled) state = "partly";
  return { state, without: devices.length - handled, total: devices.length };
}

// The wall view's case badge, next to the severity: readable at a glance,
// with text as well as colour.
export function caseStateBadge(item) {
  const { state, without, total } = siteCaseState(item);
  if (state === "handled") {
    const cases = [...new Set(downDeviceList(item).flatMap((d) => d.case_numbers || []))];
    const acked = downDeviceList(item).some((d) => d.acknowledged);
    const label = [cases.join(", "), acked ? "ACK" : ""].filter(Boolean).join(" · ");
    return `<span class="case-state case-state-handled">✓ ${escHtml(label)}</span>`;
  }
  if (state === "partly") return `<span class="case-state case-state-partly">${without} of ${total} no case or ack</span>`;
  return '<span class="case-state case-state-none">NO CASE</span>';
}

// *reason*, e.g. "2/7 devices offline (29%)" (#311): shown on hover and on
// keyboard focus (the shared tooltip), and in the badge's accessible name.
export function alertBadge(level, reason) {
  const value = level || "no_data";
  const label = value === "no_data" ? "NO DATA" : value.toUpperCase();
  const tip = reason
    ? ` tabindex="0" role="img" aria-label="${escHtml(`${label}: ${reason}`)}" data-info-tip="${escHtml(reason)}"`
    : "";
  return `<span class="alert-badge alert-${escHtml(value)}"${tip}>${escHtml(label)}</span>`;
}

// "until 14:00 · core switch upgrade" for a maintenance window (#283).
export function maintenanceNote(until, reason) {
  const when = formatFeedTime(until);
  return [when ? `until ${when}` : "", reason || ""].filter(Boolean).map(escHtml).join(" · ");
}

// A time with what it means (#275): "in alarm 3h 40m" for a site (since its
// first currently-down device went down; one device may be down) and "down
// 25m" for a device.  A bare "Downtime 24h" read as "the site was down".
// *seconds*: null when nothing is open ("—"); NaN when something is open but
// its start time isn't known ("down, time unknown": never a reassuring
// "<1m", Copilot on #280).  A known time under a minute is "<1m" (#279).
export function sinceLabel(what, seconds) {
  if (seconds === null || seconds === undefined) return "—";
  if (Number.isNaN(Number(seconds))) return `${what}, time unknown`;
  return Number(seconds) < 60 ? `${what} <1m` : `${what} ${formatDuration(seconds)}`;
}

// Seconds since the device went down, or NaN when its start isn't known.
export function deviceDowntimeSeconds(device, now = Date.now()) {
  const since = Date.parse(device.down_started_at || "");
  return Number.isNaN(since) ? NaN : Math.max(0, (now - since) / 1000);
}

// The site's time in alarm: null with nothing down; NaN when none of its
// down devices has a known start (e.g. the alert history couldn't be saved).
export function siteAlarmSeconds(item) {
  if (item.alert_level === "maintenance") return null;
  const devices = downDeviceList(item).filter((d) => !d.maintenance_until);
  if (!devices.length) return null;
  const known = devices.some((device) => !Number.isNaN(Date.parse(device.down_started_at || "")));
  return known ? item.current_downtime_seconds || 0 : NaN;
}

// "ACK olga · on my way" under an acknowledged device (#288).
export function ackNote(device) {
  const ack = device.acknowledged;
  if (!ack) return "";
  const text = [ack.by ? `ACK ${ack.by}` : "ACK", ack.note || ""].filter(Boolean).join(" · ");
  return `<div class="ack-note">${escHtml(text)}</div>`;
}

// A device going up and down (#286): one label; the count is in the hover text.
export function flappingBadge(device) {
  if (!device.flapping) return "";
  const changes = Number(device.flap_changes) || 0;
  const times = changes === 1 ? "1 time" : `${changes} times`;
  return ` <span class="flap-badge" title="Up and down ${times} in the last 30 minutes">FLAPPING</span>`;
}

export function renderCases(item) {
  const cases = Array.isArray(item.active_cases) ? item.active_cases : [];
  if (!cases.length) return "—";
  return cases.map((value) => casePill(value)).join("");
}

export function renderDeviceCases(device) {
  const cases = Array.isArray(device.case_numbers) ? device.case_numbers : [];
  if (!cases.length) return "—";
  return cases.map((value) => casePill(value)).join("");
}

export function renderDownDeviceRows(item, isExpanded, extraClass) {
  const downDevices = downDeviceList(item);
  if (!downDevices.length) return "";
  const siteLabel = escHtml(item.name || item.id || "site");
  const hidden = `${isExpanded ? "" : " hidden"}${extraClass ? ` ${extraClass}` : ""}`;
  const now = Date.now();
  const rows = downDevices.map((device) => `
    <tr class="down-device-row${hidden}">
      <td class="down-device-cell" aria-label="Down device for ${siteLabel}">
        <div class="down-device-name"><span class="visually-hidden">Down device for ${siteLabel}: </span>↳ ${escHtml(device.device_name || device.device_id || "Unknown device")}${device.device_ip ? ` <span class="device-ip">${escHtml(device.device_ip)}</span>` : ""}${device.core ? ' <span class="core-badge" title="A core device: the likely cause of the other devices down here">CORE</span>' : ""}${flappingBadge(device)}</div>
        <div class="site-meta">${[device.location_path, device.role, device.flapping ? "" : device.status, device.down_reason].filter(Boolean).map(escHtml).join(" · ") || "Down device"}</div>
        ${ackNote(device)}
        ${device.probable_cause ? `<div class="cause-note">Likely caused by ${escHtml(device.probable_cause)}</div>` : ""}
        ${device.maintenance_until ? `<div class="maintenance-note"><span class="alert-badge alert-maintenance">MAINTENANCE</span> ${maintenanceNote(device.maintenance_until, device.maintenance_reason)}</div>` : ""}
      </td>
      <td></td>
      <td class="since-cell">${sinceLabel("down", deviceDowntimeSeconds(device, now))}</td>
      <td class="cases-cell col-cases">${renderDeviceCases(device)}</td>
      <td class="col-action"></td>
    </tr>
  `).join("");
  return rows;
}

// A tenant name, with an (i) for its Nautobot description when it has one.
export function tenantName(name, descriptions) {
  const description = ownDescription(descriptions, name);
  return description ? `${escHtml(name)}${infoTipHtml(description, `${name}: ${description}`)}` : escHtml(name);
}

// The site's importance (#293): "GOLD" by its name; the most important tier is accented.
export function tierPill(item) {
  if (!item.tier) return "";
  const top = Number(item.tier_rank) === 0 ? " tier-top" : "";
  return ` <span class="tier-pill${top}" title="Importance tier">${escHtml(String(item.tier).toUpperCase())}</span>`;
}

// A site's tenants: the first, and "+N" that shows the rest in place, each
// with its (i) (#238, #279); "—" without any.  Also on the Maintenance page.
export function tenantCell(item) {
  const tenants = siteTenants(item);
  const descriptions = item.tenant_descriptions;
  if (!tenants.length) return "—";
  const rest = tenants.slice(1);
  const more = rest.length
    ? `<span class="tenant-rest" hidden>, ${rest.map((name) => tenantName(name, descriptions)).join(", ")}</span>`
      + ` <button class="tenant-more-btn" type="button" title="${escHtml(rest.join(", "))}">+${rest.length}</button>`
    : "";
  return `${tenantName(tenants[0], descriptions)}${more}`;
}

// The tenants on one line under the site's name (#311, #351), on the board
// and the wall view, where the (i) shows the description too.
export function tenantLine(item) {
  return siteTenants(item).length ? `<div class="site-tenants">${tenantCell(item)}</div>` : "";
}

// Only the path above the site, e.g. "NORAM › GRL" (#178).
export function siteMeta(item) {
  // With ALERT_BOARD_SITE_LOCATION_TYPE the full path above the site (#158).
  return escHtml(item.ancestor_path || item.parent || "");
}

// The board's rows for *alerts* (#294: was renderTableRows' loop).
// *wallView*: sites in maintenance under a heading, handled sites faded,
// plain-text names, a case badge and no "in alarm" time.
// *isExpanded(item)*: whether the site's down devices are shown.
export function siteRowsHtml(alerts, { wallView, isExpanded }) {
  let handledSeen = false;
  let maintenanceSeen = false;
  return alerts.map((item) => {
    const inMaintenance = item.alert_level === "maintenance";
    // Wall view: sites in maintenance are listed last, under a heading (#283).
    const maintenanceHeading = wallView && inMaintenance && !maintenanceSeen
      ? fullWidthRow("In maintenance", "", "maintenance-heading")
      : "";
    if (inMaintenance) maintenanceSeen = true;
    // Wall view: sites someone is on are faded, below a dashed line (#271).
    const handled = wallView && !inMaintenance && siteCaseState(item).state === "handled";
    const firstHandled = handled && !handledSeen;
    if (handled) handledSeen = true;
    const caseClass = handled ? `case-handled${firstHandled ? " handled-first" : ""}` : "";
    const address = formatLocationAddress(item);
    const expanded = isExpanded(item);
    const downCount = item.down_device_count || 0;
    const toggleButton = downDeviceList(item).length
      ? `<button class="site-toggle-btn" type="button" data-site-id="${escHtml(item.id || "")}" aria-expanded="${expanded ? "true" : "false"}" aria-label="${expanded ? "Collapse" : "Expand"} ${escHtml(item.name || item.id || "site")}">${expanded ? "▾" : "▸"}</button>`
      : '<span class="site-toggle-spacer" aria-hidden="true"></span>';
    // Only the path above the site, e.g. "LATAM › BRA" (#178): no status or type.
    const meta = siteMeta(item);

    const maintenanceClass = inMaintenance ? " in-maintenance" : "";
    return `${maintenanceHeading}
    <tr class="site-row${caseClass ? ` ${caseClass}` : ""}${maintenanceClass}">
      <td>
        <div class="site-name-row">
          ${toggleButton}
          <div>
            <div class="site-title">${siteNameHtml(item, !wallView)}${tierPill(item)}</div>
            <div class="site-summary">${downCount} down · ${item.device_count || 0} monitored</div>
            ${tenantLine(item)}
          </div>
        </div>
        ${address ? `<div class="site-address">${escHtml(address)}</div>` : ""}
        ${meta ? `<div class="site-meta">${meta}</div>` : ""}
      </td>
      <td>${alertBadge(item.alert_level, item.alert_reason)}${wallView && !inMaintenance ? caseStateBadge(item) : ""}${item.maintenance ? `<div class="maintenance-note">${maintenanceNote(item.maintenance.until, item.maintenance.reason)}</div>` : ""}</td>
      <td class="since-cell">${wallView ? "" : sinceLabel("in alarm", siteAlarmSeconds(item))}</td>
      <td class="cases-cell col-cases">${renderCases(item)}</td>
      <td class="col-action">${wallView ? "" : actionCell(item)}</td>
    </tr>
    ${wallView && inMaintenance ? "" : renderDownDeviceRows(item, expanded, handled ? "case-handled" : "")}
  `;
  }).join("");
}
