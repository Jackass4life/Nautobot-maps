// The Maintenance page (#343): what is in maintenance now, what is planned,
// what ended lately, as batches; and planning more for many sites at once.
// The entry module: the only one that touches the page when loaded.
import { formatClock } from "../lib/format.js";
import { escHtml, revealTenantRest } from "../lib/html.js";
import { readJsonResponse } from "../lib/http.js";
import { initThemeToggle } from "../lib/theme.js";
import { initInfoTips } from "../lib/tooltip.js";
import { defaultPlan, initWhen, localDay, whenFieldsetHtml } from "../lib/when.js";
import { batchMatches, batchTenants, batchesOf, groupsHtml } from "./list.js";
import { chosenHtml, devicesHtml, matchSites, matchesHtml, planRequest } from "./plan.js";

// How often the list refreshes by itself; windows start and end on their own.
const REFRESH_MS = 60 * 1000;
// A second click within this long ends a batch of more than one (no dialogs here).
const CONFIRM_MS = 4000;

const $ = (id) => document.getElementById(id);
const els = {
  groups: $("maint-groups"), search: $("maint-search"), tenant: $("maint-tenant"), status: $("maint-status"),
  planOpen: $("maint-plan-open"), plan: $("maint-plan"), form: $("maint-plan-form"), close: $("maint-plan-cancel"),
  siteSearch: $("maint-site-search"), matches: $("maint-site-matches"), chosen: $("maint-chosen"), when: $("maint-when"),
};
const step = Number(els.groups.dataset.limit) || 25;
const state = {
  batches: [], descriptions: {}, limits: {}, expanded: new Set(), pastOpen: false,
  sites: null, byId: new Map(), chosen: [], showAllChosen: false, readWhen: null,
};

initThemeToggle($("theme-toggle"));
initInfoTips();

function setStatus(text, isError = false) {
  els.status.textContent = text;
  els.status.classList.toggle("is-error", isError);
}

async function getJson(url, options) {
  const resp = await fetch(url, { cache: "no-store", ...options });
  const payload = await readJsonResponse(resp);
  if (!resp.ok || payload.error) throw new Error(payload.error || `HTTP ${resp.status}`);
  return payload;
}

// --- the list ---------------------------------------------------------------

function render() {
  const criteria = { query: els.search.value, tenant: els.tenant.value };
  const shown = state.batches.filter((batch) => batchMatches(batch, criteria));
  els.groups.innerHTML = groupsHtml(shown, {
    limits: state.limits, expanded: state.expanded, descriptions: state.descriptions, step, pastOpen: state.pastOpen,
  });
}

function fillTenants() {
  const tenants = [...new Set(state.batches.flatMap(batchTenants))].sort();
  const current = els.tenant.value;
  els.tenant.innerHTML = '<option value="">All tenants</option>'
    + tenants.map((name) => `<option value="${escHtml(name)}"${name === current ? " selected" : ""}>${escHtml(name)}</option>`).join("");
}

async function loadList() {
  try {
    const payload = await getJson("/api/maintenance?recent_days=7");
    state.batches = batchesOf(payload.windows || []);
    state.descriptions = payload.tenant_descriptions || {};
    fillTenants();
    render();
    const count = (group) => state.batches.filter((batch) => batch.group === group).length;
    setStatus(`${count("active")} active · ${count("upcoming")} planned · updated ${formatClock(Date.now())}`);
  } catch (err) {
    setStatus(`Could not load maintenance: ${err.message}`, true);
  }
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

function addSites(ids) {
  for (const id of ids) if (!state.chosen.includes(id)) state.chosen.push(id);
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
  if (!state.readWhen) {
    const now = new Date();
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "your time zone";
    els.when.innerHTML = whenFieldsetHtml(defaultPlan(now), zone);
    state.readWhen = initWhen(els.when.querySelector(".when-fieldset"), defaultPlan(now), localDay(now));
  }
  els.siteSearch.focus();
  if (state.sites) return;
  els.siteSearch.placeholder = "Loading sites…";
  try {
    state.sites = (await getJson("/api/maintenance/sites")).sites || [];
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
    if (first && !els.matches.hidden) addSites(first.dataset.ids.split("\n"));
  }
});
els.matches.addEventListener("click", (event) => {
  const match = event.target.closest(".maint-match");
  if (match) addSites(match.dataset.ids.split("\n"));
});
els.matches.addEventListener("keydown", (event) => {
  const match = event.target.closest(".maint-match");
  if (!match) return;
  if (event.key === "Enter") {
    event.preventDefault();
    addSites(match.dataset.ids.split("\n"));
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
  if (event.target.closest(".maint-chips-all")) state.showAllChosen = true;
  if (event.target.closest(".maint-chips-clear")) state.chosen = [];
  if (remove || event.target.closest(".maint-chips-all, .maint-chips-clear")) renderChosen();
});

els.form.addEventListener("change", (event) => {
  if (event.target.name === "maint-scope") updateScope();
  const planned = els.form.querySelector('input[name="maint-when"]:checked')?.value === "planned";
  els.form.querySelector(".maint-planned").hidden = !planned;
  els.form.querySelector(".maint-save-btn").textContent = planned ? "Plan maintenance" : "Start maintenance";
});

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = planRequest({
    siteIds: state.chosen,
    scope: els.form.querySelector('input[name="maint-scope"]:checked')?.value,
    deviceIds: Array.from(els.form.querySelectorAll(".maint-device:checked")).map((box) => box.value),
    reason: els.form.querySelector(".maint-reason").value,
    ...state.readWhen(),
  });
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

renderChosen();
loadList();
setInterval(() => {
  // Not while a batch waits for its second click: the redraw would drop it.
  if (!els.groups.querySelector(".is-confirm")) loadList();
}, REFRESH_MS);
