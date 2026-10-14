// The maintenance panel (#283): a site's current and planned windows, and a
// form for a new one.
import { formatFeedTime } from "../lib/format.js";
import { escHtml } from "../lib/html.js";
import { readJsonResponse } from "../lib/http.js";
import { showError } from "../lib/toast.js";
import { defaultPlan, initWhen, localDay, whenFieldsetHtml, whenRequest } from "../lib/when.js";
import { loadAlertBoard } from "./board.js";
import { openSidePanel } from "./panels.js";
import { els, findSite } from "./state.js";

// One window in the panel: what, when, why, and End now / Cancel.
export function maintenanceWindowItem(window, deviceNames) {
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
// *now* sets the planned fields' first values (from the next full hour).
export function renderMaintenancePanel(item, windows, devices, now = new Date()) {
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
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "your time zone";
  return `
    ${list}
    <form class="maint-form" data-site-id="${escHtml(item.id || "")}">
      <fieldset class="maint-fieldset">
        <legend>What</legend>
        <label class="maint-choice"><input type="radio" name="maint-scope" value="site" checked /> Whole site</label>
        <label class="maint-choice"><input type="radio" name="maint-scope" value="devices" /> Some devices</label>
        <div class="maint-devices case-device-list" hidden>${deviceChoices}</div>
      </fieldset>
      ${whenFieldsetHtml(defaultPlan(now), zone)}
      <label class="case-input-label" for="maint-reason">Reason</label>
      <input id="maint-reason" class="case-input maint-reason" type="text" maxlength="200" autocomplete="off" placeholder="e.g. Core switch upgrade" />
      <p class="maint-error" role="alert" hidden></p>
      <button class="case-save-btn maint-save-btn" type="submit">Start maintenance</button>
    </form>`;
}

// The request for a new window from the form's values, or {error}.
// Planned days and times are the browser's local time; sent as UTC (lib/when.js).
export function maintenanceRequestBody(values) {
  const reason = (values.reason || "").trim();
  if (!reason) return { error: "Enter a reason." };
  const body = { site_id: values.siteId, reason };
  if (values.scope === "devices") {
    if (!values.deviceIds.length) return { error: "Tick at least one device, or choose Whole site." };
    body.device_ids = values.deviceIds;
  }
  const when = whenRequest(values);
  return when.error ? when : { ...body, ...when };
}

// The open form's "When" reader (initWhen); one panel is open at a time.
let readWhen = () => ({});

function readMaintenanceForm(form) {
  return {
    siteId: form.dataset.siteId,
    scope: form.querySelector('input[name="maint-scope"]:checked')?.value,
    deviceIds: Array.from(form.querySelectorAll(".maint-device:checked")).map((box) => box.value),
    reason: form.querySelector(".maint-reason")?.value,
    ...readWhen(),
  };
}

export async function openMaintenancePanel(siteId) {
  const item = findSite(siteId);
  if (!els.maintPanel || !item) return;
  els.maintTitle.textContent = `Maintenance · ${item.name || siteId}`;
  els.maintContent.innerHTML = '<p class="maint-empty">Loading…</p>';
  openSidePanel(els.maintPanel, `.maint-open-btn[data-site-id="${CSS.escape(siteId)}"]`, els.maintCloseBtn);
  await refreshMaintenancePanel(item);
}

// Bumped by every panel load: a slower answer for a site opened before must
// not fill the panel of the one open now.
let maintenanceRequestSeq = 0;

async function refreshMaintenancePanel(item) {
  const seq = ++maintenanceRequestSeq;
  const params = new URLSearchParams({ site_id: item.id });
  const devicesParams = new URLSearchParams(params);
  if (els.toggleNonOperational?.checked) devicesParams.set("include_non_operational", "1");
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
    const now = new Date();
    els.maintContent.innerHTML = renderMaintenancePanel(item, windows.windows || [], devices.devices || [], now);
    const fieldset = els.maintContent.querySelector(".when-fieldset");
    readWhen = initWhen(fieldset, defaultPlan(now), localDay(now));
  } catch (err) {
    if (seq !== maintenanceRequestSeq) return;
    els.maintContent.innerHTML = `<p class="maint-error">Could not load maintenance: ${escHtml(err.message)}</p>`;
  }
}

function showMaintenanceError(form, message) {
  const box = form.querySelector(".maint-error");
  box.textContent = message;
  box.hidden = !message;
}

async function saveWindow(form) {
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
    const item = findSite(form.dataset.siteId);
    await loadAlertBoard(false);
    if (item) await refreshMaintenancePanel(findSite(form.dataset.siteId) || item);
  } catch (err) {
    showMaintenanceError(form, `Could not save: ${err.message}`);
    button.disabled = false;
  }
}

async function endWindow(endBtn) {
  endBtn.disabled = true;
  const siteId = els.maintContent.querySelector(".maint-form")?.dataset.siteId;
  try {
    const resp = await fetch(`/api/maintenance/${encodeURIComponent(endBtn.dataset.windowId)}/end`, { method: "POST" });
    const payload = await readJsonResponse(resp);
    if (!resp.ok || payload.error) throw new Error(payload.error || `HTTP ${resp.status}`);
    await loadAlertBoard(false);
    const item = findSite(siteId);
    if (item) await refreshMaintenancePanel(item);
  } catch (err) {
    showError(`Could not end maintenance: ${err.message}`);
    endBtn.disabled = false;
  }
}

export function initMaintenancePanel() {
  const { maintPanel } = els;
  if (!maintPanel) return;

  // The panel's "What" and "When" choices show their details (#283).
  maintPanel.addEventListener("change", (event) => {
    const form = event.target.closest(".maint-form");
    if (!form) return;
    form.querySelector(".maint-devices").hidden = form.querySelector('input[name="maint-scope"]:checked')?.value !== "devices";
    const planned = form.querySelector('input[name="maint-when"]:checked')?.value === "planned";
    form.querySelector(".maint-planned").hidden = !planned;
    form.querySelector(".maint-save-btn").textContent = planned ? "Plan maintenance" : "Start maintenance";
  });

  maintPanel.addEventListener("submit", (event) => {
    const form = event.target.closest(".maint-form");
    if (!form) return;
    event.preventDefault();
    saveWindow(form);
  });

  maintPanel.addEventListener("click", (event) => {
    const endBtn = event.target.closest(".maint-end-btn");
    if (endBtn) endWindow(endBtn);
  });
}
