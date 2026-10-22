// The top bar on every staff page (#392, #419): the theme icon; the menu of
// pages and the status details, which close on a press outside them (also on
// the map's controls, which keep clicks to themselves) or Escape; how fresh
// the data is; and a site search.  Called once by each page.
import { formatFeedTime } from "./format.js";
import { escHtml } from "./html.js";
import { getJson } from "./http.js";
import { initThemeToggle } from "./theme.js";
import { matchSites } from "../map/sitesearch.js";

// Once a minute, from what the scheduler records: nothing upstream (#419).
export const APP_STATUS_REFRESH_MS = 60 * 1000;

function initPopovers() {
  const popovers = Array.from(document.querySelectorAll(".app-popover"));
  document.addEventListener("pointerdown", (event) => {
    popovers.forEach((popover) => {
      if (popover.open && !popover.contains(event.target)) popover.open = false;
    });
  }, true);
  // First (capture): Escape closes the open one, not also the site panel behind it.
  document.addEventListener("keydown", (event) => {
    const open = popovers.find((popover) => popover.open);
    if (event.key !== "Escape" || !open) return;
    open.open = false;
    open.querySelector("summary")?.focus();
    event.stopPropagation();
  }, true);
}

// The bar's line and its details from /api/app-status; *status* null: the app
// didn't answer.  The worst source speaks: "Can't reach LibreNMS".
export function appStatusView(status, now = new Date()) {
  if (!status) {
    return {
      state: "down",
      text: "Can't reach Nautobot Maps",
      detail: '<p>This page can\'t reach the app: what you see may be out of date. It tries again every minute.</p>',
    };
  }
  const sources = status.sources || [];
  const worst = sources.find((source) => source.state === "down") || sources.find((source) => source.state === "stale");
  const updated = status.updated_at ? formatFeedTime(status.updated_at, now) : "";
  const text = worst ? worst.message : updated ? `Updated ${updated}` : "Up to date";
  const intro = status.state === "ok"
    ? "The map and board are up to date."
    : "The map and board show the last good data: alarms may be missing or out of date.";
  // "Nautobot · synced 14:05"; a problem in its own words, and when it was last good.
  const lines = sources.map((source) => {
    const time = source.last_success ? formatFeedTime(source.last_success, now) : "";
    const [what, when] = source.state === "ok"
      ? [source.name, time ? `synced ${time}` : ""]
      : [source.message, time ? `last good ${time}` : ""];
    return `<p class="app-status-source app-status-${escHtml(source.state)}">`
      + `<span>${escHtml(what)}</span>`
      + `${when ? `<span class="app-status-when">${escHtml(when)}</span>` : ""}</p>`;
  });
  return { state: status.state || "ok", text, detail: `<p>${intro}</p>${lines.join("")}` };
}

function initAppStatus() {
  const box = document.getElementById("app-status");
  if (!box) return;
  const text = document.getElementById("app-status-text");
  const detail = document.getElementById("app-status-detail");
  async function refresh() {
    let status;
    try {
      status = await getJson("/api/app-status");
    } catch (_err) {
      status = null;
    }
    const view = appStatusView(status);
    box.classList.remove("ok", "stale", "down");
    box.classList.add(view.state);
    text.textContent = view.text;
    box.querySelector("summary").setAttribute("aria-label", `Data: ${view.text}`);
    detail.innerHTML = view.detail;
    box.hidden = false;
  }
  refresh();
  // A tab in the background doesn't ask; it catches up when shown again.
  setInterval(() => {
    if (!document.hidden) refresh();
  }, APP_STATUS_REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
}

// One row: the site (name, path) opens it on the map; "Board" shows it on the board.
export function barSearchHtml({ matches, more }) {
  if (!matches.length) return '<li class="bar-search-note">No site matches</li>';
  const rows = matches.map((loc) => {
    const name = loc.name || loc.id;
    const path = loc.ancestor_path || loc.physical_address || "";
    return `<li class="bar-hit">`
      + `<a class="bar-hit-map" href="/?location_id=${encodeURIComponent(loc.id)}" title="Show on the map">`
      + `<span class="bar-hit-name">${escHtml(name)}</span>`
      + `${path ? `<span class="bar-hit-path">${escHtml(path)}</span>` : ""}</a>`
      + `<a class="bar-hit-board" href="/alerts?q=${encodeURIComponent(name)}" aria-label="${escHtml(`${name} on the board`)}">Board</a></li>`;
  });
  if (more) rows.push(`<li class="bar-search-note">${more.toLocaleString("en")} more: keep typing</li>`);
  return rows.join("");
}

// Not while typing, or with Ctrl, Cmd or Alt.
export function isSearchShortcut(event) {
  if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return false;
  const target = event.target;
  return !(target?.closest?.("input, textarea, select, [contenteditable]") || target?.isContentEditable);
}

function initBarSearch() {
  const input = document.getElementById("bar-search");
  if (!input) return;
  const bar = input.closest(".app-bar");
  const search = input.closest(".bar-search");
  const results = document.getElementById("bar-search-results");
  // The sites, fetched once, the first time the search is used.
  let sites = null;
  let loading = null;
  function load() {
    loading ??= getJson("/api/locations")
      .then((data) => { sites = data.locations || []; })
      .catch(() => { sites = null; loading = null; });
    return loading;
  }
  function close() {
    results.hidden = true;
    results.innerHTML = "";
    bar.classList.remove("search-open");
  }
  async function show() {
    const query = input.value;
    if (!query.trim()) {
      results.hidden = true;
      return;
    }
    if (!sites) {
      results.innerHTML = '<li class="bar-search-note">Loading the sites…</li>';
      results.hidden = false;
      await load();
      if (input.value !== query) return;
      if (!sites) {
        results.innerHTML = '<li class="bar-search-note">The sites could not be loaded: try again.</li>';
        return;
      }
    }
    results.innerHTML = barSearchHtml(matchSites(sites, query));
    results.hidden = false;
  }
  const links = () => Array.from(results.querySelectorAll(".bar-hit-map"));
  input.addEventListener("focus", load, { once: true });
  input.addEventListener("input", show);
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" && !results.hidden) {
      event.preventDefault();
      links()[0]?.focus();
    } else if (event.key === "Enter") {
      // The first site, as the map's search does.
      const first = links()[0];
      if (first) window.location.href = first.href;
    } else if (event.key === "Escape") {
      event.stopPropagation();
      input.value = "";
      close();
      input.blur();
    }
  });
  results.addEventListener("keydown", (event) => {
    const all = links();
    const at = all.indexOf(document.activeElement.closest?.(".bar-hit")?.querySelector(".bar-hit-map"));
    if (event.key === "ArrowDown" && at < all.length - 1) all[at + 1].focus();
    else if (event.key === "ArrowUp") (at > 0 ? all[at - 1] : input).focus();
    else if (event.key === "Escape") {
      event.stopPropagation();
      close();
      input.focus();
    } else return;
    event.preventDefault();
  });
  search.addEventListener("focusout", (event) => {
    if (!search.contains(event.relatedTarget)) close();
  });
  // A narrow screen: the icon opens the field over the bar.
  document.getElementById("bar-search-open")?.addEventListener("click", () => {
    bar.classList.add("search-open");
    input.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (!isSearchShortcut(event)) return;
    event.preventDefault();
    bar.classList.add("search-open");
    input.focus();
  });
}

export function initTopBar() {
  initThemeToggle(document.getElementById("theme-toggle"));
  initPopovers();
  initAppStatus();
  initBarSearch();
}
