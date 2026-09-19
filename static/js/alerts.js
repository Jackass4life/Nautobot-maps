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

let allAlerts = [];
let latestPayload = { checked_at: null, stale: false, summary: {}, alerts: [] };

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

function mapActionCell(item) {
  const hasCoordinates = Number.isFinite(item.latitude) && Number.isFinite(item.longitude);
  const mapLink = hasCoordinates
    ? `<a class="map-link" href="/?location_id=${encodeURIComponent(item.id)}">Open map</a>`
    : '<span class="map-link-disabled">No coordinates</span>';
  const downDevices = Array.isArray(item.down_devices) ? item.down_devices : [];
  if (!downDevices.length) {
    return mapLink;
  }
  const options = downDevices
    .map((d) => `<option value="${escHtml(d.device_id || "")}">${escHtml(d.device_name || d.device_id || "Unknown")}</option>`)
    .join("");
  return `
    <div class="action-stack">
      ${mapLink}
      <div class="case-form">
        <select class="case-device-select" data-site-id="${escHtml(item.id)}">${options}</select>
        <input class="case-input" data-site-id="${escHtml(item.id)}" type="text" placeholder="Case #" />
        <button class="case-save-btn" type="button" data-site-id="${escHtml(item.id)}">Add case</button>
      </div>
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

function renderCases(item) {
  const cases = Array.isArray(item.active_cases) ? item.active_cases : [];
  if (!cases.length) return "—";
  return cases.map((value) => `<span class="case-pill">${escHtml(value)}</span>`).join("");
}

function formatBoardStatus(payload, visibleCount) {
  const checkedAt = payload.checked_at ? new Date(payload.checked_at) : null;
  const timestamp = checkedAt && !Number.isNaN(checkedAt.valueOf())
    ? checkedAt.toLocaleString()
    : "unknown";
  const staleText = payload.stale ? "stale" : "fresh";
  boardStatus.textContent = `${visibleCount} site${visibleCount !== 1 ? "s" : ""} shown · last checked ${timestamp} · ${staleText}`;
}

function alertBadge(level) {
  const label = level ? level.toUpperCase() : "UNKNOWN";
  return `<span class="alert-badge alert-${escHtml(level || "unknown")}">${escHtml(label)}</span>`;
}

function renderTableRows(alerts, payload) {
  if (!alerts.length) {
    alertsTableBody.innerHTML = '<tr><td colspan="11" class="empty-state">No sites match the current filters.</td></tr>';
    formatBoardStatus(payload, 0);
    return;
  }

  alertsTableBody.innerHTML = alerts.map((item) => `
    <tr>
      <td>
        <div class="site-name">${escHtml(item.name)}</div>
        <div class="site-meta">${[item.parent, item.tenant_group].filter(Boolean).map(escHtml).join(" · ") || "—"}</div>
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
  `).join("");
  formatBoardStatus(payload, alerts.length);
}

function applyFilters(payload) {
  const siteNeedle = filterSite.value.trim().toLowerCase();
  const severity = filterSeverity.value;
  const status = filterStatus.value;
  const type = filterType.value;
  const tenant = filterTenant.value;
  const sort = sortBy.value;

  const filtered = allAlerts.filter((item) => {
    if (siteNeedle && !(`${item.name} ${item.parent} ${item.facility}`.toLowerCase().includes(siteNeedle))) return false;
    if (severity && item.alert_level !== severity) return false;
    if (status && item.status !== status) return false;
    if (type && item.location_type !== type) return false;
    if (tenant && item.tenant !== tenant) return false;
    return true;
  });

  filtered.sort((a, b) => {
    if (sort === "site") return a.name.localeCompare(b.name);
    if (sort === "down") return (b.down_device_count || 0) - (a.down_device_count || 0) || a.name.localeCompare(b.name);
    if (sort === "devices") return (b.device_count || 0) - (a.device_count || 0) || a.name.localeCompare(b.name);
    return severityWeight(a.alert_level) - severityWeight(b.alert_level)
      || (b.down_device_count || 0) - (a.down_device_count || 0)
      || a.name.localeCompare(b.name);
  });

  renderTableRows(filtered, payload);
}

async function loadAlertBoard(forceRefresh = false) {
  boardStatus.textContent = "Loading alert board…";
  refreshBtn.disabled = true;
  try {
    const suffix = forceRefresh ? `?refresh=${Date.now()}` : "";
    const resp = await fetch(`/api/alerts${suffix}`);
    const payload = await resp.json();
    if (!resp.ok || payload.error) {
      throw new Error(payload.error || `HTTP ${resp.status}`);
    }
    latestPayload = payload;
    allAlerts = payload.alerts || [];
    populateFilters(allAlerts);
    renderSummary(payload.summary || {});
    applyFilters(payload);
  } catch (err) {
    alertsTableBody.innerHTML = `<tr><td colspan="11" class="empty-state">Could not load alerts: ${escHtml(err.message)}</td></tr>`;
    boardStatus.textContent = "Alert board unavailable";
    showError(`Failed to load alert board: ${err.message}`);
  } finally {
    refreshBtn.disabled = false;
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

refreshBtn.addEventListener("click", () => loadAlertBoard(true));

alertsTableBody.addEventListener("click", async (event) => {
  const caseBtn = event.target.closest(".case-save-btn");
  if (caseBtn) {
    const siteId = caseBtn.dataset.siteId;
    const row = caseBtn.closest(".action-stack");
    const deviceSelect = row.querySelector(".case-device-select");
    const caseInput = row.querySelector(".case-input");
    const deviceId = deviceSelect?.value || "";
    const caseNumber = (caseInput?.value || "").trim();
    if (!siteId || !deviceId || !caseNumber) {
      showError("Select a device and enter a case number first.");
      return;
    }
    caseBtn.disabled = true;
    try {
      const resp = await fetch("/api/alert-cases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site_id: siteId, device_id: deviceId, case_number: caseNumber }),
      });
      const payload = await resp.json();
      if (!resp.ok || payload.error) throw new Error(payload.error || `HTTP ${resp.status}`);
      if (caseInput) caseInput.value = "";
      await loadAlertBoard(true);
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
    try {
      const resp = await fetch(`/api/alert-history?site_id=${encodeURIComponent(siteId)}`);
      const payload = await resp.json();
      if (!resp.ok || payload.error) throw new Error(payload.error || `HTTP ${resp.status}`);
      const count = Array.isArray(payload.instances) ? payload.instances.length : 0;
      const open = (payload.instances || []).filter((row) => row.status === "open").length;
      showError(`History for ${siteId}: ${count} incidents (${open} open).`);
    } catch (err) {
      showError(`Failed to load history: ${err.message}`);
    } finally {
      historyBtn.disabled = false;
    }
  }
});

loadAlertBoard();
