"""The availability tables: sort, search, tenant, the worst N first (#342)."""

from tests.js_modules import run_js


def test_rows_sort_search_and_limit():
    run_js("""
import { moreText, rowFrom, viewRows } from "@js/availability/table.js";

const tr = (name, availability, downtime, incidents, tenants) => ({ dataset: {
  name, availability: String(availability), downtime: String(downtime), incidents: String(incidents),
  tenants: JSON.stringify(tenants),
} });
const rows = [
  tr("Oslo", 99.5, 432, 3, ["Acme", "Nordic Net"]),
  tr("Bergen", 100, 0, 0, ["Acme"]),
  tr("Aarhus", 97.25, 2000, 9, ["Nordic Net"]),
  tr("Bodø", 100, 0, 0, []),
].map(rowFrom);
const names = (view) => view.shown.map((row) => row.name).join();

// The worst first; ties by name.
check(names(viewRows(rows)) === "Aarhus,Oslo,Bergen,Bodø", names(viewRows(rows)));
check(names(viewRows(rows, { key: "downtime", dir: "desc" })) === "Aarhus,Oslo,Bergen,Bodø", "downtime");
check(names(viewRows(rows, { key: "name" })) === "Aarhus,Bergen,Bodø,Oslo", "name");
check(names(viewRows(rows, { key: "incidents", dir: "asc" })) === "Bergen,Bodø,Oslo,Aarhus", "incidents asc");

// Search: the site or a tenant, every word, any case.
check(names(viewRows(rows, { query: "nordic" })) === "Aarhus,Oslo", "a tenant");
check(names(viewRows(rows, { query: "OS acme" })) === "Oslo", "every word");
check(names(viewRows(rows, { tenant: "Acme" })) === "Oslo,Bergen", "tenant filter: exact");
check(viewRows(rows, { tenant: "Acm" }).matched === 0, "not a prefix");

// The worst N, the rest counted.
const view = viewRows(rows, { limit: 2 });
check(names(view) === "Aarhus,Oslo" && view.rest.length === 2 && view.matched === 4, names(view));
check(moreText(view, 4) === "Showing 2 of 4 · all of the rest with no downtime", moreText(view, 4));
const one = viewRows(rows, { limit: 1 });
check(moreText(one, 4) === "Showing 1 of 4 · 2 of the rest with no downtime", moreText(one, 4));
check(moreText(viewRows(rows, { key: "name", limit: 1 }), 4) === "Showing 1 of 4 · 2 of the rest with no downtime", "name order");
check(moreText(viewRows(rows), 4) === "", "all shown: no line");
check(moreText(viewRows(rows, { query: "nordic" }), 4) === "2 of 4", "a search shows its count");
check(moreText(viewRows(rows, { query: "zzz" }), 4) === "Nothing matches.", "nothing");

// Thousands, as on the cards.
const many = Array.from({ length: 1200 }, (_, i) => rowFrom(tr(`Site ${i}`, i < 60 ? 99 : 100, i < 60 ? 600 : 0, 1, [])));
check(moreText(viewRows(many, { limit: 25 }), 1200) === "Showing 25 of 1,200 · 1,140 of the rest with no downtime",
  moreText(viewRows(many, { limit: 25 }), 1200));

// Bad tenants JSON: no tenants, not an error.
check(rowFrom({ dataset: { name: "X", availability: "1", downtime: "0", tenants: "[oops" } }).tenants.length === 0, "bad json");
""")
