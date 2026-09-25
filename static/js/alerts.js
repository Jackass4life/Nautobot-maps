"use strict";

const boardStatus = document.getElementById("board-status");
const alertsTableBody = document.getElementById("alerts-table-body");
const filterSite = document.getElementById("filter-site");
const filterSeverity = document.getElementById("filter-severity");
const filterStatus = document.getElementById("filter-status");
const filterType = document.getElementById("filter-type");
const filterTenant = document.getElementById("filter-tenant");
const sortBy = document.getElementById("sort-by");
const refreshBtn = document.getElementById("refresh-alerts");
const quickSeverityButtons = Array.from(document.querySelectorAll("[data-quick-severity]"));
const clearAlertFiltersBtn = document.getElementById("clear-alert-filters");
const toggleNonOperational = document.getElementById("toggle-non-operational");
const collapseAllSitesBtn = document.getElementById("collapse-all-sites");
const expandAllSitesBtn = document.getElementById("expand-all-sites");
const themeToggle = document.getElementById("theme-toggle");
const historyPanel = document.getElementById("history-panel");
const historyTitle = document.getElementById("history-title");
const historyContent = document.getElementById("history-content");
const historyCloseBtn = document.getElementById("history-close");
const nextUpdateEl = document.getElementById("next-update");

let allAlerts = [];
let latestPayload = { checked_at: null, stale: false, summary: {}, alerts: [] };
let historyTriggerBtn = null;
let expandedSiteIds = new Set();
let allSitesExpanded = false;

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

function severityWeight(level) {
  if (level === "critical") return 0;
  if (level === "medium") return 1;
  if (level === "unknown") return 2;
  if (level === "ok") return 3;
  return 4;
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
    [...new Set(alerts.map((item) => item.tenant).filter(Boolean))].sort(),
    "All tenants"
  );
}

function renderSummary(summary) {
  document.getElementById("summary-critical").textContent = summary.critical || 0;
  document.getElementById("summary-medium").textContent = summary.medium || 0;
  document.getElementById("summary-unknown").textContent = summary.unknown || 0;
  document.getElementById("summary-ok").textContent = summary.ok || 0;
  document.getElementById("summary-total").textContent = summary.total || 0;
}

function caseButtonLabel(selectedCount) {
  if (selectedCount === 0) return "Select devices";
  return `Add case to ${selectedCount} device${selectedCount === 1 ? "" : "s"}`;
}

function syncCaseSelection(form) {
  const boxes = Array.from(form.querySelectorAll(".case-device-checkbox"));
  const selected = boxes.filter((box) => box.checked).length;
  const selectAll = form.querySelector(".case-select-all");
  if (selectAll) {
    selectAll.checked = selected === boxes.length;
    selectAll.indeterminate = selected > 0 && selected < boxes.length;
  }
  const button = form.querySelector(".case-save-btn");
  button.textContent = caseButtonLabel(selected);
  button.disabled = selected === 0;
}

function mapActionCell(item) {
  const hasCoordinates = Number.isFinite(item.latitude) && Number.isFinite(item.longitude);
  const mapLink = hasCoordinates
    ? `<a class="map-link" href="/?location_id=${encodeURIComponent(item.id)}">Open map</a>`
    : '<span class="map-link-disabled">No coordinates</span>';
  const downDevices = Array.isArray(item.down_devices) ? item.down_devices : [];
  const siteLabel = escHtml(item.name || item.id || "site");
  // One case often covers several devices (e.g. a whole site down), so every
  // down device gets a checkbox; all start selected.
  const deviceChoices = downDevices
    .filter((d) => d.device_id)
    .map((d) => `
        <label class="case-device-option">
          <input type="checkbox" class="case-device-checkbox" value="${escHtml(d.device_id)}" checked />
          <span>${escHtml(d.device_name || d.device_id)}</span>
        </label>`)
    .join("");
  const selectAll = downDevices.length > 1 ? `
        <label class="case-device-option case-select-all-option">
          <input type="checkbox" class="case-select-all" checked />
          <span>All down devices (${downDevices.length})</span>
        </label>` : "";
  const caseForm = deviceChoices ? `
      <div class="case-form">
        <fieldset class="case-devices">
          <legend>Devices for this case<span class="visually-hidden"> at ${siteLabel}</span></legend>
          ${selectAll}
          <div class="case-device-list">${deviceChoices}</div>
        </fieldset>
        <input class="case-input" data-site-id="${escHtml(item.id)}" type="text" placeholder="Case #" aria-label="Case number for ${siteLabel}" />
        <button class="case-save-btn" type="button" data-site-id="${escHtml(item.id)}">${caseButtonLabel(downDevices.filter((d) => d.device_id).length)}</button>
      </div>
    ` : "";
  return `
    <div class="action-stack">
      ${mapLink}
      ${caseForm}
      <button class="history-btn" type="button" data-site-id="${escHtml(item.id)}">History</button>
    </div>
  `;
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

function renderAlertHistory(siteId, instances) {
  if (!historyPanel || !historyTitle || !historyContent) return;
  historyTitle.textContent = `History · ${siteId}`;
  if (!instances.length) {
    historyContent.innerHTML = `<div class="history-instance"><div class="history-line">No incidents found.</div></div>`;
  } else {
    historyContent.innerHTML = instances.map((instance) => {
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
  historyPanel.classList.remove("hidden");
  historyPanel.setAttribute("aria-hidden", "false");
  if (historyCloseBtn) {
    historyCloseBtn.focus();
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

function formatBoardStatus(payload, visibleCount) {
  const checkedAt = payload.checked_at ? new Date(payload.checked_at) : null;
  const timestamp = checkedAt && !Number.isNaN(checkedAt.valueOf())
    ? checkedAt.toLocaleString()
    : "unknown";
  const staleText = payload.stale ? "stale" : "fresh";
  const syncText = payload.sync_pending ? " · inventory sync in progress…" : "";
  boardStatus.textContent = `${visibleCount} site${visibleCount !== 1 ? "s" : ""} shown · last checked ${timestamp} · ${staleText}${syncText}`;
}

function alertBadge(level) {
  const label = level ? level.toUpperCase() : "UNKNOWN";
  return `<span class="alert-badge alert-${escHtml(level || "unknown")}">${escHtml(label)}</span>`;
}

function formatLocationAddress(item) {
  return item.physical_address || item.facility || "";
}

function compareText(left, right) {
  return (left || "").localeCompare(right || "");
}

function isSiteExpanded(item) {
  const siteId = String(item.id || "");
  return allSitesExpanded ? !expandedSiteIds.has(siteId) : expandedSiteIds.has(siteId);
}

function renderDownDeviceRows(item, isExpanded) {
  const downDevices = Array.isArray(item.down_devices) ? item.down_devices : [];
  if (!downDevices.length) return "";
  const siteLabel = escHtml(item.name || item.id || "site");
  return downDevices.map((device) => `
    <tr class="down-device-row${isExpanded ? "" : " hidden"}">
      <td class="down-device-cell" aria-label="Down device for ${siteLabel}">
        <div class="down-device-name"><span class="visually-hidden">Down device for ${siteLabel}: </span>↳ ${escHtml(device.device_name || device.device_id || "Unknown device")}${device.device_ip ? ` <span class="device-ip">${escHtml(device.device_ip)}</span>` : ""}</div>
        <div class="site-meta">${[device.role, device.status].filter(Boolean).map(escHtml).join(" · ") || "Down device"}</div>
      </td>
      <td>${alertBadge(item.alert_level)}</td>
      <td>${escHtml(device.status || item.status || "—")}</td>
      <td>${escHtml(item.location_type || "—")}</td>
      <td>${escHtml(item.tenant || "—")}</td>
      <td>—</td>
      <td>—</td>
      <td>—</td>
      <td class="cases-cell">${renderDeviceCases(device)}</td>
      <td class="reason-cell">${escHtml(item.alert_reason || "Down device")}</td>
      <td></td>
    </tr>
  `).join("");
}

function renderTableRows(alerts, payload) {
  if (!alerts.length) {
    let emptyText = "No sites match the current filters.";
    if (payload.persistence_configured === false) {
      emptyText = "The alert board needs a PostgreSQL database. Set NAUTOBOT_MAPS_DATABASE_URL "
        + "and restart the app. The map works without it.";
    } else if (payload.sync_pending && !allAlerts.length) {
      emptyText = "Inventory sync in progress – the board will update automatically.";
    }
    alertsTableBody.innerHTML = `<tr><td colspan="11" class="empty-state">${emptyText}</td></tr>`;
    formatBoardStatus(payload, 0);
    return;
  }

  alertsTableBody.innerHTML = alerts.map((item) => {
    const address = formatLocationAddress(item);
    const isExpanded = isSiteExpanded(item);
    const downCount = item.down_device_count || 0;
    const toggleButton = downCount
      ? `<button class="site-toggle-btn" type="button" data-site-id="${escHtml(item.id || "")}" aria-expanded="${isExpanded ? "true" : "false"}" aria-label="${isExpanded ? "Collapse" : "Expand"} ${escHtml(item.name || item.id || "site")}">${isExpanded ? "▾" : "▸"}</button>`
      : '<span class="site-toggle-spacer" aria-hidden="true"></span>';
    const siteMeta = [
      item.country && address && address.toLowerCase().endsWith(item.country.toLowerCase()) ? "" : item.country,
      item.parent,
      item.tenant_group,
    ].filter(Boolean).map(escHtml).join(" · ");

    return `
    <tr class="site-row">
      <td>
        <div class="site-name-row">
          ${toggleButton}
          <div>
            <div class="site-name">${escHtml(item.name)}</div>
            <div class="site-summary">${downCount} down · ${item.device_count || 0} monitored</div>
          </div>
        </div>
        ${address ? `<div class="site-address">${escHtml(address)}</div>` : ""}
        <div class="site-meta">${siteMeta || "—"}</div>
      </td>
      <td>${alertBadge(item.alert_level)}</td>
      <td>${escHtml(item.status || "—")}</td>
      <td>${escHtml(item.location_type || "—")}</td>
      <td>${escHtml(item.tenant || "—")}</td>
      <td>${item.device_count || 0}</td>
      <td>${item.down_device_count || 0}</td>
      <td>${formatDuration(item.current_downtime_seconds || 0)}</td>
      <td class="cases-cell">${renderCases(item)}</td>
      <td class="reason-cell">${escHtml(item.alert_reason || "No active alert")}</td>
      <td>${mapActionCell(item)}</td>
    </tr>
    ${renderDownDeviceRows(item, isExpanded)}
  `;
  }).join("");
  formatBoardStatus(payload, alerts.length);
}

function getFilteredAlerts() {
  const siteNeedle = filterSite.value.trim().toLowerCase();
  const severity = filterSeverity.value;
  const status = filterStatus.value;
  const type = filterType.value;
  const tenant = filterTenant.value;
  const sort = sortBy.value;

  const filtered = allAlerts.filter((item) => {
    const searchableText = [
      item.name,
      item.parent,
      formatLocationAddress(item),
      item.country,
    ].join(" ").toLowerCase();
    if (siteNeedle && !searchableText.includes(siteNeedle)) return false;
    if (severity && item.alert_level !== severity) return false;
    if (status && item.status !== status) return false;
    if (type && item.location_type !== type) return false;
    if (tenant && item.tenant !== tenant) return false;
    return true;
  });

  filtered.sort((a, b) => {
    if (sort === "site") return compareText(a.name, b.name);
    if (sort === "address") return compareText(formatLocationAddress(a), formatLocationAddress(b)) || compareText(a.name, b.name);
    if (sort === "country") return compareText(a.country, b.country) || compareText(formatLocationAddress(a), formatLocationAddress(b)) || compareText(a.name, b.name);
    if (sort === "down") return (b.down_device_count || 0) - (a.down_device_count || 0) || compareText(a.name, b.name);
    if (sort === "devices") return (b.device_count || 0) - (a.device_count || 0) || compareText(a.name, b.name);
    return severityWeight(a.alert_level) - severityWeight(b.alert_level)
      || (b.down_device_count || 0) - (a.down_device_count || 0)
      || compareText(a.name, b.name);
  });

  return filtered;
}

function applyFilters(payload) {
  const filtered = getFilteredAlerts();
  renderTableRows(filtered, payload);
  syncQuickSeverityButtons();
}

function syncQuickSeverityButtons() {
  quickSeverityButtons.forEach((button) => {
    const isActive = (button.dataset.quickSeverity || "") === filterSeverity.value;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
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
    if (!resp.ok || payload.error) {
      throw new Error(payload.error || resp.statusText || `HTTP ${resp.status}`);
    }
    latestPayload = payload;
    allAlerts = payload.alerts || [];
    populateFilters(allAlerts);
    renderSummary(payload.summary || {});
    applyFilters(payload);
    scheduleSyncPoll(payload);
    setNextUpdate(payload);
  } catch (err) {
    stopSyncPolling();
    alertsTableBody.innerHTML = `<tr><td colspan="11" class="empty-state">Could not load alerts: ${escHtml(err.message)}</td></tr>`;
    boardStatus.textContent = "Alert board unavailable";
    showError(`Failed to load alert board: ${err.message}`);
  } finally {
    refreshBtn.disabled = false;
    if (toggleNonOperational) toggleNonOperational.disabled = false;
    if (collapseAllSitesBtn) collapseAllSitesBtn.disabled = false;
    if (expandAllSitesBtn) expandAllSitesBtn.disabled = false;
  }
}

function showError(message) {
  const toast = document.getElementById("error-toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(showError._timer);
  showError._timer = setTimeout(() => toast.classList.add("hidden"), 6000);
}

[filterSite, filterSeverity, filterStatus, filterType, filterTenant, sortBy].forEach((element) => {
  element.addEventListener(element.tagName === "INPUT" ? "input" : "change", () => {
    applyFilters(latestPayload);
  });
});

quickSeverityButtons.forEach((button) => {
  button.addEventListener("click", () => {
    if (refreshBtn.disabled) return;
    filterSeverity.value = button.dataset.quickSeverity || "";
    filterSeverity.dispatchEvent(new Event("change"));
  });
});

if (clearAlertFiltersBtn) {
  clearAlertFiltersBtn.addEventListener("click", () => {
    if (refreshBtn.disabled) return;
    filterSite.value = "";
    filterSeverity.value = "";
    filterStatus.value = "";
    filterType.value = "";
    filterTenant.value = "";
    sortBy.value = "severity";
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
setInterval(() => renderNextUpdate(), 1000);
// Browsers slow timers in background tabs; catch up as soon as the tab is visible.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) renderNextUpdate();
});
if (historyCloseBtn && historyPanel) {
  historyCloseBtn.addEventListener("click", () => {
    historyPanel.classList.add("hidden");
    historyPanel.setAttribute("aria-hidden", "true");
    if (historyTriggerBtn) {
      historyTriggerBtn.focus();
    }
  });
}

alertsTableBody.addEventListener("change", (event) => {
  const form = event.target.closest(".case-form");
  if (!form) return;
  if (event.target.classList.contains("case-select-all")) {
    form.querySelectorAll(".case-device-checkbox").forEach((box) => {
      box.checked = event.target.checked;
    });
  }
  if (event.target.matches(".case-select-all, .case-device-checkbox")) syncCaseSelection(form);
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

  const caseBtn = event.target.closest(".case-save-btn");
  if (caseBtn) {
    const siteId = caseBtn.dataset.siteId;
    const row = caseBtn.closest(".action-stack");
    const caseInput = row.querySelector(".case-input");
    const deviceIds = Array.from(row.querySelectorAll(".case-device-checkbox:checked")).map((box) => box.value);
    const deviceNameById = Object.fromEntries(
      Array.from(row.querySelectorAll(".case-device-checkbox")).map((box) => [box.value, box.nextElementSibling.textContent]),
    );
    const caseNumber = (caseInput?.value || "").trim();
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
      if (caseInput) caseInput.value = "";
      // The server drops its cached board when a case is added; a plain reload
      // is enough and must not trigger a full inventory sync.
      await loadAlertBoard(false);
    } catch (err) {
      showError(`Failed to add case: ${err.message}`);
    } finally {
      caseBtn.disabled = false;
    }
    return;
  }

  const historyBtn = event.target.closest(".history-btn");
  if (historyBtn) {
    const siteId = historyBtn.dataset.siteId;
    if (!siteId) return;
    historyBtn.disabled = true;
    historyTriggerBtn = historyBtn;
    try {
      const resp = await fetch(`/api/alert-history?site_id=${encodeURIComponent(siteId)}`);
      const payload = await readJsonResponse(resp);
      if (!resp.ok || payload.error) throw new Error(payload.error || `HTTP ${resp.status}`);
      renderAlertHistory(siteId, Array.isArray(payload.instances) ? payload.instances : []);
    } catch (err) {
      showError(`Failed to load history: ${err.message}`);
    } finally {
      historyBtn.disabled = false;
    }
  }
});

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
