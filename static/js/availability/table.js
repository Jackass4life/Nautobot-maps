// The availability tables (#342): sort by a column, search, a tenant filter,
// and the worst N first with "+N more", so 1,200 sites stay one screen.
// The rows are rendered on the server (they work without script); this only
// orders and hides them.  No page access at import.

import { hasAllWords, searchWords } from "../lib/format.js";
import { revealTenantRest } from "../lib/html.js";

// Which way a column sorts first: the worst first.
const FIRST_DIRECTION = { name: "asc", availability: "asc", downtime: "desc", incidents: "desc", sites: "desc" };

// One row's values from its data- attributes.
export function rowFrom(tr) {
  const { name = "", availability, downtime, incidents, sites, tenants } = tr.dataset;
  let tenantList;
  try {
    tenantList = tenants ? JSON.parse(tenants) : [];
  } catch {
    tenantList = [];
  }
  return {
    el: tr,
    name,
    // Lower case once: the server's order (Python's str.lower()), so rows don't move when the script starts.
    lname: name.toLowerCase(),
    availability: Number(availability),
    downtime: Number(downtime),
    incidents: Number(incidents || 0),
    sites: Number(sites || 0),
    tenants: tenantList,
    search: [name, ...tenantList].join(" ").toLowerCase(),
  };
}

function byName(a, b) {
  return a.lname < b.lname ? -1 : a.lname > b.lname ? 1 : 0;
}

// A sorted copy, by *key* and *dir*; ties by name.  Only when the column changes.
export function sortRows(rows, key = "availability", dir = "asc") {
  const sign = dir === "desc" ? -1 : 1;
  return [...rows].sort((a, b) => (key === "name" ? byName(a, b) : a[key] - b[key]) * sign || byName(a, b));
}

// The sorted rows a search and a tenant leave, in order: {shown, rest, matched}.
export function filterRows(sorted, { query = "", tenant = "", limit = Infinity } = {}) {
  const words = searchWords(query);
  const matched = sorted.filter((row) => hasAllWords(row.search, words)
    && (!tenant || row.tenants.includes(tenant)));
  return { shown: matched.slice(0, limit), rest: matched.slice(limit), matched: matched.length };
}

export function viewRows(rows, options = {}) {
  return filterRows(sortRows(rows, options.key, options.dir), options);
}

// The line under a table: "Showing 25 of 1,200 · 1,140 of the rest with no downtime".
export function moreText(view, total) {
  const count = (n) => n.toLocaleString("en-GB");
  if (!view.matched) return total ? "Nothing matches." : "";
  if (!view.rest.length) return view.matched < total ? `${count(view.matched)} of ${count(total)}` : "";
  const quiet = view.rest.filter((row) => !row.downtime).length;
  const tail = quiet === view.rest.length ? " · all of the rest with no downtime"
    : quiet ? ` · ${count(quiet)} of the rest with no downtime` : "";
  return `Showing ${count(view.shown.length)} of ${count(view.matched)}${tail}`;
}

// The site rows' (i) carry only the tenant's name; the description is in the
// section once (data-tenant-descriptions), so 1,200 rows don't repeat it.
export function fillTenantTips(root, descriptions) {
  for (const tip of root.querySelectorAll(".info-tip[data-tenant-tip]")) {
    const name = tip.dataset.tenantTip;
    const description = Object.hasOwn(descriptions, name) ? descriptions[name] : "";
    if (!description) continue;
    tip.dataset.infoTip = description;
    tip.setAttribute("aria-label", `${name}: ${description}`);
  }
}

export function initAvailabilityTable(section) {
  const tbody = section.querySelector("tbody");
  const rows = Array.from(tbody.querySelectorAll("tr[data-name]")).map(rowFrom);
  if (!rows.length) return;
  const more = section.querySelector(".avail-more");
  const search = section.querySelector(".avail-search");
  const tenant = section.querySelector(".avail-tenant");
  const step = Number(section.dataset.limit) || 25;
  const state = { key: "availability", dir: "asc", limit: step };
  let sorted = sortRows(rows, state.key, state.dir);
  // The server already shows the worst *step*, in this order.
  let shown = rows.filter((row) => !row.el.hidden);
  section.querySelector(".avail-filters")?.removeAttribute("hidden");
  try {
    fillTenantTips(tbody, JSON.parse(section.dataset.tenantDescriptions || "{}"));
  } catch {
    // No descriptions: no (i) text, the names still show.
  }

  // Sortable headers: a button in each, aria-sort on the column in use.
  const headers = Array.from(section.querySelectorAll("th[data-sort]"));
  for (const th of headers) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sort-btn";
    button.textContent = th.textContent;
    th.textContent = "";
    th.appendChild(button);
    button.addEventListener("click", () => {
      const key = th.dataset.sort;
      state.dir = state.key === key ? (state.dir === "asc" ? "desc" : "asc") : FIRST_DIRECTION[key] || "asc";
      state.key = key;
      sorted = sortRows(rows, state.key, state.dir);
      render();
    });
  }

  function addMore(label, limit) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "avail-more-btn";
    button.textContent = label;
    button.addEventListener("click", () => {
      state.limit = limit;
      render();
      // The line was redrawn: keep the keyboard here, not at the top of the page.
      (more.querySelector(".avail-more-btn") || section.querySelector("h2")).focus();
    });
    more.append(" ", button);
  }

  // Only rows that come or go are touched; the shown ones are moved only when their order changed.
  function render() {
    const view = filterRows(sorted, { query: search?.value || "", tenant: tenant?.value || "", limit: state.limit });
    const next = new Set(view.shown);
    for (const row of shown) if (!next.has(row)) row.el.hidden = true;
    if (view.shown.some((row, i) => shown[i] !== row)) {
      for (const row of view.shown) tbody.appendChild(row.el);
    }
    for (const row of view.shown) row.el.hidden = false;
    shown = view.shown;
    for (const th of headers) {
      if (th.dataset.sort === state.key) th.setAttribute("aria-sort", state.dir === "asc" ? "ascending" : "descending");
      else th.removeAttribute("aria-sort");
    }
    const text = moreText(view, rows.length);
    more.hidden = !text;
    more.textContent = text;
    if (view.rest.length) addMore(`Show ${Math.min(step, view.rest.length)} more`, state.limit + step);
    if (view.rest.length > step) addMore("Show all", Infinity);
  }

  // "+2" shows a site's other tenants in place (#279, as on the board).
  tbody.addEventListener("click", revealTenantRest);

  // A new search or tenant starts again from the worst N.
  for (const input of [search, tenant]) {
    input?.addEventListener("input", () => {
      state.limit = step;
      render();
    });
  }
  render();
}
