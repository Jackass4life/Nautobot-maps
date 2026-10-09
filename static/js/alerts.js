"use strict";

const boardStatus = document.getElementById("board-status");
const alertsTableBody = document.getElementById("alerts-table-body");
const filterSite = document.getElementById("filter-site");
const filterStatus = document.getElementById("filter-status");
const filterType = document.getElementById("filter-type");
const filterTenant = document.getElementById("filter-tenant");
const sortBy = document.getElementById("sort-by");
const refreshBtn = document.getElementById("refresh-alerts");
// The summary tiles are the severity filter: Alarms (default), one level, or All sites.
const severityFilterButtons = Array.from(document.querySelectorAll("[data-severity-filter]"));
const DEFAULT_SEVERITY_FILTER = "alarms";
const moreFiltersToggle = document.getElementById("more-filters-toggle");
const moreFilters = document.getElementById("more-filters");
const clearAlertFiltersBtn = document.getElementById("clear-alert-filters");
const toggleNonOperational = document.getElementById("toggle-non-operational");
const collapseAllSitesBtn = document.getElementById("collapse-all-sites");
const expandAllSitesBtn = document.getElementById("expand-all-sites");
const themeToggle = document.getElementById("theme-toggle");
const historyPanel = document.getElementById("history-panel");
const historyTitle = document.getElementById("history-title");
const historyContent = document.getElementById("history-content");
const historyCloseBtn = document.getElementById("history-close");
const casePanel = document.getElementById("case-panel");
const caseTitle = document.getElementById("case-title");
const caseContent = document.getElementById("case-content");
const caseCloseBtn = document.getElementById("case-close");
const copyPanel = document.getElementById("copy-panel");
const copyTextArea = document.getElementById("copy-text");
const copyCloseBtn = document.getElementById("copy-close");
const copyStatus = document.getElementById("copy-status");
const maintPanel = document.getElementById("maint-panel");
const maintTitle = document.getElementById("maint-title");
const maintContent = document.getElementById("maint-content");
const maintCloseBtn = document.getElementById("maint-close");
const sidePanels = [historyPanel, casePanel, copyPanel, maintPanel].filter(Boolean);
// "Now, for …" in the maintenance panel (#283), in minutes.
const MAINTENANCE_DURATIONS = [60, 120, 240, 480];
// Settings from the server (templates/alerts.html).
const APP_CONFIG = document.getElementById("app-config")?.dataset || {};
const NAUTOBOT_URL = APP_CONFIG.nautobotUrl || "";
const COPIED_LABEL_MS = 2000;
const boardLayout = document.getElementById("board-layout");
const feedPanel = document.getElementById("feed-panel");
const feedToggle = document.getElementById("feed-toggle");
const feedShowBtn = document.getElementById("feed-show");
const feedList = document.getElementById("feed-list");
const feedFilterButtons = Array.from(document.querySelectorAll("[data-feed-kind]"));
const FEED_LIMIT = 100;
const FEED_COLLAPSED_KEY = "nautobot-maps-feed-collapsed";
// Below this width the open feed leaves the table too little room (it then
// scrolls sideways), so the feed starts collapsed unless the user opened it.
const FEED_OPEN_MIN_WIDTH_PX = 1700;
const nextUpdateEl = document.getElementById("next-update");
// Wall-screen view, /alerts?view=wall (#243): alarms only, every site
// expanded, no controls.  It reloads on its own every minute as well, so a
// screen nobody touches recovers from errors and an unknown next update.
const WALL_VIEW = document.body.classList.contains("wall-view");
const wallUpdatedEl = document.getElementById("wall-updated");
const wallErrorEl = document.getElementById("wall-error");
const WALL_RELOAD_MS = 60000;
// No successful load for this long: say so loudly, the data is old.
const WALL_STALE_MS = 10 * 60000;
let lastLoadedAt = null;
// Board loads can overlap (countdown, sync polling, the wall's minute
// reload); only the newest one may change the page.
let loadSeq = 0;
// The operator's last Sort choice; the default puts the site with the
// newest down device first (#228).
const SORT_KEY = "nautobot-maps-alert-sort";
const DEFAULT_SORT = "newest";
// Levels that are an active alarm; No data is not an alert (#124).
const ALARM_LEVELS = ["critical", "medium", "low"];

let allAlerts = [];
let latestPayload = { checked_at: null, stale: false, summary: {}, alerts: [] };
// Selector of the button that opened the side panel.  Rows are re-rendered
// on every board update, so the button itself may be gone when it closes.
let panelTriggerSelector = null;
let expandedSiteIds = new Set();
let allSitesExpanded = false;
let severityFilter = DEFAULT_SEVERITY_FILTER;

// While the server reports an inventory sync in progress, re-poll the board
// quietly so fresh data appears without the operator clicking Refresh.
const SYNC_POLL_INTERVAL_MS = 5000;
const SYNC_POLL_MAX_ATTEMPTS = 36; // ~3 minutes
let syncPollTimer = null;
let syncPollAttempts = 0;

// Countdown to the next automatic update (#152).  The server sends seconds
// remaining (not a clock time), so a skewed browser clock does not matter.
// When it reaches zero the board reloads in the background, which starts the
// due sync; the sync polling above then shows the new data.
const AUTO_UPDATE_MIN_GAP_MS = 30000;
let nextUpdateDueAt = null;
let lastAutoUpdateAt = 0;

function escHtml(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Most severe first; the server sorts the same way (ALERT_LEVEL_ORDER, #124).
const SEVERITY_ORDER = ["critical", "medium", "low", "no_data", "ok"];

function severityWeight(level) {
  const index = SEVERITY_ORDER.indexOf(level);
  return index === -1 ? SEVERITY_ORDER.length : index;
}

function populateSelect(selectEl, values, label) {
  selectEl.innerHTML = `<option value="">${label}</option>`;
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    selectEl.appendChild(option);
  });
}

function populateFilters(alerts) {
  populateSelect(
    filterStatus,
    [...new Set(alerts.map((item) => item.status).filter(Boolean))].sort(),
    "All statuses"
  );
  populateSelect(
    filterType,
    [...new Set(alerts.map((item) => item.location_type).filter(Boolean))].sort(),
    "All location types"
  );
  populateSelect(
    filterTenant,
    [...new Set(alerts.flatMap(siteTenants))].sort(),
    "All tenants"
  );
}

function renderSummary(summary) {
  const alarms = summary.non_ok ?? ALARM_LEVELS.reduce((sum, level) => sum + (summary[level] || 0), 0);
  document.getElementById("summary-alarms").textContent = alarms;
  document.getElementById("summary-critical").textContent = summary.critical || 0;
  document.getElementById("summary-medium").textContent = summary.medium || 0;
  document.getElementById("summary-low").textContent = summary.low || 0;
  document.getElementById("summary-no_data").textContent = summary.no_data || 0;
  document.getElementById("summary-ok").textContent = summary.ok || 0;
  document.getElementById("summary-maintenance").textContent = summary.maintenance || 0;
  document.getElementById("summary-total").textContent = summary.total || 0;
}

function devicesLabel(count) {
  return `${count} device${count === 1 ? "" : "s"}`;
}

// *alreadyCount*: ticked devices that already have the typed case number
// (#273); saving it there again would change nothing.
function caseButtonLabel(selectedCount, alreadyCount) {
  if (selectedCount === 0) return "Select devices";
  const already = alreadyCount || 0;
  if (already === selectedCount) return `Already on ${devicesLabel(already)}`;
  if (already) return `Add case to ${devicesLabel(selectedCount - already)} (already on ${already})`;
  return `Add case to ${devicesLabel(selectedCount)}`;
}

// A device's open case numbers, as the panel stores them (newline-separated).
function deviceCaseList(box) {
  return (box.dataset.cases || "").split("\n").filter(Boolean);
}

// Whether *box*'s device already has *caseNumber*, ignoring case: "inc-881"
// next to "INC-881" would be a second ticket by mistake.
function deviceHasCase(box, caseNumber) {
  const wanted = (caseNumber || "").trim().toLowerCase();
  return Boolean(wanted) && deviceCaseList(box).some((value) => value.toLowerCase() === wanted);
}

// The ticked devices that get *caseNumber*: those that don't have it yet.
// The button label and the request both use this, so they always agree.
function caseTargetIds(form, caseNumber) {
  return Array.from(form.querySelectorAll(".case-device-checkbox"))
    .filter((box) => box.checked && !deviceHasCase(box, caseNumber))
    .map((box) => box.value);
}

function syncCaseSelection(form) {
  const boxes = Array.from(form.querySelectorAll(".case-device-checkbox"));
  const ticked = boxes.filter((box) => box.checked);
  const selected = ticked.length;
  const selectAll = form.querySelector(".case-select-all");
  if (selectAll) {
    selectAll.checked = selected === boxes.length;
    selectAll.indeterminate = selected > 0 && selected < boxes.length;
  }
  const already = selected - caseTargetIds(form, form.querySelector(".case-input")?.value).length;
  const button = form.querySelector(".case-save-btn");
  button.textContent = caseButtonLabel(selected, already);
  button.disabled = selected === 0 || already === selected;
}

// The site's expandable device list: down now, or with an alert still open.
// It can be non-empty while down_device_count is 0 (a failed observation
// keeps open alerts), so the toggle, row buttons and History all use this.
function downDeviceList(item) {
  return Array.isArray(item.down_devices) ? item.down_devices : [];
}

function caseDevices(item) {
  return downDeviceList(item).filter((d) => d.device_id);
}

// The row keeps the two triage actions (#179, #227); the site name links to
// the map, and History sits under the down devices (in the row when there
// are none to expand).
function historyButton(item) {
  const siteId = escHtml(item.id || "");
  const siteLabel = `<span class="visually-hidden"> for ${escHtml(item.name || item.id || "site")}</span>`;
  return `<button class="action-btn history-btn" type="button" data-site-id="${siteId}" aria-haspopup="dialog">History${siteLabel}</button>`;
}

// Opens the maintenance panel (#283); on every row, also a site in maintenance (to end it).
function maintenanceButton(item) {
  // Nothing to put in maintenance without monitored devices (still shown while a window is on).
  if (!item.device_count && !item.maintenance_device_count && !item.maintenance) return "";
  const siteLabel = `<span class="visually-hidden"> for ${escHtml(item.name || item.id || "site")}</span>`;
  return `<button class="action-btn maint-open-btn" type="button" data-site-id="${escHtml(item.id || "")}" aria-haspopup="dialog" title="Maintenance window">Maint.${siteLabel}</button>`;
}

function actionCell(item) {
  const siteId = escHtml(item.id || "");
  const siteLabel = `<span class="visually-hidden"> for ${escHtml(item.name || item.id || "site")}</span>`;
  if (!downDeviceList(item).length) return `<div class="action-row">${historyButton(item)}${maintenanceButton(item)}</div>`;
  const caseButton = caseDevices(item).length
    ? `<button class="action-btn case-open-btn" type="button" data-site-id="${siteId}" aria-haspopup="dialog">+ Case${siteLabel}</button>`
    : "";
  // Copy the site and its down devices as text for an ITSM ticket (#227).
  const copyButton = `<button class="action-btn copy-site-btn" type="button" data-site-id="${siteId}" title="Copy the site and its down devices as text">Copy${siteLabel}</button>`;
  return `
    <div class="action-row">
      ${caseButton}
      ${copyButton}
      ${maintenanceButton(item)}
    </div>
  `;
}

// One window in the panel: what, when, why, and End now / Cancel.
function maintenanceWindowItem(window, deviceNames) {
  const what = window.device_id ? (deviceNames[window.device_id] || window.device_id) : "Whole site";
  const when = window.state === "upcoming"
    ? `${formatFeedTime(window.starts_at)} – ${formatFeedTime(window.ends_at)}`
    : `until ${formatFeedTime(window.ends_at)}`;
  const action = window.state === "upcoming" ? "Cancel" : "End now";
  const by = window.created_by ? ` · by ${escHtml(window.created_by)}` : "";
  return `
    <li class="maint-window">
      <span class="alert-badge alert-maintenance">${window.state === "upcoming" ? "PLANNED" : "ACTIVE"}</span>
      <span class="maint-window-text"><strong>${escHtml(what)}</strong> · ${escHtml(when)}<br />${escHtml(window.reason || "")}${by}</span>
      <button class="action-btn maint-end-btn" type="button" data-window-id="${escHtml(String(window.id))}">${action}</button>
    </li>`;
}

// The panel: the site's current and planned windows, and a form for a new one.
function renderMaintenancePanel(item, windows, devices) {
  const deviceNames = Object.fromEntries(devices.map((device) => [device.id, device.name]));
  const list = windows.length
    ? `<ul class="maint-windows">${windows.map((window) => maintenanceWindowItem(window, deviceNames)).join("")}</ul>`
    : '<p class="maint-empty">No maintenance now or planned.</p>';
  const deviceChoices = devices.length
    ? devices.map((device) => `
        <label class="case-device-option">
          <input type="checkbox" class="maint-device" value="${escHtml(device.id)}" />
          <span>${escHtml(device.name || device.id)}</span>
          <span class="maint-device-meta">${[device.location_path, device.role, device.status].filter(Boolean).map(escHtml).join(" · ")}</span>
        </label>`).join("")
    : '<p class="maint-empty">No monitored devices.</p>';
  const durations = MAINTENANCE_DURATIONS
    .map((minutes) => `<option value="${minutes}"${minutes === 120 ? " selected" : ""}>${minutes / 60} hour${minutes === 60 ? "" : "s"}</option>`)
    .join("");
  return `
    ${list}
    <form class="maint-form" data-site-id="${escHtml(item.id || "")}">
      <fieldset class="maint-fieldset">
        <legend>What</legend>
        <label class="maint-choice"><input type="radio" name="maint-scope" value="site" checked /> Whole site</label>
        <label class="maint-choice"><input type="radio" name="maint-scope" value="devices" /> Some devices</label>
        <div class="maint-devices case-device-list" hidden>${deviceChoices}</div>
      </fieldset>
      <fieldset class="maint-fieldset">
        <legend>When</legend>
        <label class="maint-choice"><input type="radio" name="maint-when" value="now" checked /> Now, for
          <select class="maint-duration" aria-label="How long">${durations}</select></label>
        <label class="maint-choice"><input type="radio" name="maint-when" value="planned" /> Planned</label>
        <div class="maint-planned" hidden>
          <label>From <input type="datetime-local" class="maint-start" /></label>
          <label>To <input type="datetime-local" class="maint-end" /></label>
        </div>
      </fieldset>
      <label class="case-input-label" for="maint-reason">Reason</label>
      <input id="maint-reason" class="case-input maint-reason" type="text" maxlength="200" autocomplete="off" placeholder="e.g. Core switch upgrade" />
      <p class="maint-error" role="alert" hidden></p>
      <button class="case-save-btn maint-save-btn" type="submit">Start maintenance</button>
    </form>`;
}

// The request for a new window from the form's values, or {error}.
// datetime-local values are the browser's local time; sent as UTC.
function maintenanceRequestBody(values) {
  const reason = (values.reason || "").trim();
  if (!reason) return { error: "Enter a reason." };
  const body = { site_id: values.siteId, reason };
  if (values.scope === "devices") {
    if (!values.deviceIds.length) return { error: "Tick at least one device, or choose Whole site." };
    body.device_ids = values.deviceIds;
  }
  if (values.when === "planned") {
    const start = new Date(values.start || "");
    const end = new Date(values.end || "");
    if (Number.isNaN(start.valueOf()) || Number.isNaN(end.valueOf())) return { error: "Enter both From and To." };
    if (end <= start) return { error: "To must be after From." };
    body.starts_at = start.toISOString();
    body.ends_at = end.toISOString();
  } else {
    body.duration_minutes = Number(values.duration);
  }
  return body;
}

function readMaintenanceForm(form) {
  return {
    siteId: form.dataset.siteId,
    scope: form.querySelector('input[name="maint-scope"]:checked')?.value,
    deviceIds: Array.from(form.querySelectorAll(".maint-device:checked")).map((box) => box.value),
    when: form.querySelector('input[name="maint-when"]:checked')?.value,
    duration: form.querySelector(".maint-duration")?.value,
    start: form.querySelector(".maint-start")?.value,
    end: form.querySelector(".maint-end")?.value,
    reason: form.querySelector(".maint-reason")?.value,
  };
}

async function openMaintenancePanel(siteId) {
  const item = allAlerts.find((alert) => String(alert.id) === siteId);
  if (!maintPanel || !item) return;
  maintTitle.textContent = `Maintenance · ${item.name || siteId}`;
  maintContent.innerHTML = '<p class="maint-empty">Loading…</p>';
  openSidePanel(maintPanel, `.maint-open-btn[data-site-id="${CSS.escape(siteId)}"]`, maintCloseBtn);
  await refreshMaintenancePanel(item);
}

// Bumped by every panel load: a slower answer for a site opened before must
// not fill the panel of the one open now.
let maintenanceRequestSeq = 0;

async function refreshMaintenancePanel(item) {
  const seq = ++maintenanceRequestSeq;
  const params = new URLSearchParams({ site_id: item.id });
  const devicesParams = new URLSearchParams(params);
  if (toggleNonOperational?.checked) devicesParams.set("include_non_operational", "1");
  try {
    const [windowsResp, devicesResp] = await Promise.all([
      fetch(`/api/maintenance?${params}`, { cache: "no-store" }),
      fetch(`/api/maintenance/devices?${devicesParams}`, { cache: "no-store" }),
    ]);
    const windows = await readJsonResponse(windowsResp);
    const devices = await readJsonResponse(devicesResp);
    if (seq !== maintenanceRequestSeq) return;
    if (!windowsResp.ok || windows.error) throw new Error(windows.error || `HTTP ${windowsResp.status}`);
    if (!devicesResp.ok || devices.error) throw new Error(devices.error || `HTTP ${devicesResp.status}`);
    maintContent.innerHTML = renderMaintenancePanel(item, windows.windows || [], devices.devices || []);
  } catch (err) {
    if (seq !== maintenanceRequestSeq) return;
    maintContent.innerHTML = `<p class="maint-error">Could not load maintenance: ${escHtml(err.message)}</p>`;
  }
}

function showMaintenanceError(form, message) {
  const box = form.querySelector(".maint-error");
  box.textContent = message;
  box.hidden = !message;
}

// The site name, linked to the site on the map when it has coordinates;
// plain text when *linked* is false (the wall view, #271).
function siteNameHtml(item, linked) {
  const name = escHtml(item.name || item.id || "Unknown site");
  const hasCoordinates = Number.isFinite(item.latitude) && Number.isFinite(item.longitude);
  if (linked === false || !hasCoordinates || !item.id) return `<span class="site-name">${name}</span>`;
  return `<a class="site-name" href="/?location_id=${encodeURIComponent(item.id)}" title="Show on the map">${name}</a>`;
}

// Whether someone is on a site (#271): "handled" when every down device has a
// case, "partly" when some do, "none" otherwise (also with no listed devices,
// so an alarm never looks handled by accident).
function siteCaseState(item) {
  // Devices in maintenance are planned work, not waiting for a case (#283).
  const devices = downDeviceList(item).filter((d) => !d.maintenance_until);
  const withCase = devices.filter((d) => Array.isArray(d.case_numbers) && d.case_numbers.length).length;
  let state = "none";
  if (devices.length && withCase === devices.length) state = "handled";
  else if (withCase) state = "partly";
  return { state, without: devices.length - withCase, total: devices.length };
}

// The wall view's case badge, next to the severity: readable at a glance,
// with text as well as colour.
function caseStateBadge(item) {
  const { state, without, total } = siteCaseState(item);
  if (state === "handled") {
    const cases = [...new Set(downDeviceList(item).flatMap((d) => d.case_numbers))];
    return `<span class="case-state case-state-handled">✓ ${escHtml(cases.join(", "))}</span>`;
  }
  if (state === "partly") return `<span class="case-state case-state-partly">${without} of ${total} no case</span>`;
  return '<span class="case-state case-state-none">NO CASE</span>';
}

function renderCaseForm(item) {
  const devices = caseDevices(item);
  const casesOf = (d) => (Array.isArray(d.case_numbers) ? d.case_numbers : []);
  // One case often covers several devices (e.g. a whole site down), so every
  // down device gets a checkbox.  Devices that already have a case start
  // unticked and show it, so a second ticket isn't added by mistake (#273).
  const deviceChoices = devices.map((d) => {
    const cases = casesOf(d);
    const pills = cases.map((value) => `<span class="case-pill">${escHtml(value)}</span>`).join("");
    return `
        <label class="case-device-option">
          <input type="checkbox" class="case-device-checkbox" value="${escHtml(d.device_id)}" data-cases="${escHtml(cases.join("\n"))}"${cases.length ? "" : " checked"} />
          <span>${escHtml(d.device_name || d.device_id)}</span>${pills ? `<span class="case-device-cases">${pills}</span>` : ""}
        </label>`;
  }).join("");
  const ticked = devices.filter((d) => !casesOf(d).length).length;
  const siteCases = [...new Set(devices.flatMap(casesOf))];
  const existing = siteCases.length ? `
      <p class="case-existing">Open cases on this site: ${siteCases.map((value) => `<span class="case-pill">${escHtml(value)}</span>`).join(" ")}</p>` : "";
  const allHaveOne = devices.length > 0 && ticked === 0;
  const note = allHaveOne ? `
      <p class="case-note">Every down device already has a case. Ticking a device adds a second case next to it; it doesn't replace it.</p>` : "";
  const selectAll = devices.length > 1 ? `
        <label class="case-device-option case-select-all-option">
          <input type="checkbox" class="case-select-all"${ticked === devices.length ? " checked" : ""} />
          <span>All down devices (${devices.length})</span>
        </label>` : "";
  return `
    <form class="case-form" data-site-id="${escHtml(item.id || "")}">${existing}${note}
      <fieldset class="case-devices">
        <legend>Devices for this case</legend>
        ${selectAll}
        <div class="case-device-list">${deviceChoices}</div>
      </fieldset>
      <label class="case-input-label" for="case-input">Case number</label>
      <input id="case-input" class="case-input" type="text" autocomplete="off" placeholder="Case #" />
      <button class="case-save-btn" type="submit"${ticked ? "" : " disabled"}>${caseButtonLabel(ticked)}</button>
    </form>
  `;
}

function hideSidePanel(panel) {
  panel.classList.add("hidden");
  panel.setAttribute("aria-hidden", "true");
}

function openSidePanel(panel, triggerSelector, focusTarget) {
  sidePanels.forEach((other) => {
    if (other !== panel) hideSidePanel(other);
  });
  panel.classList.remove("hidden");
  panel.setAttribute("aria-hidden", "false");
  panelTriggerSelector = triggerSelector;
  (focusTarget || panel).focus();
}

function restorePanelFocus() {
  const trigger = panelTriggerSelector && document.querySelector(panelTriggerSelector);
  panelTriggerSelector = null;
  if (trigger) trigger.focus();
}

function closeSidePanel(panel) {
  if (!panel || panel.classList.contains("hidden")) return;
  hideSidePanel(panel);
  restorePanelFocus();
}

function openCasePanel(siteId) {
  const item = allAlerts.find((alert) => String(alert.id) === siteId);
  if (!casePanel || !item) return;
  caseTitle.textContent = `Add case · ${item.name || siteId}`;
  caseContent.innerHTML = renderCaseForm(item);
  // Part-ticked select-all can only be set from script.
  const form = caseContent.querySelector(".case-form");
  if (form) syncCaseSelection(form);
  openSidePanel(casePanel, `.case-open-btn[data-site-id="${CSS.escape(siteId)}"]`, caseContent.querySelector(".case-input"));
}

// "2026-09-29 13:05 +02:00": local time with its offset, so a ticket read
// in another time zone is still right (#227).
function formatReportTime(value) {
  const parsed = new Date(value || "");
  if (!value || Number.isNaN(parsed.valueOf())) return "";
  const pad = (number) => String(number).padStart(2, "0");
  const offset = -parsed.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const zone = `${sign}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`;
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} `
    + `${pad(parsed.getHours())}:${pad(parsed.getMinutes())} ${zone}`;
}

// A site and its down devices as plain text, for pasting into an ITSM
// ticket (#227): one line per device, newest down first.
function siteReport(item, nautobotUrl = "") {
  const level = item.alert_level || "no_data";
  const lines = [`Site: ${item.name || item.id || "Unknown site"}`];
  const path = item.ancestor_path || item.parent;
  if (path) lines.push(`Location: ${path}`);
  const address = formatLocationAddress(item);
  if (address) lines.push(`Address: ${address}`);
  const tenants = siteTenants(item);
  if (tenants.length > 1) lines.push(`Tenants (${tenants.length}): ${tenants.join(", ")}`);
  else if (tenants.length) lines.push(`Tenant: ${tenants[0]}`);
  lines.push(`Severity: ${level === "no_data" ? "NO DATA" : level.toUpperCase()} (${item.down_device_count || 0} of ${item.device_count || 0} devices down)`);
  if (item.alert_reason) lines.push(`Reason: ${item.alert_reason}`);
  if (Array.isArray(item.active_cases) && item.active_cases.length) lines.push(`Cases: ${item.active_cases.join(", ")}`);
  if (nautobotUrl && item.id) lines.push(`Nautobot: ${nautobotUrl.replace(/\/+$/, "")}/dcim/locations/${encodeURIComponent(item.id)}/`);

  const devices = (Array.isArray(item.down_devices) ? [...item.down_devices] : [])
    .sort((a, b) => (Date.parse(b.down_started_at || "") || 0) - (Date.parse(a.down_started_at || "") || 0));
  lines.push("", `Down devices (${devices.length}):`);
  devices.forEach((device) => {
    const since = formatReportTime(device.down_started_at);
    const cases = Array.isArray(device.case_numbers) ? device.case_numbers : [];
    const fields = [
      device.device_name || device.device_id || "Unknown device",
      `IP ${device.device_ip || "—"}`,
      `Role ${device.role || "—"}`,
      `Status ${device.status || "—"}`,
      device.location_path ? `Location ${device.location_path}` : "",
      since ? `Down since ${since}` : "",
      cases.length ? `Case ${cases.join(", ")}` : "",
    ];
    lines.push(`- ${fields.filter(Boolean).join(" | ")}`);
  });
  return lines.join("\n");
}

// The clipboard API needs HTTPS (or localhost); plain-HTTP pages fall back to
// the older copy command.  Returns whether the text was copied.
async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_err) {
      // Denied: try the fallback.
    }
  }
  const buffer = document.createElement("textarea");
  buffer.className = "clipboard-buffer";
  buffer.value = text;
  buffer.setAttribute("readonly", "");
  document.body.appendChild(buffer);
  buffer.select();
  let copied;
  try {
    copied = document.execCommand("copy");
  } catch (_err) {
    copied = false;
  }
  buffer.remove();
  return copied;
}

async function copySite(siteId, button) {
  const item = allAlerts.find((alert) => String(alert.id) === siteId);
  if (!item) return;
  const text = siteReport(item, NAUTOBOT_URL);
  if (await copyText(text)) {
    if (copyStatus) copyStatus.textContent = `Copied ${item.name || siteId} to the clipboard`;
    if (button?.isConnected) {
      button.classList.add("copied");
      button.firstChild.textContent = "Copied";
      setTimeout(() => {
        button.classList.remove("copied");
        button.firstChild.textContent = "Copy";
      }, COPIED_LABEL_MS);
    }
    return;
  }
  // Not allowed: show the text, selected, to copy by hand.
  if (!copyPanel || !copyTextArea) {
    showError("Could not copy to the clipboard.");
    return;
  }
  copyTextArea.value = text;
  openSidePanel(copyPanel, `.copy-site-btn[data-site-id="${CSS.escape(siteId)}"]`, copyTextArea);
  copyTextArea.select();
}

function formatDuration(seconds) {
  const total = Number(seconds || 0);
  if (!Number.isFinite(total) || total <= 0) return "—";
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatTimestamp(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return escHtml(value);
  return escHtml(parsed.toLocaleString());
}

// The site's history as a CSV download (#277).
function historyExportUrl(siteId, view, days) {
  return `/api/alert-history.csv?${new URLSearchParams({ site_id: siteId, days, view })}`;
}

const HISTORY_EXPORT_DEFAULT_DAYS = "30";

function historyExportBar(siteId) {
  const link = (view, label) =>
    `<a class="action-btn history-export-link" data-view="${view}" href="${escHtml(historyExportUrl(siteId, view, HISTORY_EXPORT_DEFAULT_DAYS))}" download>${label}</a>`;
  return `
    <div class="history-export" data-site-id="${escHtml(siteId)}">
      <label class="history-export-label">Export
        <select class="history-export-days" aria-label="Export period">
          <option value="7">Last 7 days</option>
          <option value="30" selected>Last 30 days</option>
          <option value="90">Last 90 days</option>
          <option value="all">All</option>
        </select>
      </label>
      ${link("incidents", "Incidents CSV")}
      ${link("devices", "Per-device CSV")}
    </div>`;
}

function renderAlertHistory(siteId, instances) {
  if (!historyPanel || !historyTitle || !historyContent) return;
  const site = allAlerts.find((alert) => String(alert.id) === siteId);
  historyTitle.textContent = `History · ${site?.name || siteId}`;
  const exportBar = historyExportBar(siteId);
  if (!instances.length) {
    historyContent.innerHTML = `${exportBar}<div class="history-instance"><div class="history-line">No incidents found.</div></div>`;
  } else {
    historyContent.innerHTML = exportBar + instances.map((instance) => {
      const cases = Array.isArray(instance.cases) ? instance.cases.map((entry) => escHtml(entry.case_number || "")).filter(Boolean) : [];
      const events = Array.isArray(instance.events)
        ? instance.events.map((event) => `${escHtml(event.event_type || "")} @ ${formatTimestamp(event.event_at)}`).join(", ")
        : "";
      return `
        <article class="history-instance">
          <div><strong>${escHtml(instance.device_name || instance.device_id || "Unknown device")}</strong> · ${escHtml(instance.status || "unknown")} · ${escHtml(instance.alert_level || "unknown")}</div>
          <div class="history-line">Downtime: ${formatDuration(instance.total_downtime_seconds || 0)}</div>
          <div class="history-line">Opened: ${formatTimestamp(instance.down_started_at)} · Resolved: ${formatTimestamp(instance.resolved_at)}</div>
          <div class="history-line">Cases: ${cases.length ? cases.join(", ") : "—"}</div>
          <div class="history-line">Events: ${events || "—"}</div>
        </article>
      `;
    }).join("");
  }
}

async function readJsonResponse(resp) {
  const contentType = resp.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return resp.json();
  }
  const text = await resp.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch (_err) {
    return { error: text };
  }
}

function renderCases(item) {
  const cases = Array.isArray(item.active_cases) ? item.active_cases : [];
  if (!cases.length) return "—";
  return cases.map((value) => `<span class="case-pill">${escHtml(value)}</span>`).join("");
}

function renderDeviceCases(device) {
  const cases = Array.isArray(device.case_numbers) ? device.case_numbers : [];
  if (!cases.length) return "—";
  return cases.map((value) => `<span class="case-pill">${escHtml(value)}</span>`).join("");
}

// Today: the time only; otherwise the date too.
function formatFeedTime(value, now = new Date()) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return "";
  const time = parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (parsed.toDateString() === now.toDateString()) return time;
  return `${parsed.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}

function renderFeedEvent(event, now = new Date()) {
  const at = `<time datetime="${escHtml(event.at)}">${escHtml(formatFeedTime(event.at, now))}</time>`;
  const site = escHtml(event.site_name || event.site_id || "Unknown site");
  const device = escHtml(event.device_name || event.device_id || "Unknown device");
  if (event.kind === "severity") {
    return `
      <li class="feed-entry feed-severity">
        <span class="feed-icon" aria-hidden="true">●</span>
        <span><strong>${site}</strong><span class="visually-hidden"> severity changed</span></span>
        <span class="feed-meta">${alertBadge(event.from_level)} → ${alertBadge(event.to_level)} · ${at}</span>
      </li>`;
  }
  if (event.kind === "up") {
    return `
      <li class="feed-entry feed-up">
        <span class="feed-icon" aria-hidden="true">▲</span>
        <span><strong>${device}</strong> back up</span>
        <span class="feed-meta">${at} · ${site}</span>
      </li>`;
  }
  // Down: also when it went down, if that was before it was noticed (#166).
  const since = new Date(event.down_since);
  const noticed = new Date(event.at);
  const earlier = !Number.isNaN(since.valueOf()) && noticed - since >= 60000
    ? ` · down since ${escHtml(formatFeedTime(event.down_since, now))}`
    : "";
  return `
    <li class="feed-entry feed-down">
      <span class="feed-icon" aria-hidden="true">▼</span>
      <span><strong>${device}</strong> down</span>
      <span class="feed-meta">${at} · ${site}${earlier}</span>
    </li>`;
}

function renderFeed(payload, kinds) {
  if (!kinds.length) return '<li class="feed-empty">Choose what to show.</li>';
  if (payload.persistence_configured === false) {
    return '<li class="feed-empty">The activity log needs the PostgreSQL database.</li>';
  }
  const events = Array.isArray(payload.events) ? payload.events : [];
  if (!events.length) return '<li class="feed-empty">No changes yet.</li>';
  const now = new Date();
  return events.map((event) => renderFeedEvent(event, now)).join("");
}

function selectedFeedKinds() {
  return feedFilterButtons
    .filter((button) => button.getAttribute("aria-pressed") === "true")
    .map((button) => button.dataset.feedKind);
}

async function loadFeed() {
  // Hidden: nothing to show; it loads again when shown.
  if (!feedList || feedPanel?.hidden) return;
  const kinds = selectedFeedKinds();
  if (!kinds.length) {
    feedList.innerHTML = renderFeed({}, kinds);
    return;
  }
  try {
    const params = new URLSearchParams({ limit: String(FEED_LIMIT), kinds: kinds.join(",") });
    const resp = await fetch(`/api/alert-feed?${params}`, { cache: "no-store" });
    const payload = await readJsonResponse(resp);
    if (!resp.ok || payload.error) throw new Error(payload.error || `HTTP ${resp.status}`);
    feedList.innerHTML = renderFeed(payload, kinds);
  } catch (err) {
    // The board refreshes often; no toast for every failed feed load.
    feedList.innerHTML = `<li class="feed-empty">Could not load activity: ${escHtml(err.message)}</li>`;
  }
}

// The user's last Hide/Show choice; without one, whether there is room.
function feedStartsCollapsed(stored, viewportWidth) {
  if (stored === "1" || stored === "0") return stored === "1";
  return viewportWidth < FEED_OPEN_MIN_WIDTH_PX;
}

function setFeedCollapsed(collapsed) {
  if (!boardLayout || !feedPanel || !feedShowBtn) return;
  boardLayout.classList.toggle("feed-collapsed", collapsed);
  feedPanel.hidden = collapsed;
  feedShowBtn.hidden = !collapsed;
}

function rememberFeedCollapsed(collapsed) {
  try {
    localStorage.setItem(FEED_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch (_err) {
    // noop
  }
}

function hasActiveAlarm(item) {
  return ALARM_LEVELS.includes(item.alert_level);
}

// Whether a site passes the severity tiles: Alarms, one level, or all.
function matchesSeverityFilter(item, filter = severityFilter) {
  if (filter === "all") return true;
  if (filter === "alarms") return hasActiveAlarm(item);
  return (item.alert_level || "no_data") === filter;
}

function setSeverityFilter(value) {
  severityFilter = severityFilterButtons.some((button) => button.dataset.severityFilter === value)
    ? value
    : DEFAULT_SEVERITY_FILTER;
  severityFilterButtons.forEach((button) => {
    button.setAttribute("aria-pressed", button.dataset.severityFilter === severityFilter ? "true" : "false");
  });
}

function formatBoardStatus(payload, visibleCount, alarmsOnly = severityFilter === "alarms") {
  const checkedAt = payload.checked_at ? new Date(payload.checked_at) : null;
  const timestamp = checkedAt && !Number.isNaN(checkedAt.valueOf())
    ? checkedAt.toLocaleString()
    : "unknown";
  const staleText = payload.stale ? "stale" : "fresh";
  const syncText = payload.sync_pending ? " · inventory sync in progress…" : "";
  const scopeText = alarmsOnly ? " (alarms only)" : "";
  boardStatus.textContent = `${visibleCount} site${visibleCount !== 1 ? "s" : ""} shown${scopeText} · last checked ${timestamp} · ${staleText}${syncText}`;
}

function alertBadge(level) {
  const value = level || "no_data";
  const label = value === "no_data" ? "NO DATA" : value.toUpperCase();
  return `<span class="alert-badge alert-${escHtml(value)}">${escHtml(label)}</span>`;
}

// "until 14:00 · core switch upgrade" for a maintenance window (#283).
function maintenanceNote(until, reason) {
  const when = formatFeedTime(until);
  return [when ? `until ${when}` : "", reason || ""].filter(Boolean).map(escHtml).join(" · ");
}

function formatLocationAddress(item) {
  return item.physical_address || item.facility || "";
}

function compareText(left, right) {
  return (left || "").localeCompare(right || "");
}

// Milliseconds of the site's newest open alert start, or null without one.
function latestDownTime(item) {
  const parsed = Date.parse(item.latest_down_at || "");
  return Number.isNaN(parsed) ? null : parsed;
}

function compareSites(a, b, sort) {
  if (sort === "site") return compareText(a.name, b.name);
  if (sort === "newest") {
    // Newest down first (#228); sites with nothing down follow, by severity.
    const left = latestDownTime(a);
    const right = latestDownTime(b);
    if (left !== right) {
      if (left === null) return 1;
      if (right === null) return -1;
      return right - left;
    }
  }
  return severityWeight(a.alert_level) - severityWeight(b.alert_level)
    || (b.down_device_count || 0) - (a.down_device_count || 0)
    || compareText(a.name, b.name);
}

function restoreSort() {
  let stored = null;
  try {
    stored = localStorage.getItem(SORT_KEY);
  } catch (_err) {
    // Storage unavailable: keep the default.
  }
  sortBy.value = Array.from(sortBy.options).some((option) => option.value === stored) ? stored : DEFAULT_SORT;
}

function rememberSort(value) {
  try {
    if (value === DEFAULT_SORT) localStorage.removeItem(SORT_KEY);
    else localStorage.setItem(SORT_KEY, value);
  } catch (_err) {
    // noop
  }
}

function isSiteExpanded(item) {
  const siteId = String(item.id || "");
  return allSitesExpanded ? !expandedSiteIds.has(siteId) : expandedSiteIds.has(siteId);
}

// A time with what it means (#275): "in alarm 3h 40m" for a site (since its
// first currently-down device went down; one device may be down) and "down
// 25m" for a device.  A bare "Downtime 24h" read as "the site was down".
// *seconds*: null when nothing is open ("—"); NaN when something is open but
// its start time isn't known ("down, time unknown": never a reassuring
// "<1m", Copilot on #280).  A known time under a minute is "<1m" (#279).
function sinceLabel(what, seconds) {
  if (seconds === null || seconds === undefined) return "—";
  if (Number.isNaN(Number(seconds))) return `${what}, time unknown`;
  return Number(seconds) < 60 ? `${what} <1m` : `${what} ${formatDuration(seconds)}`;
}

// Seconds since the device went down, or NaN when its start isn't known.
function deviceDowntimeSeconds(device, now = Date.now()) {
  const since = Date.parse(device.down_started_at || "");
  return Number.isNaN(since) ? NaN : Math.max(0, (now - since) / 1000);
}

// The site's time in alarm: null with nothing down; NaN when none of its
// down devices has a known start (e.g. the alert history couldn't be saved).
function siteAlarmSeconds(item) {
  if (item.alert_level === "maintenance") return null;
  const devices = downDeviceList(item).filter((d) => !d.maintenance_until);
  if (!devices.length) return null;
  const known = devices.some((device) => !Number.isNaN(Date.parse(device.down_started_at || "")));
  return known ? item.current_downtime_seconds || 0 : NaN;
}

function renderDownDeviceRows(item, isExpanded, extraClass) {
  const downDevices = downDeviceList(item);
  if (!downDevices.length) return "";
  const siteLabel = escHtml(item.name || item.id || "site");
  const hidden = `${isExpanded ? "" : " hidden"}${extraClass ? ` ${extraClass}` : ""}`;
  const now = Date.now();
  const rows = downDevices.map((device) => `
    <tr class="down-device-row${hidden}">
      <td class="down-device-cell" aria-label="Down device for ${siteLabel}">
        <div class="down-device-name"><span class="visually-hidden">Down device for ${siteLabel}: </span>↳ ${escHtml(device.device_name || device.device_id || "Unknown device")}${device.device_ip ? ` <span class="device-ip">${escHtml(device.device_ip)}</span>` : ""}</div>
        <div class="site-meta">${[device.location_path, device.role, device.status].filter(Boolean).map(escHtml).join(" · ") || "Down device"}</div>
        ${device.maintenance_until ? `<div class="maintenance-note"><span class="alert-badge alert-maintenance">MAINTENANCE</span> ${maintenanceNote(device.maintenance_until, device.maintenance_reason)}</div>` : ""}
      </td>
      <td></td>
      <td class="col-tenants"></td>
      <td class="since-cell">${sinceLabel("down", deviceDowntimeSeconds(device, now))}</td>
      <td class="cases-cell col-cases">${renderDeviceCases(device)}</td>
      <td class="col-reason"></td>
      <td class="col-action"></td>
    </tr>
  `).join("");
  // The site's history, under its devices.
  return `${rows}
    <tr class="down-device-row site-tools-row${hidden}">
      <td class="site-tools-cell" colspan="7">${historyButton(item)}</td>
    </tr>
  `;
}

// Every tenant of the site: its own, then those linked by a Nautobot
// Relationship (#238).  Boards cached before #238 only have `tenant`.
function siteTenants(item) {
  if (Array.isArray(item.tenants) && item.tenants.length) return item.tenants;
  return item.tenant ? [item.tenant] : [];
}

// An (i) that shows *text* on hover or focus (js/tooltip.js, #263).
function infoTipHtml(text, label) {
  return `<span class="info-tip" tabindex="0" role="img" aria-label="${escHtml(label)}" data-info-tip="${escHtml(text)}">i</span>`;
}

// Only the map's own keys: a tenant named "constructor" or "toString" must not
// pick up an inherited object property.
function ownDescription(descriptions, name) {
  return descriptions && Object.hasOwn(descriptions, name) ? descriptions[name] : "";
}

// A tenant name, with an (i) for its Nautobot description when it has one.
function tenantName(name, descriptions) {
  const description = ownDescription(descriptions, name);
  return description ? `${escHtml(name)}${infoTipHtml(description, `${name}: ${description}`)}` : escHtml(name);
}

const TENANTS_SHOWN = 3;

// One tenant as text; several get a "N tenants" badge so a multi-customer
// site stands out when it alarms (#238).
function tenantCell(item) {
  const tenants = siteTenants(item);
  const descriptions = item.tenant_descriptions;
  if (!tenants.length) return "—";
  if (tenants.length === 1) return tenantName(tenants[0], descriptions);
  const shown = tenants.slice(0, TENANTS_SHOWN).map((name) => tenantName(name, descriptions)).join(", ");
  // The rest are in the row, hidden until "+N more" shows them in place,
  // each with its (i) (#279).
  const rest = tenants.slice(TENANTS_SHOWN);
  const more = rest.length
    ? `<span class="tenant-rest" hidden>, ${rest.map((name) => tenantName(name, descriptions)).join(", ")}</span>`
      + ` <button class="tenant-more-btn" type="button">+${rest.length} more</button>`
    : "";
  return `<span class="multi-tenant-badge" title="${escHtml(tenants.join(", "))}">${tenants.length} tenants</span>`
    + `<div class="tenant-list">${shown}${more}</div>`;
}

// Only the path above the site, e.g. "NORAM › GRL" (#178); the tenant has its own column.
function siteMeta(item) {
  // With ALERT_BOARD_SITE_LOCATION_TYPE the full path above the site (#158).
  return escHtml(item.ancestor_path || item.parent || "");
}

function renderTableRows(alerts, payload) {
  if (!alerts.length) {
    let emptyText = "No sites match the current filters.";
    let emptyClass = "empty-state";
    if (payload.persistence_configured === false) {
      emptyText = "The alert board needs a PostgreSQL database. Set NAUTOBOT_MAPS_DATABASE_URL "
        + "and restart the app. The map works without it.";
    } else if (payload.sync_pending && !allAlerts.length) {
      emptyText = "Inventory sync in progress – the board will update automatically.";
    } else if (severityFilter === "alarms" && !allAlerts.some(hasActiveAlarm)) {
      emptyText = WALL_VIEW
        ? "No active alarms."
        : "No site has an active alarm. Choose All sites to see every site.";
      emptyClass = "empty-state all-clear";
    }
    alertsTableBody.innerHTML = `<tr><td colspan="7" class="${emptyClass}">${emptyText}</td></tr>`;
    formatBoardStatus(payload, 0);
    return;
  }

  let handledSeen = false;
  let maintenanceSeen = false;
  alertsTableBody.innerHTML = alerts.map((item) => {
    const inMaintenance = item.alert_level === "maintenance";
    // Wall view: sites in maintenance are listed last, under a heading (#283).
    const maintenanceHeading = WALL_VIEW && inMaintenance && !maintenanceSeen
      ? '<tr class="maintenance-heading"><td colspan="7">In maintenance</td></tr>'
      : "";
    if (inMaintenance) maintenanceSeen = true;
    // Wall view: sites someone is on are faded, below a dashed line (#271).
    const handled = WALL_VIEW && !inMaintenance && siteCaseState(item).state === "handled";
    const firstHandled = handled && !handledSeen;
    if (handled) handledSeen = true;
    const caseClass = handled ? `case-handled${firstHandled ? " handled-first" : ""}` : "";
    const address = formatLocationAddress(item);
    const isExpanded = isSiteExpanded(item);
    const downCount = item.down_device_count || 0;
    const toggleButton = downDeviceList(item).length
      ? `<button class="site-toggle-btn" type="button" data-site-id="${escHtml(item.id || "")}" aria-expanded="${isExpanded ? "true" : "false"}" aria-label="${isExpanded ? "Collapse" : "Expand"} ${escHtml(item.name || item.id || "site")}">${isExpanded ? "▾" : "▸"}</button>`
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
            ${siteNameHtml(item, !WALL_VIEW)}
            <div class="site-summary">${downCount} down · ${item.device_count || 0} monitored</div>
          </div>
        </div>
        ${address ? `<div class="site-address">${escHtml(address)}</div>` : ""}
        ${meta ? `<div class="site-meta">${meta}</div>` : ""}
      </td>
      <td>${alertBadge(item.alert_level)}${WALL_VIEW && !inMaintenance ? caseStateBadge(item) : ""}${item.maintenance ? `<div class="maintenance-note">${maintenanceNote(item.maintenance.until, item.maintenance.reason)}</div>` : ""}</td>
      <td class="col-tenants">${tenantCell(item)}</td>
      <td class="since-cell">${WALL_VIEW ? "" : sinceLabel("in alarm", siteAlarmSeconds(item))}</td>
      <td class="cases-cell col-cases">${renderCases(item)}</td>
      <td class="reason-cell col-reason">${escHtml(item.alert_reason || "No active alert")}</td>
      <td class="col-action">${actionCell(item)}</td>
    </tr>
    ${WALL_VIEW && inMaintenance ? "" : renderDownDeviceRows(item, isExpanded, handled ? "case-handled" : "")}
  `;
  }).join("");
  formatBoardStatus(payload, alerts.length);
}

function getFilteredAlerts() {
  const siteNeedle = filterSite.value.trim().toLowerCase();
  const status = filterStatus.value;
  const type = filterType.value;
  const tenant = filterTenant.value;
  const sort = sortBy.value;

  const filtered = allAlerts.filter((item) => {
    const searchableText = [
      item.name,
      item.ancestor_path || item.parent,
      formatLocationAddress(item),
      item.country,
    ].join(" ").toLowerCase();
    // The wall view also lists sites in maintenance, at the bottom (#283).
    if (!matchesSeverityFilter(item) && !(WALL_VIEW && item.alert_level === "maintenance")) return false;
    if (siteNeedle && !searchableText.includes(siteNeedle)) return false;
    if (status && item.status !== status) return false;
    if (type && item.location_type !== type) return false;
    if (tenant && !siteTenants(item).includes(tenant)) return false;
    return true;
  });

  // The wall view puts the sites nobody is on first (#271).
  filtered.sort((a, b) => (WALL_VIEW ? caseSortRank(a) - caseSortRank(b) : 0) || compareSites(a, b, sort));

  return filtered;
}

// Wall view order: nobody on it, someone on it, then planned maintenance (#283).
function caseSortRank(item) {
  if (item.alert_level === "maintenance") return 2;
  return siteCaseState(item).state === "handled" ? 1 : 0;
}

function applyFilters(payload) {
  const filtered = getFilteredAlerts();
  renderTableRows(filtered, payload);
  renderMoreFiltersLabel();
}

// "More filters (2)": the hidden filters say when they are in use.
function moreFiltersInUse() {
  return [filterStatus?.value, filterType?.value, toggleNonOperational?.checked].filter(Boolean).length;
}

function renderMoreFiltersLabel() {
  if (!moreFiltersToggle) return;
  const inUse = moreFiltersInUse();
  moreFiltersToggle.textContent = inUse ? `More filters (${inUse})` : "More filters";
}

function stopSyncPolling() {
  if (syncPollTimer) clearTimeout(syncPollTimer);
  syncPollTimer = null;
}

function scheduleSyncPoll(payload) {
  stopSyncPolling();
  if (!payload.sync_pending || syncPollAttempts >= SYNC_POLL_MAX_ATTEMPTS) return;
  syncPollAttempts += 1;
  syncPollTimer = setTimeout(() => loadAlertBoard(false, { background: true }), SYNC_POLL_INTERVAL_MS);
}

function formatCountdown(totalSeconds) {
  const seconds = Math.max(0, Math.ceil(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = String(seconds % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

function setNextUpdate(payload, now = Date.now()) {
  const seconds = payload.next_update_in_seconds;
  nextUpdateDueAt = typeof seconds === "number" && seconds >= 0 ? now + seconds * 1000 : null;
  renderNextUpdate(now);
}

function renderNextUpdate(now = Date.now()) {
  if (!nextUpdateEl) return;
  if (latestPayload.sync_pending) {
    nextUpdateEl.textContent = "Updating…";
    nextUpdateEl.hidden = false;
    return;
  }
  if (nextUpdateDueAt === null) {
    nextUpdateEl.hidden = true;
    return;
  }
  nextUpdateEl.hidden = false;
  const remainingMs = nextUpdateDueAt - now;
  if (remainingMs > 0) {
    nextUpdateEl.textContent = `Next update in ${formatCountdown(remainingMs / 1000)}`;
    return;
  }
  nextUpdateEl.textContent = "Updating…";
  // At most one automatic reload per gap, so a sync that keeps failing does
  // not turn into a request loop.
  if (refreshBtn.disabled || syncPollTimer || now - lastAutoUpdateAt < AUTO_UPDATE_MIN_GAP_MS) return;
  lastAutoUpdateAt = now;
  loadAlertBoard(false, { background: true });
}

async function loadAlertBoard(forceRefresh = false, { background = false } = {}) {
  loadSeq += 1;
  const seq = loadSeq;
  if (!background) {
    // A user-initiated load restarts the polling budget.
    stopSyncPolling();
    syncPollAttempts = 0;
    boardStatus.textContent = "Loading alert board…";
    refreshBtn.disabled = true;
    if (toggleNonOperational) toggleNonOperational.disabled = true;
    if (collapseAllSitesBtn) collapseAllSitesBtn.disabled = true;
    if (expandAllSitesBtn) expandAllSitesBtn.disabled = true;
  }
  try {
    const params = new URLSearchParams();
    // The server only accepts 1/true/yes/refresh; a timestamp was silently ignored (#121).
    if (forceRefresh) params.set("refresh", "1");
    if (toggleNonOperational?.checked) params.set("include_non_operational", "1");
    const query = params.toString();
    const resp = await fetch(`/api/alerts${query ? `?${query}` : ""}`, { cache: "no-store" });
    const payload = await readJsonResponse(resp);
    if (seq !== loadSeq) return; // A newer load started meanwhile.
    if (!resp.ok || payload.error) {
      throw new Error(payload.error || resp.statusText || `HTTP ${resp.status}`);
    }
    latestPayload = payload;
    allAlerts = payload.alerts || [];
    lastLoadedAt = Date.now();
    if (wallErrorEl) wallErrorEl.hidden = true;
    populateFilters(allAlerts);
    renderSummary(payload.summary || {});
    applyFilters(payload);
    scheduleSyncPoll(payload);
    setNextUpdate(payload);
    // The feed refreshes with the board (#180).
    loadFeed();
    renderWallStatus();
  } catch (err) {
    if (seq !== loadSeq) return;
    stopSyncPolling();
    if (WALL_VIEW) {
      // Keep the last good board on screen (even an empty one), under a
      // banner that says it is old.
      showWallError(err.message);
      if (lastLoadedAt !== null) return;
    }
    alertsTableBody.innerHTML = `<tr><td colspan="7" class="empty-state">Could not load alerts: ${escHtml(err.message)}</td></tr>`;
    boardStatus.textContent = "Alert board unavailable";
    showError(`Failed to load alert board: ${err.message}`);
  } finally {
    // An older load must not re-enable the controls while a newer one runs.
    if (seq === loadSeq) {
      refreshBtn.disabled = false;
      if (toggleNonOperational) toggleNonOperational.disabled = false;
      if (collapseAllSitesBtn) collapseAllSitesBtn.disabled = false;
      if (expandAllSitesBtn) expandAllSitesBtn.disabled = false;
    }
  }
}

function formatClock(ms) {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// "Updated 14:05 · 3 sites with alarms"; flagged once no load has worked for a while.
function renderWallStatus(now = Date.now()) {
  if (!wallUpdatedEl) return;
  if (lastLoadedAt === null) {
    wallUpdatedEl.textContent = "Loading…";
    return;
  }
  const alarmSites = allAlerts.filter(hasActiveAlarm);
  const alarms = alarmSites.length;
  const withoutCase = alarmSites.filter((item) => siteCaseState(item).state !== "handled").length;
  const planned = allAlerts.filter((item) => item.alert_level === "maintenance").length;
  const stale = now - lastLoadedAt >= WALL_STALE_MS;
  wallUpdatedEl.classList.toggle("wall-stale", stale);
  wallUpdatedEl.textContent = stale
    ? `NOT UPDATED since ${formatClock(lastLoadedAt)}`
    : `Updated ${formatClock(lastLoadedAt)} · ${alarms} site${alarms === 1 ? "" : "s"} with alarms · ${withoutCase} without case`
      + (planned ? ` · ${planned} in maintenance` : "");
}

function showWallError(message) {
  if (!wallErrorEl) return;
  const since = lastLoadedAt === null ? "" : ` Showing the board from ${formatClock(lastLoadedAt)}.`;
  wallErrorEl.textContent = `Alert board not updating: ${message}.${since}`;
  wallErrorEl.hidden = false;
}

function showError(message) {
  const toast = document.getElementById("error-toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(showError._timer);
  showError._timer = setTimeout(() => toast.classList.add("hidden"), 6000);
}

[filterSite, filterStatus, filterType, filterTenant, sortBy].forEach((element) => {
  element.addEventListener(element.tagName === "INPUT" ? "input" : "change", () => {
    applyFilters(latestPayload);
  });
});

sortBy.addEventListener("change", () => rememberSort(sortBy.value));

severityFilterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setSeverityFilter(button.dataset.severityFilter);
    applyFilters(latestPayload);
  });
});

if (moreFiltersToggle && moreFilters) {
  moreFiltersToggle.addEventListener("click", () => {
    const open = moreFilters.hidden;
    moreFilters.hidden = !open;
    moreFiltersToggle.setAttribute("aria-expanded", open ? "true" : "false");
  });
}

if (clearAlertFiltersBtn) {
  clearAlertFiltersBtn.addEventListener("click", () => {
    if (refreshBtn.disabled) return;
    filterSite.value = "";
    filterStatus.value = "";
    filterType.value = "";
    filterTenant.value = "";
    setSeverityFilter(DEFAULT_SEVERITY_FILTER);
    sortBy.value = DEFAULT_SORT;
    rememberSort(DEFAULT_SORT);
    // Non-operational sites came from the server: reload without them.
    if (toggleNonOperational?.checked) {
      toggleNonOperational.checked = false;
      expandedSiteIds = new Set();
      allSitesExpanded = false;
      loadAlertBoard(false);
      return;
    }
    applyFilters(latestPayload);
  });
}

if (toggleNonOperational) {
  toggleNonOperational.addEventListener("change", () => {
    if (refreshBtn.disabled) return;
    expandedSiteIds = new Set();
    allSitesExpanded = false;
    loadAlertBoard(false);
  });
}

if (collapseAllSitesBtn) {
  collapseAllSitesBtn.addEventListener("click", () => {
    if (refreshBtn.disabled) return;
    allSitesExpanded = false;
    expandedSiteIds = new Set();
    applyFilters(latestPayload);
  });
}

if (expandAllSitesBtn) {
  expandAllSitesBtn.addEventListener("click", () => {
    if (refreshBtn.disabled) return;
    allSitesExpanded = true;
    expandedSiteIds = new Set();
    applyFilters(latestPayload);
  });
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  if (themeToggle) {
    const darkEnabled = theme === "dark";
    themeToggle.textContent = darkEnabled ? "Light mode" : "Dark mode";
    themeToggle.setAttribute("aria-label", darkEnabled ? "Switch to light mode" : "Switch to dark mode");
    themeToggle.setAttribute("aria-pressed", darkEnabled ? "true" : "false");
  }
}

function initTheme() {
  let theme = "light";
  try {
    const stored = localStorage.getItem("nautobot-maps-theme");
    if (stored === "dark" || stored === "light") {
      theme = stored;
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      theme = "dark";
    }
  } catch (_err) {
    theme = "light";
  }
  applyTheme(theme);
}

refreshBtn.addEventListener("click", () => loadAlertBoard(true));
setInterval(() => {
  renderNextUpdate();
  renderWallStatus();
}, 1000);
if (WALL_VIEW) setInterval(() => loadAlertBoard(false, { background: true }), WALL_RELOAD_MS);
// Browsers slow timers in background tabs; catch up as soon as the tab is visible.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) renderNextUpdate();
});
if (historyCloseBtn && historyPanel) {
  historyCloseBtn.addEventListener("click", () => closeSidePanel(historyPanel));
}
if (caseCloseBtn && casePanel) {
  caseCloseBtn.addEventListener("click", () => closeSidePanel(casePanel));
}
if (copyCloseBtn && copyPanel) {
  copyCloseBtn.addEventListener("click", () => closeSidePanel(copyPanel));
}
if (maintCloseBtn && maintPanel) {
  maintCloseBtn.addEventListener("click", () => closeSidePanel(maintPanel));
}

// The panel's "What" and "When" choices show their details (#283).
maintPanel?.addEventListener("change", (event) => {
  const form = event.target.closest(".maint-form");
  if (!form) return;
  form.querySelector(".maint-devices").hidden = form.querySelector('input[name="maint-scope"]:checked')?.value !== "devices";
  const planned = form.querySelector('input[name="maint-when"]:checked')?.value === "planned";
  form.querySelector(".maint-planned").hidden = !planned;
  form.querySelector(".maint-save-btn").textContent = planned ? "Plan maintenance" : "Start maintenance";
});

maintPanel?.addEventListener("submit", async (event) => {
  const form = event.target.closest(".maint-form");
  if (!form) return;
  event.preventDefault();
  const body = maintenanceRequestBody(readMaintenanceForm(form));
  if (body.error) {
    showMaintenanceError(form, body.error);
    return;
  }
  const button = form.querySelector(".maint-save-btn");
  button.disabled = true;
  try {
    const resp = await fetch("/api/maintenance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await readJsonResponse(resp);
    if (!resp.ok || payload.error) throw new Error(payload.error || `HTTP ${resp.status}`);
    const item = allAlerts.find((alert) => String(alert.id) === form.dataset.siteId);
    await loadAlertBoard(false);
    if (item) await refreshMaintenancePanel(allAlerts.find((alert) => String(alert.id) === form.dataset.siteId) || item);
  } catch (err) {
    showMaintenanceError(form, `Could not save: ${err.message}`);
    button.disabled = false;
  }
});

maintPanel?.addEventListener("click", async (event) => {
  const endBtn = event.target.closest(".maint-end-btn");
  if (!endBtn) return;
  endBtn.disabled = true;
  const siteId = maintContent.querySelector(".maint-form")?.dataset.siteId;
  try {
    const resp = await fetch(`/api/maintenance/${encodeURIComponent(endBtn.dataset.windowId)}/end`, { method: "POST" });
    const payload = await readJsonResponse(resp);
    if (!resp.ok || payload.error) throw new Error(payload.error || `HTTP ${resp.status}`);
    await loadAlertBoard(false);
    const item = allAlerts.find((alert) => String(alert.id) === siteId);
    if (item) await refreshMaintenancePanel(item);
  } catch (err) {
    showError(`Could not end maintenance: ${err.message}`);
    endBtn.disabled = false;
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const openPanel = sidePanels.find((panel) => !panel.classList.contains("hidden"));
  if (!openPanel) return;
  event.preventDefault();
  closeSidePanel(openPanel);
});

casePanel?.addEventListener("change", (event) => {
  const form = event.target.closest(".case-form");
  if (!form) return;
  if (event.target.classList.contains("case-select-all")) {
    form.querySelectorAll(".case-device-checkbox").forEach((box) => {
      box.checked = event.target.checked;
    });
  }
  if (event.target.matches(".case-select-all, .case-device-checkbox")) syncCaseSelection(form);
});

// The export period applies to both download links (#277).
historyPanel?.addEventListener("change", (event) => {
  if (!event.target.classList.contains("history-export-days")) return;
  const bar = event.target.closest(".history-export");
  bar.querySelectorAll(".history-export-link").forEach((link) => {
    link.href = historyExportUrl(bar.dataset.siteId, link.dataset.view, event.target.value);
  });
});

// The typed number may already be on the ticked devices (#273).
casePanel?.addEventListener("input", (event) => {
  const form = event.target.closest(".case-form");
  if (form && event.target.classList.contains("case-input")) syncCaseSelection(form);
});

alertsTableBody.addEventListener("click", (event) => {
  const moreBtn = event.target.closest(".tenant-more-btn");
  if (!moreBtn) return;
  moreBtn.previousElementSibling.hidden = false;
  moreBtn.remove();
});

alertsTableBody.addEventListener("click", async (event) => {
  const toggleBtn = event.target.closest(".site-toggle-btn");
  if (toggleBtn) {
    const siteId = String(toggleBtn.dataset.siteId || "");
    if (!siteId) return;
    if (allSitesExpanded) {
      if (expandedSiteIds.has(siteId)) {
        expandedSiteIds.delete(siteId);
      } else {
        expandedSiteIds.add(siteId);
      }
    } else if (expandedSiteIds.has(siteId)) {
      expandedSiteIds.delete(siteId);
    } else {
      expandedSiteIds.add(siteId);
    }
    applyFilters(latestPayload);
    return;
  }

  const copyBtn = event.target.closest(".copy-site-btn");
  if (copyBtn) {
    await copySite(String(copyBtn.dataset.siteId || ""), copyBtn);
    return;
  }

  const maintOpenBtn = event.target.closest(".maint-open-btn");
  if (maintOpenBtn) {
    await openMaintenancePanel(String(maintOpenBtn.dataset.siteId || ""));
    return;
  }

  const caseOpenBtn = event.target.closest(".case-open-btn");
  if (caseOpenBtn) {
    openCasePanel(String(caseOpenBtn.dataset.siteId || ""));
    return;
  }

  const historyBtn = event.target.closest(".history-btn");
  if (historyBtn) {
    const siteId = historyBtn.dataset.siteId;
    if (!siteId) return;
    historyBtn.disabled = true;
    try {
      const resp = await fetch(`/api/alert-history?site_id=${encodeURIComponent(siteId)}`);
      const payload = await readJsonResponse(resp);
      if (!resp.ok || payload.error) throw new Error(payload.error || `HTTP ${resp.status}`);
      renderAlertHistory(siteId, Array.isArray(payload.instances) ? payload.instances : []);
      openSidePanel(historyPanel, `.history-btn[data-site-id="${CSS.escape(siteId)}"]`, historyCloseBtn);
    } catch (err) {
      showError(`Failed to load history: ${err.message}`);
    } finally {
      historyBtn.disabled = false;
    }
  }
});

casePanel?.addEventListener("submit", async (event) => {
  const form = event.target.closest(".case-form");
  if (!form) return;
  event.preventDefault();
  const siteId = form.dataset.siteId;
  const caseBtn = form.querySelector(".case-save-btn");
  const caseInput = form.querySelector(".case-input");
  const caseNumber = (caseInput?.value || "").trim();
  // Not the devices that already have this number (Copilot review on #274).
  const deviceIds = caseTargetIds(form, caseNumber);
  const deviceNameById = Object.fromEntries(
    Array.from(form.querySelectorAll(".case-device-checkbox")).map((box) => [box.value, box.nextElementSibling.textContent]),
  );
  if (!siteId || !deviceIds.length || !caseNumber) {
    showError("Select at least one device and enter a case number first.");
    return;
  }
  caseBtn.disabled = true;
  try {
    const resp = await fetch("/api/alert-cases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ site_id: siteId, device_ids: deviceIds, case_number: caseNumber }),
    });
    const payload = await resp.json();
    if (!resp.ok || payload.error) {
      const missing = Array.isArray(payload.missing_device_ids) && payload.missing_device_ids.length
        ? ` (no longer down: ${payload.missing_device_ids.map((id) => deviceNameById[id] || id).join(", ")} – untick and try again)`
        : "";
      throw new Error(`${payload.error || `HTTP ${resp.status}`}${missing}`);
    }
    // Saved: close the panel.  The server drops its cached board when a case
    // is added; a plain reload is enough and must not trigger a full
    // inventory sync.  Focus returns to the row's (re-rendered) + Case button.
    hideSidePanel(casePanel);
    await loadAlertBoard(false);
    restorePanelFocus();
  } catch (err) {
    showError(`Failed to add case: ${err.message}`);
  } finally {
    caseBtn.disabled = false;
  }
});

feedFilterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    button.setAttribute("aria-pressed", button.getAttribute("aria-pressed") === "true" ? "false" : "true");
    loadFeed();
  });
});

if (feedToggle && feedShowBtn) {
  let stored = null;
  try {
    stored = localStorage.getItem(FEED_COLLAPSED_KEY);
  } catch (_err) {
    // Storage unavailable: decide by the window width.
  }
  setFeedCollapsed(feedStartsCollapsed(stored, window.innerWidth));
  feedToggle.addEventListener("click", () => {
    setFeedCollapsed(true);
    rememberFeedCollapsed(true);
    feedShowBtn.focus();
  });
  feedShowBtn.addEventListener("click", () => {
    setFeedCollapsed(false);
    rememberFeedCollapsed(false);
    feedToggle.focus();
    loadFeed();
  });
}

restoreSort();
setSeverityFilter(DEFAULT_SEVERITY_FILTER);
if (WALL_VIEW) {
  // The same screen for everyone: newest down first, every site open, no feed.
  sortBy.value = DEFAULT_SORT;
  allSitesExpanded = true;
  setFeedCollapsed(true);
}
loadAlertBoard();
if (themeToggle) {
  initTheme();
  themeToggle.addEventListener("click", () => {
    const nextTheme = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(nextTheme);
    try {
      localStorage.setItem("nautobot-maps-theme", nextTheme);
    } catch (_err) {
      // noop
    }
  });
}
