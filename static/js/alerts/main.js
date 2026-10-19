// The alert board page (templates/alerts.html): finds the page's elements,
// wires the controls and loads the board.  The only module that looks at the
// page when it loads (#294).
import { setCaseUrlTemplate } from "../lib/cases.js";
import { revealTenantRest } from "../lib/html.js";
import { initTopBar } from "../lib/nav.js";
import { initInfoTips } from "../lib/tooltip.js";
import { showError } from "../lib/toast.js";
import { applyFilters, loadAlertBoard, renderNextUpdate, showScopeLabel } from "./board.js";
import { initCasePanel, openCasePanel, sendAck } from "./case-panel.js";
import { FEED_COLLAPSED_KEY, feedStartsCollapsed, loadFeed, rememberFeedCollapsed, setFeedCollapsed } from "./feed.js";
import { currentWallLink, filtersLeftOffTheWall, rememberSort, restoreSort, setSeverityFilter } from "./filters.js";
import { initMaintenancePanel, openMaintenancePanel } from "./maintenance.js";
import { COPIED_LABEL_MS, closeSidePanel, copySite, copyText, initPanels, openHistory, openPanel } from "./panels.js";
import { readScope, scopeLabel } from "./scope.js";
import { DEFAULT_SEVERITY_FILTER, DEFAULT_SORT, config, els, state } from "./state.js";
import { WALL_RELOAD_MS, initWallSound, renderWallStatus } from "./wall.js";

const byId = (id) => document.getElementById(id);

// First, as when it was its own script: its Escape runs before the panels'.
initInfoTips();

Object.assign(els, {
  boardStatus: byId("board-status"),
  alertsTableBody: byId("alerts-table-body"),
  filterSite: byId("filter-site"),
  filterStatus: byId("filter-status"),
  filterType: byId("filter-type"),
  filterTenant: byId("filter-tenant"),
  sortBy: byId("sort-by"),
  refreshBtn: byId("refresh-alerts"),
  severityFilterButtons: Array.from(document.querySelectorAll("[data-severity-filter]")),
  moreFiltersToggle: byId("more-filters-toggle"),
  moreFilters: byId("more-filters"),
  clearAlertFiltersBtn: byId("clear-alert-filters"),
  copyWallLinkBtn: byId("copy-wall-link"),
  feedTitle: byId("feed-title"),
  toggleNonOperational: byId("toggle-non-operational"),
  summaryLine: byId("summary-line-text"),
  summaryAlarms: byId("summary-alarms"),
  summaryNoData: byId("summary-no_data"),
  summaryMaintenance: byId("summary-maintenance"),
  summaryTotal: byId("summary-total"),
  historyPanel: byId("history-panel"),
  historyTitle: byId("history-title"),
  historyContent: byId("history-content"),
  historyCloseBtn: byId("history-close"),
  casePanel: byId("case-panel"),
  caseTitle: byId("case-title"),
  caseContent: byId("case-content"),
  caseCloseBtn: byId("case-close"),
  copyPanel: byId("copy-panel"),
  copyTextArea: byId("copy-text"),
  copyCloseBtn: byId("copy-close"),
  copyStatus: byId("copy-status"),
  maintPanel: byId("maint-panel"),
  maintTitle: byId("maint-title"),
  maintContent: byId("maint-content"),
  maintCloseBtn: byId("maint-close"),
  boardLayout: byId("board-layout"),
  feedPanel: byId("feed-panel"),
  feedToggle: byId("feed-toggle"),
  feedShowBtn: byId("feed-show"),
  feedList: byId("feed-list"),
  feedFilterButtons: Array.from(document.querySelectorAll("[data-feed-kind]")),
  nextUpdateEl: byId("next-update"),
  wallUpdatedEl: byId("wall-updated"),
  wallErrorEl: byId("wall-error"),
  wallSoundBtn: byId("wall-sound"),
});

// Settings from the server (templates/alerts.html).
const appConfig = byId("app-config")?.dataset || {};
config.nautobotUrl = appConfig.nautobotUrl || "";
config.wallView = document.body.classList.contains("wall-view");
// ?tenant= (repeatable) and ?q=: a board or wall for some sites only (#364).
config.scope = readScope(window.location.search);
if (config.scope) {
  // Spelt as typed until the sites are in; then as Nautobot has it (board.js).
  els.boardScopeLabel = byId("board-scope-label");
  if (els.boardScopeLabel) byId("board-scope").hidden = false;
  showScopeLabel(scopeLabel(config.scope));
}
// "https://itsm.example.com/ticket/{case}" (#289); "" when not set.
setCaseUrlTemplate(appConfig.caseUrlTemplate || "");

// Filters and sorting.
const { filterSite, filterStatus, filterType, filterTenant, sortBy, refreshBtn, toggleNonOperational } = els;
[filterSite, filterStatus, filterType, filterTenant, sortBy].forEach((element) => {
  element.addEventListener(element.tagName === "INPUT" ? "input" : "change", () => {
    applyFilters(state.latestPayload);
  });
});

sortBy.addEventListener("change", () => rememberSort(sortBy.value));

els.severityFilterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setSeverityFilter(button.dataset.severityFilter);
    applyFilters(state.latestPayload);
  });
});

if (els.moreFiltersToggle && els.moreFilters) {
  els.moreFiltersToggle.addEventListener("click", () => {
    const open = els.moreFilters.hidden;
    els.moreFilters.hidden = !open;
    els.moreFiltersToggle.setAttribute("aria-expanded", open ? "true" : "false");
  });
}

function resetExpandedSites() {
  state.expandedSiteIds = new Set();
  state.allSitesExpanded = false;
}

// Copy wall link (#366): the full address, for a screen's browser.
if (els.copyWallLinkBtn) {
  els.copyWallLinkBtn.addEventListener("click", async () => {
    const link = currentWallLink();
    if (!link) return;
    const copied = await copyText(new URL(link, window.location.origin).href);
    // Say what the wall won't filter by, rather than drop it silently.
    const leftOff = filtersLeftOffTheWall();
    const without = leftOff.length ? ` (without ${leftOff.join(" and ")})` : "";
    els.copyWallLinkBtn.textContent = copied ? `Copied${without}` : "Copy failed";
    if (els.copyStatus) {
      els.copyStatus.textContent = copied ? `Copied the wall link${without}` : "Could not copy the wall link";
    }
    setTimeout(() => {
      els.copyWallLinkBtn.textContent = "Copy wall link";
    }, COPIED_LABEL_MS);
  });
}

if (els.clearAlertFiltersBtn) {
  els.clearAlertFiltersBtn.addEventListener("click", () => {
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
      resetExpandedSites();
      loadAlertBoard(false);
      return;
    }
    applyFilters(state.latestPayload);
  });
}

if (toggleNonOperational) {
  toggleNonOperational.addEventListener("change", () => {
    if (refreshBtn.disabled) return;
    resetExpandedSites();
    loadAlertBoard(false);
  });
}

initWallSound();

refreshBtn.addEventListener("click", () => loadAlertBoard(true));
setInterval(() => {
  renderNextUpdate();
  renderWallStatus();
}, 1000);
if (config.wallView) setInterval(() => loadAlertBoard(false, { background: true }), WALL_RELOAD_MS);
// Browsers slow timers in background tabs; catch up as soon as the tab is visible.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) renderNextUpdate();
});

initPanels();
initMaintenancePanel();
initCasePanel();

// The row's "⋯" menu (#311): one open at a time; it closes when an action
// is chosen, on a click elsewhere, and with Escape (before any side panel).
function closeRowMenus(except) {
  document.querySelectorAll("details.row-menu[open]").forEach((menu) => {
    if (menu !== except) menu.open = false;
  });
}

// Capture phase: the menu closes and focus goes to its button before the
// chosen action runs, so a panel it opens can still take focus.
document.addEventListener(
  "click",
  (event) => {
    const menu = event.target.closest("details.row-menu");
    if (menu && event.target.closest(".row-menu-items button, .row-menu-items a")) {
      menu.open = false;
      menu.querySelector("summary")?.focus();
      return;
    }
    closeRowMenus(menu);
  },
  true,
);

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const openMenu = document.querySelector("details.row-menu[open]");
  if (openMenu) {
    event.preventDefault();
    openMenu.open = false;
    openMenu.querySelector("summary")?.focus();
    return;
  }
  const panel = openPanel();
  if (!panel) return;
  event.preventDefault();
  closeSidePanel(panel);
});

// The rows' buttons; rows are re-rendered on every update, so the listeners
// are on the table body.
els.alertsTableBody.addEventListener("click", revealTenantRest);

function toggleSite(siteId) {
  // With every site open, the set holds the ones the operator closed.
  if (state.expandedSiteIds.has(siteId)) state.expandedSiteIds.delete(siteId);
  else state.expandedSiteIds.add(siteId);
  applyFilters(state.latestPayload);
}

els.alertsTableBody.addEventListener("click", async (event) => {
  const toggleBtn = event.target.closest(".site-toggle-btn");
  if (toggleBtn) {
    const siteId = String(toggleBtn.dataset.siteId || "");
    if (siteId) toggleSite(siteId);
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
  if (historyBtn) await openHistory(historyBtn);
});

els.alertsTableBody.addEventListener("click", async (event) => {
  const button = event.target.closest(".ack-btn, .unack-btn");
  if (!button) return;
  button.disabled = true;
  try {
    await sendAck(button.dataset.siteId, [], "", button.classList.contains("unack-btn"));
  } catch (err) {
    showError(`Could not ${button.classList.contains("unack-btn") ? "remove the acknowledgement" : "acknowledge"}: ${err.message}`);
    button.disabled = false;
  }
});

// The activity feed (#180).
els.feedFilterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    button.setAttribute("aria-pressed", button.getAttribute("aria-pressed") === "true" ? "false" : "true");
    loadFeed();
  });
});

const { feedToggle, feedShowBtn } = els;
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
if (config.wallView) {
  // The same screen for everyone: newest down first, every site open, no feed.
  sortBy.value = DEFAULT_SORT;
  state.allSitesExpanded = true;
  setFeedCollapsed(true);
}
loadAlertBoard();
initTopBar();
