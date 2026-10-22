// A tenant's status page (#291).  The key from a share link's "#key=" goes
// in the Authorization header; the fragment is never sent to the server.
// Public: every file this imports is in auth.STATUS_PAGE_ASSETS.
import { formatFeedTime } from "../lib/format.js";
import { escHtml } from "../lib/html.js";

const STATE_LABELS = {
  operational: "Operational",
  degraded: "Degraded",
  outage: "Outage",
  maintenance: "Planned maintenance",
  unknown: "No data",
};

export function headline(data) {
  const { sites, affected } = data.summary;
  if (!affected) return sites === 1 ? "Operational" : `All ${sites} sites operational`;
  return `${affected} of ${sites} site${sites === 1 ? "" : "s"} affected`;
}

// "since 14:05" today, else "since 3 Oct 14:05" (the viewer's time zone).
export function siteLine(site, now) {
  const label = STATE_LABELS[site.state] || STATE_LABELS.unknown;
  let when = "";
  if (site.since) when = `since ${formatFeedTime(site.since, now)}`;
  else if (site.until) when = `until ${formatFeedTime(site.until, now)}`;
  return `<li class="status-site status-${escHtml(site.state)}">
    <span class="status-dot" aria-hidden="true"></span>
    <span class="status-name">${escHtml(site.name)}</span>
    <span class="status-state">${escHtml(label)}${when ? ` <span class="status-when">${escHtml(when)}</span>` : ""}</span>
  </li>`;
}

// Up to this many sites, every one is listed open; above, the operational
// ones fold into one line, so a large tenant sees what is wrong first (#407).
export const FOLD_ABOVE = 10;

// {attention, operational}: the sites to list first (outage, degraded,
// maintenance, no data, worst first as the API sorts them) and the folded
// operational line, as HTML.  *open*: the fold as the viewer left it.
export function sitesHtml(sites, now, open = false) {
  const line = (site) => siteLine(site, now);
  if (sites.length <= FOLD_ABOVE) return { attention: sites.map(line).join(""), operational: "" };
  const attention = sites.filter((site) => site.state !== "operational");
  const operational = sites.filter((site) => site.state === "operational");
  if (!operational.length) return { attention: attention.map(line).join(""), operational: "" };
  const count = operational.length;
  return {
    attention: attention.map(line).join(""),
    operational: `<details class="status-fold"${open ? " open" : ""}>
    <summary>${count.toLocaleString("en")} site${count === 1 ? "" : "s"} operational</summary>
    <ul class="status-sites">${operational.map(line).join("")}</ul>
  </details>`,
  };
}

// Whether the viewer opened the operational fold: kept across the refresh.
let operationalOpen = false;

export function statusKey(hash) {
  return new URLSearchParams(String(hash || "").replace(/^#/, "")).get("key") || "";
}

export function errorText(code) {
  if (code === 401 || code === 403) return "This link doesn't work (anymore). Ask your contact for a new one.";
  if (code === 404) return "No sites found.";
  return "Status is unavailable right now. Retrying…";
}

// Load and show the status; whether to try again later (not for a refused link).
export async function refresh() {
  const tenant = document.body.dataset.tenant || "";
  const key = statusKey(window.location.hash);
  const headlineEl = document.getElementById("status-headline");
  try {
    const response = await fetch(`/api/status/${encodeURIComponent(tenant)}`, {
      headers: key ? { Authorization: `Bearer ${key}` } : {},
      cache: "no-store",
    });
    if (!response.ok) {
      headlineEl.textContent = errorText(response.status);
      headlineEl.className = "status-headline status-error";
      document.getElementById("status-sites").innerHTML = "";
      document.getElementById("status-operational").innerHTML = "";
      return response.status >= 500;
    }
    const data = await response.json();
    const now = new Date();
    document.getElementById("status-tenant").textContent = data.tenant;
    document.title = `${data.tenant} · Status`;
    headlineEl.textContent = headline(data);
    headlineEl.className = `status-headline${data.summary.affected ? " status-affected" : " status-ok"}`;
    const { attention, operational } = sitesHtml(data.sites, now, operationalOpen);
    document.getElementById("status-sites").innerHTML = attention;
    const fold = document.getElementById("status-operational");
    fold.innerHTML = operational;
    fold.querySelector("details")?.addEventListener("toggle", (event) => {
      operationalOpen = event.target.open;
    });
    document.getElementById("status-updated").textContent = `Updated ${formatFeedTime(data.checked_at, now)}`;
  } catch (_err) {
    headlineEl.textContent = errorText(0);
    headlineEl.className = "status-headline status-error";
  }
  return true;
}
