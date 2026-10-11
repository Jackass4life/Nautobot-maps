// The side panels (#179): one open at a time, Escape closes it and focus
// returns to the button that opened it.  Also History and Copy.
import { caseWithUrl, casePill } from "../lib/cases.js";
import { formatDuration, formatReportTime, formatTimestamp } from "../lib/format.js";
import { escHtml } from "../lib/html.js";
import { readJsonResponse } from "../lib/http.js";
import { showError } from "../lib/toast.js";
import { formatLocationAddress, siteTenants } from "./rows.js";
import { config, els, findSite } from "./state.js";

const COPIED_LABEL_MS = 2000;

// Selector of the button that opened the side panel.  Rows are re-rendered
// on every board update, so the button itself may be gone when it closes.
let panelTriggerSelector = null;

function sidePanels() {
  return [els.historyPanel, els.casePanel, els.copyPanel, els.maintPanel].filter(Boolean);
}

export function hideSidePanel(panel) {
  panel.classList.add("hidden");
  panel.setAttribute("aria-hidden", "true");
}

export function openSidePanel(panel, triggerSelector, focusTarget) {
  sidePanels().forEach((other) => {
    if (other !== panel) hideSidePanel(other);
  });
  panel.classList.remove("hidden");
  panel.setAttribute("aria-hidden", "false");
  panelTriggerSelector = triggerSelector;
  (focusTarget || panel).focus();
}

export function restorePanelFocus() {
  const trigger = panelTriggerSelector && document.querySelector(panelTriggerSelector);
  panelTriggerSelector = null;
  if (!trigger) return;
  // Opened from a row's "⋯" menu, now closed: back to the menu button (#311).
  const menu = trigger.closest("details.row-menu");
  if (menu && !menu.open) menu.querySelector("summary")?.focus();
  else trigger.focus();
}

export function closeSidePanel(panel) {
  if (!panel || panel.classList.contains("hidden")) return;
  hideSidePanel(panel);
  restorePanelFocus();
}

// The open side panel, or undefined.
export function openPanel() {
  return sidePanels().find((panel) => !panel.classList.contains("hidden"));
}

// A site and its down devices as plain text, for pasting into an ITSM
// ticket (#227): one line per device, newest down first.
export function siteReport(item, nautobotUrl = "") {
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
  if (Array.isArray(item.active_cases) && item.active_cases.length) lines.push(`Cases: ${item.active_cases.map(caseWithUrl).join(", ")}`);
  if (nautobotUrl && item.id) lines.push(`Nautobot: ${nautobotUrl.replace(/\/+$/, "")}/dcim/locations/${encodeURIComponent(item.id)}/`);

  // The likely cause first (#285), then newest down first.
  const devices = (Array.isArray(item.down_devices) ? [...item.down_devices] : [])
    .sort((a, b) => (Number(Boolean(b.core)) - Number(Boolean(a.core)))
      || (Date.parse(b.down_started_at || "") || 0) - (Date.parse(a.down_started_at || "") || 0));
  const cores = devices.filter((device) => device.core).map((device) => device.device_name || device.device_id);
  const behind = devices.filter((device) => device.probable_cause).length;
  if (cores.length && behind) {
    lines.push(`Likely cause: ${cores.join(", ")} down; ${behind} other device${behind === 1 ? "" : "s"} probably behind it`);
  }
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
      cases.length ? `Case ${cases.map(caseWithUrl).join(", ")}` : "",
      device.core ? "Core device" : "",
      device.probable_cause ? `Likely caused by ${device.probable_cause}` : "",
    ];
    lines.push(`- ${fields.filter(Boolean).join(" | ")}`);
  });
  return lines.join("\n");
}

// The clipboard API needs HTTPS (or localhost); plain-HTTP pages fall back to
// the older copy command.  Returns whether the text was copied.
export async function copyText(text) {
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

export async function copySite(siteId, button) {
  const item = findSite(siteId);
  if (!item) return;
  const text = siteReport(item, config.nautobotUrl);
  if (await copyText(text)) {
    if (els.copyStatus) els.copyStatus.textContent = `Copied ${item.name || siteId} to the clipboard`;
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
  const { copyPanel, copyTextArea } = els;
  if (!copyPanel || !copyTextArea) {
    showError("Could not copy to the clipboard.");
    return;
  }
  copyTextArea.value = text;
  openSidePanel(copyPanel, `.copy-site-btn[data-site-id="${CSS.escape(siteId)}"]`, copyTextArea);
  copyTextArea.select();
}

// The site's history as a CSV download (#277).
export function historyExportUrl(siteId, view, days) {
  return `/api/alert-history.csv?${new URLSearchParams({ site_id: siteId, days, view })}`;
}

const HISTORY_EXPORT_DEFAULT_DAYS = "30";

export function historyExportBar(siteId) {
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

export function historyHtml(siteId, instances) {
  const exportBar = historyExportBar(siteId);
  if (!instances.length) {
    return `${exportBar}<div class="history-instance"><div class="history-line">No incidents found.</div></div>`;
  }
  return exportBar + instances.map((instance) => {
    const cases = Array.isArray(instance.cases) ? instance.cases.map((entry) => entry.case_number || "").filter(Boolean) : [];
    const events = Array.isArray(instance.events)
      ? instance.events.map((event) => `${escHtml(event.event_type || "")} @ ${formatTimestamp(event.event_at)}`).join(", ")
      : "";
    return `
        <article class="history-instance">
          <div><strong>${escHtml(instance.device_name || instance.device_id || "Unknown device")}</strong> · ${escHtml(instance.status || "unknown")} · ${escHtml(instance.alert_level || "unknown")}</div>
          <div class="history-line">Downtime: ${formatDuration(instance.total_downtime_seconds || 0)}</div>
          <div class="history-line">Opened: ${formatTimestamp(instance.down_started_at)} · Resolved: ${formatTimestamp(instance.resolved_at)}</div>
          <div class="history-line">Cases: ${cases.length ? cases.map((value) => casePill(value)).join(" ") : "—"}</div>
          ${instance.probable_cause ? `<div class="history-line">Likely caused by: ${escHtml(instance.probable_cause)}</div>` : ""}
          <div class="history-line">Events: ${events || "—"}</div>
        </article>
      `;
  }).join("");
}

function renderAlertHistory(siteId, instances) {
  const { historyPanel, historyTitle, historyContent } = els;
  if (!historyPanel || !historyTitle || !historyContent) return;
  historyTitle.textContent = `History · ${findSite(siteId)?.name || siteId}`;
  historyContent.innerHTML = historyHtml(siteId, instances);
}

export async function openHistory(historyBtn) {
  const siteId = historyBtn.dataset.siteId;
  if (!siteId) return;
  historyBtn.disabled = true;
  try {
    const resp = await fetch(`/api/alert-history?site_id=${encodeURIComponent(siteId)}`);
    const payload = await readJsonResponse(resp);
    if (!resp.ok || payload.error) throw new Error(payload.error || `HTTP ${resp.status}`);
    renderAlertHistory(siteId, Array.isArray(payload.instances) ? payload.instances : []);
    openSidePanel(els.historyPanel, `.history-btn[data-site-id="${CSS.escape(siteId)}"]`, els.historyCloseBtn);
  } catch (err) {
    showError(`Failed to load history: ${err.message}`);
  } finally {
    historyBtn.disabled = false;
  }
}

export function initPanels() {
  [
    [els.historyCloseBtn, els.historyPanel],
    [els.caseCloseBtn, els.casePanel],
    [els.copyCloseBtn, els.copyPanel],
    [els.maintCloseBtn, els.maintPanel],
  ].forEach(([closeBtn, panel]) => {
    if (closeBtn && panel) closeBtn.addEventListener("click", () => closeSidePanel(panel));
  });

  // The export period applies to both download links (#277).
  els.historyPanel?.addEventListener("change", (event) => {
    if (!event.target.classList.contains("history-export-days")) return;
    const bar = event.target.closest(".history-export");
    bar.querySelectorAll(".history-export-link").forEach((link) => {
      link.href = historyExportUrl(bar.dataset.siteId, link.dataset.view, event.target.value);
    });
  });
}
