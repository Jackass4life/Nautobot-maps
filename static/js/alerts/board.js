// Loading and drawing the alert board (#294).
import { formatCountdown } from "../lib/format.js";
import { escHtml } from "../lib/html.js";
import { getJson } from "../lib/http.js";
import { SCOPE_NO_MATCH_HINT } from "../lib/scope.js";
import { showError } from "../lib/toast.js";
import { loadFeed } from "./feed.js";
import {
  currentWallLink,
  filtersInUse,
  getFilteredAlerts,
  isSiteExpanded,
  populateFilters,
  renderMoreFiltersLabel,
} from "./filters.js";
import { fullWidthRow, hasActiveAlarm, siteRowsHtml, summaryText } from "./rows.js";
import { inScope, scopeLabel, scopeSummary } from "./scope.js";
import { ALARM_LEVELS, config, els, state } from "./state.js";
import { announceNewAlarms, renderWallStatus, showWallError } from "./wall.js";

// Board loads can overlap (countdown, sync polling, the wall's minute
// reload); only the newest one may change the page.
let loadSeq = 0;

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

export function renderSummary(summary) {
  const alarms = summary.non_ok ?? ALARM_LEVELS.reduce((sum, level) => sum + (summary[level] || 0), 0);
  if (els.summaryLine) {
    els.summaryLine.textContent = summaryText(summary);
    els.summaryLine.classList.toggle("has-alarms", alarms > 0);
  }
  els.summaryAlarms.textContent = alarms;
  els.summaryNoData.textContent = summary.no_data || 0;
  els.summaryMaintenance.textContent = summary.maintenance || 0;
  els.summaryTotal.textContent = summary.total || 0;
}

export function formatBoardStatus(payload, visibleCount, alarmsOnly = state.severityFilter === "alarms") {
  // One short line (#311): how many, and when; "stale" only when it is.
  const checkedAt = payload.checked_at ? new Date(payload.checked_at) : null;
  const time = checkedAt && !Number.isNaN(checkedAt.valueOf())
    ? checkedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "unknown";
  const staleText = payload.stale ? " · data may be out of date" : "";
  const syncText = payload.sync_pending ? " · inventory sync in progress…" : "";
  const scopeText = alarmsOnly ? " with alarms" : "";
  els.boardStatus.textContent = `${visibleCount} site${visibleCount !== 1 ? "s" : ""}${scopeText} · updated ${time}${staleText}${syncText}`;
  els.boardStatus.classList.toggle("board-stale", Boolean(payload.stale));
}

// Why the board is empty: no database, the first sync, nothing in alarm, or the filters.
export function emptyBoardText(payload) {
  if (payload.persistence_configured === false) {
    return {
      text: "The alert board needs a PostgreSQL database. Set NAUTOBOT_MAPS_DATABASE_URL "
        + "and restart the app. The map works without it.",
      className: "empty-state",
    };
  }
  if (payload.sync_pending && !state.allAlerts.length) {
    return { text: "Inventory sync in progress – the board will update automatically.", className: "empty-state" };
  }
  // A link whose tenant= / q= matches nothing must not look like an all-clear (#364).
  if (config.scope && !state.allAlerts.length && (payload.alerts || []).length) {
    return {
      text: `No site matches ${state.scopeLabel}. ${SCOPE_NO_MATCH_HINT}`,
      className: "empty-state scope-no-match",
    };
  }
  if (state.severityFilter === "alarms" && !state.allAlerts.some(hasActiveAlarm)) {
    return {
      text: config.wallView
        ? `No active alarms${config.scope ? ` for ${state.scopeLabel}` : ""}.`
        : "No site has an active alarm. Choose All sites to see every site.",
      className: "empty-state all-clear",
    };
  }
  return { text: "No sites match the current filters.", className: "empty-state" };
}

export function renderTableRows(alerts, payload) {
  if (!alerts.length) {
    const { text, className } = emptyBoardText(payload);
    // Plain text, and it can carry a link's ?q= (#364): escaped.
    els.alertsTableBody.innerHTML = fullWidthRow(escHtml(text), className);
    formatBoardStatus(payload, 0);
    return;
  }
  els.alertsTableBody.innerHTML = siteRowsHtml(alerts, { wallView: config.wallView, isExpanded: isSiteExpanded });
  formatBoardStatus(payload, alerts.length);
}

export function applyFilters(payload) {
  const filtered = getFilteredAlerts();
  renderTableRows(filtered, payload);
  renderMoreFiltersLabel();
  if (els.clearAlertFiltersBtn) els.clearAlertFiltersBtn.hidden = !filtersInUse();
  if (els.copyWallLinkBtn) els.copyWallLinkBtn.hidden = config.wallView || !currentWallLink();
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

export function setNextUpdate(payload, now = Date.now()) {
  const seconds = payload.next_update_in_seconds;
  nextUpdateDueAt = typeof seconds === "number" && seconds >= 0 ? now + seconds * 1000 : null;
  renderNextUpdate(now);
}

export function renderNextUpdate(now = Date.now()) {
  const nextUpdateEl = els.nextUpdateEl;
  if (!nextUpdateEl) return;
  if (state.latestPayload.sync_pending) {
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
  if (els.refreshBtn.disabled || syncPollTimer || now - lastAutoUpdateAt < AUTO_UPDATE_MIN_GAP_MS) return;
  lastAutoUpdateAt = now;
  loadAlertBoard(false, { background: true });
}

export async function loadAlertBoard(forceRefresh = false, { background = false } = {}) {
  loadSeq += 1;
  const seq = loadSeq;
  if (!background) {
    // A user-initiated load restarts the polling budget.
    stopSyncPolling();
    syncPollAttempts = 0;
    els.boardStatus.textContent = "Loading alert board…";
    els.refreshBtn.disabled = true;
    if (els.toggleNonOperational) els.toggleNonOperational.disabled = true;
  }
  try {
    const params = new URLSearchParams();
    // The server only accepts 1/true/yes/refresh; a timestamp was silently ignored (#121).
    if (forceRefresh) params.set("refresh", "1");
    if (els.toggleNonOperational?.checked) params.set("include_non_operational", "1");
    const query = params.toString();
    const payload = await getJson(`/api/alerts${query ? `?${query}` : ""}`);
    if (seq !== loadSeq) return; // A newer load started meanwhile.
    state.latestPayload = payload;
    // Only the sites a link's ?tenant= / ?q= asks for (#364).
    state.allAlerts = (payload.alerts || []).filter((item) => inScope(item, config.scope));
    state.lastLoadedAt = Date.now();
    if (els.wallErrorEl) els.wallErrorEl.hidden = true;
    populateFilters(state.allAlerts);
    if (config.scope) {
      state.scopeLabel = scopeLabel(config.scope, state.allAlerts);
      if (els.boardScopeLabel) els.boardScopeLabel.textContent = state.scopeLabel;
      // The feed is for the same sites (#369).
      if (els.feedTitle) els.feedTitle.textContent = `Activity · ${state.scopeLabel}`;
    }
    renderSummary(config.scope ? scopeSummary(state.allAlerts) : payload.summary || {});
    applyFilters(payload);
    if (config.wallView) announceNewAlarms(state.allAlerts);
    scheduleSyncPoll(payload);
    setNextUpdate(payload);
    // The feed refreshes with the board (#180).
    loadFeed();
    renderWallStatus();
  } catch (err) {
    if (seq !== loadSeq) return;
    stopSyncPolling();
    if (config.wallView) {
      // Keep the last good board on screen (even an empty one), under a
      // banner that says it is old.
      showWallError(err.message);
      if (state.lastLoadedAt !== null) return;
    }
    els.alertsTableBody.innerHTML = fullWidthRow(`Could not load alerts: ${escHtml(err.message)}`, "empty-state");
    els.boardStatus.textContent = "Alert board unavailable";
    showError(`Failed to load alert board: ${err.message}`);
  } finally {
    // An older load must not re-enable the controls while a newer one runs.
    if (seq === loadSeq) {
      els.refreshBtn.disabled = false;
      if (els.toggleNonOperational) els.toggleNonOperational.disabled = false;
    }
  }
}
