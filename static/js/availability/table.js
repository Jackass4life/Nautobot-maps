// The availability tables (#342): sort by a column, search, a tenant filter,
// and the worst N first with "+N more", so 1,200 sites stay one screen.
// The rows are rendered on the server (they work without script); this only
// orders and hides them.  No page access at import.

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
    availability: Number(availability),
    downtime: Number(downtime),
    incidents: Number(incidents || 0),
    sites: Number(sites || 0),
    tenants: tenantList,
    search: [name, ...tenantList].join(" ").toLowerCase(),
  };
}

// The rows to show, in order: {shown, rest, matched}.  Ties by name.
export function viewRows(rows, { query = "", tenant = "", key = "availability", dir = "asc", limit = Infinity } = {}) {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const matched = rows.filter((row) => words.every((word) => row.search.includes(word))
    && (!tenant || row.tenants.includes(tenant)));
  const sign = dir === "desc" ? -1 : 1;
  matched.sort((a, b) => {
    const order = key === "name" ? a.name.localeCompare(b.name) : a[key] - b[key];
    return order * sign || a.name.localeCompare(b.name);
  });
  return { shown: matched.slice(0, limit), rest: matched.slice(limit), matched: matched.length };
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

export function initAvailabilityTable(section) {
  const tbody = section.querySelector("tbody");
  const rows = Array.from(tbody.querySelectorAll("tr[data-name]")).map(rowFrom);
  if (!rows.length) return;
  const more = section.querySelector(".avail-more");
  const search = section.querySelector(".avail-search");
  const tenant = section.querySelector(".avail-tenant");
  const step = Number(section.dataset.limit) || 25;
  const state = { key: "availability", dir: "asc", limit: step };
  section.querySelector(".avail-filters")?.removeAttribute("hidden");

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
      render();
    });
  }

  function render() {
    const view = viewRows(rows, {
      query: search?.value || "", tenant: tenant?.value || "", key: state.key, dir: state.dir, limit: state.limit,
    });
    for (const row of rows) row.el.hidden = true;
    for (const row of view.shown) {
      row.el.hidden = false;
      tbody.appendChild(row.el);
    }
    for (const th of headers) {
      if (th.dataset.sort === state.key) th.setAttribute("aria-sort", state.dir === "asc" ? "ascending" : "descending");
      else th.removeAttribute("aria-sort");
    }
    const text = moreText(view, rows.length);
    more.hidden = !text;
    more.textContent = text;
    if (view.rest.length) {
      for (const [label, limit] of [[`Show ${Math.min(step, view.rest.length)} more`, state.limit + step], ["Show all", Infinity]]) {
        if (label === "Show all" && view.rest.length <= step) continue;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "avail-more-btn";
        button.textContent = label;
        button.addEventListener("click", () => {
          state.limit = limit;
          render();
        });
        more.append(" ", button);
      }
    }
  }

  // A new search or tenant starts again from the worst N.
  for (const input of [search, tenant]) {
    input?.addEventListener("input", () => {
      state.limit = step;
      render();
    });
  }
  render();
}
