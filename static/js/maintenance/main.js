// The Maintenance page (#343): what is in maintenance now, what is planned,
// what ended lately, as batches; and planning more for many sites at once.
// ?view=history (#353): every window of a period, for one site or all, and its export.
// The entry module: the only one that touches the page when loaded.
import { addDays, formatRange } from "../lib/calendar.js";
import { formatClock, searchWords } from "../lib/format.js";
import { escHtml, revealTenantRest } from "../lib/html.js";
import { getJson } from "../lib/http.js";
import { devicesHtml, windowRequest } from "../lib/maintenance-form.js";
import { initPeriodPicker } from "../lib/period-picker.js";
import { initThemeToggle } from "../lib/theme.js";
import { initInfoTips } from "../lib/tooltip.js";
import { localDay, mountWhen } from "../lib/when.js";
import { batchMatches, batchesOf, groupsHtml, historyHtml } from "./list.js";
import { chosenHtml, matchSites, matchesHtml, prepareSites } from "./plan.js";

// How often the list refreshes by itself; windows start and end on their own.
const REFRESH_MS = 60 * 1000;
// A second click within this long ends a batch of more than one (no dialogs here).
const CONFIRM_MS = 4000;

const $ = (id) => document.getElementById(id);
const els = {
  groups: $("maint-groups"), search: $("maint-search"), tenant: $("maint-tenant"), status: $("maint-status"),
  planOpen: $("maint-plan-open"), plan: $("maint-plan"), form: $("maint-plan-form"), close: $("maint-plan-cancel"),
  siteSearch: $("maint-site-search"), matches: $("maint-site-matches"), chosen: $("maint-chosen"), when: $("maint-when"),
  historyBar: $("maint-history-bar"), siteFilter: $("maint-site-filter"), csv: $("maint-history-csv"),
};
const step = Number(els.groups.dataset.limit) || 25;
const recentDays = Number(els.groups.dataset.recentDays) || 7;
const maxSites = Number(els.groups.dataset.maxSites) || 200;
const state = {
  batches: [], descriptions: {}, limits: {}, expanded: new Set(), pastOpen: false,
  sites: null, byId: new Map(), chosen: [], showAllChosen: false, readWhen: null,
};

// The history's period and site, from the address (a link from the board or the
// map opens one site's); the last 30 days by default.
const params = new URLSearchParams(window.location.search);
const historyView = params.get("view") === "history";
// The browser's days: the server reads the period in this time zone.
const today = localDay(new Date());
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const historyDays = Number(els.historyBar.dataset.defaultDays) || 30;
const history = {
  from: params.get("from") || addDays(today, 1 - historyDays),
  to: params.get("to") || today,
  siteId: params.get("site_id") || "",
  siteName: "",
};

function historyQuery(extra = {}) {
  const query = { from: history.from, to: history.to, tz: zone, ...extra };
  if (history.siteId) query.site_id = history.siteId;
  return new URLSearchParams(query);
}

// The export: the same period, site and tenant (the search is the page's own);
// set at once and on each change, so it is right even if a load fails.
function updateCsvLink() {
  const tenant = els.tenant.value ? { tenant: els.tenant.value } : {};
  els.csv.href = `/api/maintenance/history.csv?${historyQuery(tenant)}`;
}

initThemeToggle($("theme-toggle"));
initInfoTips();

function setStatus(text, isError = false) {
  els.status.textContent = text;
  els.status.classList.toggle("is-error", isError);
}

// --- the list ---------------------------------------------------------------

// Which list button has the keyboard, to give it back after a redraw (the minute refresh).
function focusedButton() {
  const button = document.activeElement?.closest?.("#maint-groups button");
  if (!button) return null;
  const batch = button.closest(".maint-batch");
  return { key: batch?.dataset.key, ids: button.dataset.ids, group: button.dataset.group, className: button.className };
}

function restoreFocus(was) {
  if (!was) return;
  const scope = was.key ? els.groups.querySelector(`.maint-batch[data-key="${CSS.escape(was.key)}"]`) : els.groups;
  const same = Array.from(scope?.querySelectorAll("button") || [])
    .find((b) => b.className === was.className && b.dataset.ids === was.ids && b.dataset.group === was.group);
  same?.focus();
}

function render() {
  const was = focusedButton();
  const criteria = { words: searchWords(els.search.value), tenant: els.tenant.value };
  const shown = state.batches.filter((batch) => batchMatches(batch, criteria));
  els.groups.innerHTML = historyView
    ? historyHtml(shown, {
      limit: state.limits.history || step, expanded: state.expanded, descriptions: state.descriptions, step,
      title: `History · ${formatRange(history.from, history.to)}`,
    })
    : groupsHtml(shown, {
      limits: state.limits, expanded: state.expanded, descriptions: state.descriptions, step, pastOpen: state.pastOpen,
      days: recentDays,
    });
  restoreFocus(was);
}

function fillTenants() {
  const tenants = [...new Set(state.batches.flatMap((batch) => batch.tenants))].sort();
  const current = els.tenant.value;
  els.tenant.innerHTML = '<option value="">All tenants</option>'
    + tenants.map((name) => `<option value="${escHtml(name)}"${name === current ? " selected" : ""}>${escHtml(name)}</option>`).join("");
}

async function loadList() {
  try {
    const url = historyView ? `/api/maintenance/history?${historyQuery()}` : `/api/maintenance?recent_days=${recentDays}`;
    const payload = await getJson(url);
    state.batches = batchesOf(payload.windows || [], { keepOrder: historyView });
    state.descriptions = payload.tenant_descriptions || {};
    fillTenants();
    if (historyView) showSiteFilter(payload.windows?.[0]?.site_name);
    render();
    const cut = payload.truncated ? " · only the first windows are shown: choose a shorter period or a site" : "";
    if (historyView) {
      const windows = (payload.windows || []).length;
      setStatus(`${windows.toLocaleString("en-GB")} window${windows === 1 ? "" : "s"} · ${formatRange(history.from, history.to)}${cut}`);
      return;
    }
    const count = (group) => state.batches.filter((batch) => batch.group === group).length;
    setStatus(`${count("active")} active · ${count("upcoming")} planned · updated ${formatClock(Date.now())}${cut}`);
  } catch (err) {
    setStatus(`Could not load maintenance: ${err.message}`, true);
  }
}

// "Site: Aarhus ×" while the history is one site's; × shows every site's.
function showSiteFilter(name) {
  if (name) history.siteName = name;
  els.siteFilter.hidden = !history.siteId;
  if (!history.siteId) return;
  els.siteFilter.innerHTML = `<span>Site: ${escHtml(history.siteName || history.siteId)}</span>`
    + '<button type="button" class="maint-chip-remove" aria-label="Show every site">×</button>';
}

// The address keeps the period and site, so a history can be shared or bookmarked.
function historyChanged() {
  const query = historyQuery({ view: "history" });
  query.delete("tz");
  window.history.replaceState(null, "", `/maintenance?${query}`);
  updateCsvLink();
  state.limits = {};
  state.expanded.clear();
  loadList();
}

// End or cancel; a batch of more than one asks for a second click first.
async function endWindows(button) {
  const ids = button.dataset.ids.split(",").map(Number);
  if (ids.length > 1 && !button.dataset.confirm) {
    button.dataset.confirm = button.textContent;
    button.textContent = `Sure? ${button.textContent}`;
    button.classList.add("is-confirm");
    setTimeout(() => {
      if (!button.isConnected || !button.dataset.confirm) return;
      button.textContent = button.dataset.confirm;
      delete button.dataset.confirm;
      button.classList.remove("is-confirm");
    }, CONFIRM_MS);
    return;
  }
  button.disabled = true;
  try {
    await getJson("/api/maintenance/end", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }),
    });
    await loadList();
  } catch (err) {
    setStatus(`Could not end maintenance: ${err.message}`, true);
    button.disabled = false;
  }
}

els.groups.addEventListener("click", (event) => {
  revealTenantRest(event);
  const more = event.target.closest(".maint-more");
  if (more) {
    state.limits[more.dataset.group] = (state.limits[more.dataset.group] || step) + step;
    render();
    // By hand as well: Safari doesn't focus a clicked button, so render() has none to give back.
    els.groups.querySelector(`[data-group="${more.dataset.group}"] .maint-more`)?.focus();
    return;
  }
  const toggle = event.target.closest(".maint-expand");
  if (toggle) {
    const key = toggle.closest(".maint-batch").dataset.key;
    if (state.expanded.has(key)) state.expanded.delete(key);
    else state.expanded.add(key);
    render();
    els.groups.querySelector(`.maint-batch[data-key="${CSS.escape(key)}"] .maint-expand`)?.focus();
    return;
  }
  const end = event.target.closest(".maint-end");
  if (end) endWindows(end);
});

// Keep the Ended group as the operator left it across refreshes.
els.groups.addEventListener("toggle", (event) => {
  if (event.target.matches?.('details[data-group="past"]')) state.pastOpen = event.target.open;
}, true);

els.tenant.addEventListener("change", () => {
  if (historyView) updateCsvLink();
});
for (const input of [els.search, els.tenant]) {
  input.addEventListener("input", () => {
    state.limits = {};
    render();
  });
}

// --- planning -----------------------------------------------------------------

function renderChosen() {
  els.chosen.innerHTML = chosenHtml(state.chosen, state.byId, state.showAllChosen);
  const one = state.chosen.length === 1;
  const devicesRadio = els.form.querySelector('input[name="maint-scope"][value="devices"]');
  devicesRadio.disabled = !one;
  if (!one && devicesRadio.checked) els.form.querySelector('input[name="maint-scope"][value="site"]').checked = true;
  updateScope();
}

async function updateScope() {
  const box = els.form.querySelector(".maint-devices");
  const devices = els.form.querySelector('input[name="maint-scope"]:checked')?.value === "devices";
  box.hidden = !devices;
  if (!devices || box.dataset.siteId === state.chosen[0]) return;
  const siteId = state.chosen[0];
  box.dataset.siteId = siteId;
  box.innerHTML = '<p class="maint-empty">Loading devices…</p>';
  try {
    const payload = await getJson(`/api/maintenance/devices?${new URLSearchParams({ site_id: siteId })}`);
    if (box.dataset.siteId === siteId) box.innerHTML = devicesHtml(payload.devices || []);
  } catch (err) {
    box.innerHTML = `<p class="maint-error">Could not load devices: ${escHtml(err.message)}</p>`;
  }
}

// A match (a site, or all of a tenant's) into the chosen sites.
function addMatch(match) {
  for (const id of match.dataset.ids.split("\n")) if (!state.chosen.includes(id)) state.chosen.push(id);
  els.siteSearch.value = "";
  els.matches.hidden = true;
  renderChosen();
  els.siteSearch.focus();
}

function showMatches() {
  if (!state.sites || !els.siteSearch.value.trim()) {
    els.matches.hidden = true;
    return;
  }
  els.matches.innerHTML = matchesHtml(matchSites(state.sites, els.siteSearch.value, new Set(state.chosen)));
  els.matches.hidden = false;
}

async function openPlan() {
  els.plan.hidden = false;
  els.planOpen.setAttribute("aria-expanded", "true");
  state.readWhen ||= mountWhen(els.when, {
    onModeChange: (planned) => {
      els.form.querySelector(".maint-save-btn").textContent = planned ? "Plan maintenance" : "Start maintenance";
    },
  });
  els.siteSearch.focus();
  if (state.sites) return;
  els.siteSearch.placeholder = "Loading sites…";
  try {
    state.sites = prepareSites((await getJson("/api/maintenance/sites")).sites || []);
    state.byId = new Map(state.sites.map((site) => [site.id, site]));
    els.siteSearch.placeholder = `Site, path or tenant (${state.sites.length.toLocaleString("en-GB")} sites)`;
    showMatches();
  } catch (err) {
    els.siteSearch.placeholder = "Site, path or tenant";
    showPlanError(`Could not load the sites: ${err.message}`);
  }
}

function closePlan() {
  els.plan.hidden = true;
  els.planOpen.setAttribute("aria-expanded", "false");
  els.planOpen.focus();
}

function showPlanError(message) {
  const box = els.form.querySelector(".maint-error");
  box.textContent = message;
  box.hidden = !message;
}

els.planOpen.addEventListener("click", () => (els.plan.hidden ? openPlan() : closePlan()));
els.close.addEventListener("click", closePlan);
els.siteSearch.addEventListener("input", showMatches);
els.siteSearch.addEventListener("keydown", (event) => {
  if (event.key === "Escape") els.matches.hidden = true;
  if (event.key === "ArrowDown") els.matches.querySelector(".maint-match")?.focus();
  if (event.key === "Enter") {
    event.preventDefault();
    const first = els.matches.querySelector(".maint-match");
    if (first && !els.matches.hidden) addMatch(first);
  }
});
els.matches.addEventListener("click", (event) => {
  const match = event.target.closest(".maint-match");
  if (match) addMatch(match);
});
els.matches.addEventListener("keydown", (event) => {
  const match = event.target.closest(".maint-match");
  if (!match) return;
  if (event.key === "Enter") {
    event.preventDefault();
    addMatch(match);
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const next = event.key === "ArrowDown" ? match.nextElementSibling : match.previousElementSibling;
    (next?.matches(".maint-match") ? next : event.key === "ArrowUp" ? els.siteSearch : match).focus();
  } else if (event.key === "Escape") {
    els.matches.hidden = true;
    els.siteSearch.focus();
  }
});
els.chosen.addEventListener("click", (event) => {
  const remove = event.target.closest(".maint-chip-remove");
  if (remove) state.chosen = state.chosen.filter((id) => id !== remove.dataset.id);
  else if (event.target.closest(".maint-chips-all")) state.showAllChosen = true;
  else if (event.target.closest(".maint-chips-clear")) state.chosen = [];
  else return;
  renderChosen();
});

// "Some devices" shows the devices; "When" shows its own (mountWhen).
els.form.addEventListener("change", (event) => {
  if (event.target.name === "maint-scope") updateScope();
});

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = windowRequest({
    siteIds: state.chosen,
    scope: els.form.querySelector('input[name="maint-scope"]:checked')?.value,
    deviceIds: Array.from(els.form.querySelectorAll(".maint-device:checked")).map((box) => box.value),
    reason: els.form.querySelector(".maint-reason").value,
    ...state.readWhen(),
  }, maxSites);
  if (body.error) {
    showPlanError(body.error);
    return;
  }
  showPlanError("");
  const button = els.form.querySelector(".maint-save-btn");
  button.disabled = true;
  try {
    const { windows } = await getJson("/api/maintenance", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    state.chosen = [];
    state.showAllChosen = false;
    els.form.querySelector(".maint-reason").value = "";
    renderChosen();
    await loadList();
    setStatus(`Saved: ${windows.length} window${windows.length === 1 ? "" : "s"}. ${els.status.textContent}`);
  } catch (err) {
    showPlanError(`Could not save: ${err.message}`);
  } finally {
    button.disabled = false;
  }
});

if (historyView) {
  // The history only reads: no planning here, no minute refresh.
  els.planOpen.hidden = true;
  els.historyBar.hidden = false;
  updateCsvLink();
  initPeriodPicker(els.historyBar.querySelector(".period-field"), {
    from: history.from,
    to: history.to,
    max: today,
    today,
    onPick(from, to) {
      Object.assign(history, { from, to });
      historyChanged();
    },
  });
  els.siteFilter.addEventListener("click", (event) => {
    if (!event.target.closest(".maint-chip-remove")) return;
    history.siteId = "";
    historyChanged();
  });
}

renderChosen();
loadList();
setInterval(() => {
  if (historyView) return;
  // Not in a hidden tab (it catches up when shown), nor while a batch waits
  // for its second click: the redraw would drop it.
  if (!document.hidden && !els.groups.querySelector(".is-confirm")) loadList();
}, REFRESH_MS);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadList();
});
