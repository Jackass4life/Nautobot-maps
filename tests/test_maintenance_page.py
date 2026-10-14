"""The Maintenance page: batches, groups, the site picker and the request (#343)."""

from tests.js_modules import run_js

WINDOWS = """
const w = (id, site, state, extra = {}) => ({
  id, site_id: site, site_name: site.toUpperCase(), site_path: "EMEA", device_id: "", device_name: "",
  tenants: ["Acme"], state, reason: "Core upgrade", created_by: "olga",
  created_at: "2026-10-14T10:00:00Z", starts_at: "2026-10-14T10:00:00Z", ends_at: "2026-10-14T16:00:00Z", ...extra,
});
// One change across 30 sites, one device window, one planned, one ended.
const batch30 = Array.from({ length: 30 }, (_, i) => w(i + 1, `s${String(i).padStart(2, "0")}`, "active"));
const device = w(40, "oslo", "active", { device_id: "d1", device_name: "core<1>", reason: "PSU", created_at: "2026-10-14T11:00:00Z",
  ends_at: "2026-10-14T12:00:00Z", tenants: ["Nordic"] });
const planned = w(50, "bergen", "upcoming", { reason: "Fibre", created_at: "2026-10-13T09:00:00Z",
  starts_at: "2026-10-15T20:00:00Z", ends_at: "2026-10-16T02:00:00Z", created_by: "ivan" });
const ended = w(60, "aarhus", "ended", { created_at: "2026-10-12T09:00:00Z", ended_at: "2026-10-12T12:00:00Z" });
const windows = [...batch30, device, planned, ended];
"""


def test_batches_and_groups():
    run_js(
        """
import { batchesOf, batchMatches, batchTitle, groupsHtml, whenText } from "@js/maintenance/list.js";
"""
        + WINDOWS
        + """
const now = new Date("2026-10-14T13:00:00Z");
const batches = batchesOf(windows);
check(batches.length === 4, `four batches: ${batches.length}`);
// Active: ending soonest first (the device at 12:00, then the 30 sites at 16:00).
check(batches.map((b) => b.group).join() === "active,active,upcoming,past", batches.map((b) => b.group).join());
check(batchTitle(batches[0]) === "OSLO · core<1>" && batchTitle(batches[1]) === "30 sites", batchTitle(batches[1]));
check(whenText(batches[1], now).startsWith("until ") && whenText(batches[3], now).startsWith("ended "), "when");
check(whenText({ ...batches[3], state: "cancelled" }, now) === "cancelled", "cancelled");

// Search every word over reason, person, sites, devices and tenants; a tenant exactly.
check(batchMatches(batches[0], { query: "psu core<1>" }) && !batchMatches(batches[1], { query: "psu" }), "words");
check(batchMatches(batches[2], { query: "IVAN" }) && batchMatches(batches[1], { query: "s17" }), "person and site");
check(batchMatches(batches[0], { tenant: "Nordic" }) && !batchMatches(batches[1], { tenant: "Nordic" }), "tenant");

// The 30-site batch is one line: one "End all 30", names as a preview, not 30 rows.
let html = groupsHtml(batches, { limits: {}, expanded: new Set(), descriptions: { Acme: 'Bank <"VIP">' }, now });
check((html.match(/class="maint-batch"/g) || []).length === 4, "four lines");
check(html.includes(`data-ids="${batch30.map((x) => x.id).join(",")}">End all 30</button>`), "end all");
check(html.includes(">S00, S01, S02 +27<") && html.includes(">Show 30</button>"), "preview");
check(html.includes("<strong>OSLO · core&lt;1&gt;</strong>") && !html.includes("core<1>"), "escaped");
check(html.includes('data-ids="50">Cancel</button>') && html.includes('data-ids="40">End now</button>'), "cancel vs end");
check(html.includes('data-info-tip="Bank &lt;&quot;VIP&quot;&gt;"'), "tenant (i)");
check(html.includes("Active now <span class=\\"avail-count\\">2 · 31 sites</span>"), "group counts");
// Ended is folded, without End buttons.
const past = html.slice(html.indexOf('<details class="table-shell maint-group" data-group="past">'));
check(!past.startsWith("<details class=\\"table-shell maint-group\\" data-group=\\"past\\" open") && !past.includes("maint-end"), "past");
check(groupsHtml(batches, { limits: {}, expanded: new Set(), descriptions: {}, now, pastOpen: true }).includes('data-group="past" open>'), "kept open");

// Opened: every site with its own End.
html = groupsHtml(batches, { limits: {}, expanded: new Set([batches[1].key]), descriptions: {}, now });
check((html.match(/class="maint-batch-window"/g) || []).length === 30 && html.includes('data-ids="17">End now</button>'), "expanded");

// Groups first, whatever the input order (review on #343); the Ended title follows the days.
const mixed = batchesOf([ended, planned, ...batch30, device]);
check(mixed.map((b) => b.group).join() === "active,active,upcoming,past", mixed.map((b) => b.group).join());
check(groupsHtml(mixed, { limits: {}, expanded: new Set(), descriptions: {}, now, days: 14 }).includes("Ended in the last 14 days"), "days");
// A batch partly cancelled and partly ended: two lines, each right.
const split = batchesOf([
  w(70, "a", "cancelled", { created_at: "2026-10-12T09:00:00Z", ended_at: "2026-10-12T08:00:00Z", starts_at: "2026-10-12T10:00:00Z" }),
  w(71, "b", "ended", { created_at: "2026-10-12T09:00:00Z", ended_at: "2026-10-12T11:00:00Z", starts_at: "2026-10-12T10:00:00Z" }),
  w(72, "c", "ended", { created_at: "2026-10-12T09:00:00Z", ended_at: "2026-10-12T12:30:00Z", starts_at: "2026-10-12T10:00:00Z" }),
]);
check(split.length === 2 && split.map((b) => b.state).sort().join() === "cancelled,ended", JSON.stringify(split.map((b) => b.state)));
check(split.find((b) => b.state === "ended").ended_at === "2026-10-12T12:30:00Z", "the latest end");

// Hundreds of batches: the first 25, then "Show 25 more".
const many = batchesOf(Array.from({ length: 300 }, (_, i) => w(1000 + i, `x${i}`, "upcoming",
  { created_at: `2026-10-14T10:${String(i % 60).padStart(2, "0")}:00Z`, reason: `job ${i}` })));
html = groupsHtml(many, { limits: {}, expanded: new Set(), descriptions: {}, now });
check((html.match(/class="maint-batch"/g) || []).length === 25 && html.includes("275 more <button"), "limited");
html = groupsHtml(many, { limits: { upcoming: 50 }, expanded: new Set(), descriptions: {}, now });
check((html.match(/class="maint-batch"/g) || []).length === 50, "more");
check(groupsHtml([], { limits: {}, expanded: new Set(), descriptions: {}, now }).includes("Nothing planned."), "empty");
"""
    )


def test_site_picker_and_request():
    run_js(
        """
import { MAX_MATCHES, chosenHtml, matchSites, matchesHtml, planRequest } from "@js/maintenance/plan.js";

// 1,200 sites; Acme has 40 of them.
const sites = Array.from({ length: 1200 }, (_, i) => ({
  id: `s${i}`, name: `Site ${String(i).padStart(4, "0")} ${["Oslo", "Bergen", "Aarhus"][i % 3]}`, path: "EMEA › NOR",
  tenants: i % 30 === 0 ? ["Acme Corp"] : [`Tenant ${i % 7}`], devices: 8, maintenance: i === 3,
}));
let match = matchSites(sites, "oslo");
check(match.sites.length === MAX_MATCHES && match.more === 400 - MAX_MATCHES, JSON.stringify(match.more));
match = matchSites(sites, "acme");
check(match.tenants.length === 1 && match.tenants[0].name === "Acme Corp" && match.tenants[0].ids.length === 40, "a tenant's sites");
// Chosen ones are not offered again; every word counts.
match = matchSites(sites, "site 0003", new Set());
check(match.sites.map((s) => s.id).join() === "s3", JSON.stringify(match.sites));
check(matchSites(sites, "site 0003", new Set(["s3"])).sites.length === 0, "chosen");
check(matchSites(sites, "  ").sites.length === 0, "nothing typed");
let html = matchesHtml(matchSites(sites, "acme"));
check(html.includes(">Add all 40 sites of <strong>Acme Corp</strong></li>") && html.includes('data-ids="s0\\ns30\\n'), "add all");
check(matchesHtml(matchSites(sites, "site 0003")).includes("in maintenance now"), "already in maintenance");
check(matchesHtml({ sites: [], tenants: [], more: 0 }).includes("No site matches."), "none");

// Chips: the first 12, "+N more", Clear.
const byId = new Map(sites.map((s) => [s.id, s]));
const ids = sites.slice(0, 40).map((s) => s.id);
html = chosenHtml(ids, byId);
check((html.match(/class="maint-chip"/g) || []).length === 12 && html.includes(">+28 more<") && html.includes(">Clear<"), "chips");
check((chosenHtml(ids, byId, true).match(/class="maint-chip"/g) || []).length === 40, "all");

// The request: many sites at once; devices for one site.
const when = { when: "now", duration: "120" };
let body = planRequest({ siteIds: ids, scope: "site", deviceIds: [], reason: " Core ", ...when });
check(body.site_ids.length === 40 && body.reason === "Core" && body.duration_minutes === 120 && !("site_id" in body), JSON.stringify(body).slice(0, 80));
body = planRequest({ siteIds: ["s1"], scope: "devices", deviceIds: ["d1"], reason: "PSU", ...when });
check(body.site_id === "s1" && body.device_ids[0] === "d1" && !("site_ids" in body), JSON.stringify(body));
check(planRequest({ siteIds: [], scope: "site", deviceIds: [], reason: "x", ...when }).error === "Choose at least one site.", "no site");
check(planRequest({ siteIds: ids, scope: "site", deviceIds: [], reason: " ", ...when }).error === "Enter a reason.", "reason");
check(planRequest({ siteIds: ["a", "b"], scope: "devices", deviceIds: ["d"], reason: "x", ...when }).error.includes("one site"), "devices");
check(planRequest({ siteIds: sites.slice(0, 201).map((s) => s.id), scope: "site", deviceIds: [], reason: "x", ...when }).error.includes("200"), "limit");
check(planRequest({ siteIds: ids, scope: "site", deviceIds: [], reason: "x", ...when }, 30).error === "At most 30 sites at once.", "the page's limit");
"""
    )
