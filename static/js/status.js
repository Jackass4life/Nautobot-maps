"use strict";

// A tenant's status page (#291).  The key from a share link's "#key=" goes
// in the Authorization header; the fragment is never sent to the server.
const REFRESH_MS = 60 * 1000;
const STATE_LABELS = {
  operational: "Operational",
  degraded: "Degraded",
  outage: "Outage",
  maintenance: "Planned maintenance",
  unknown: "No data",
};

function escHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// "14:05" today, else "3 Oct 14:05" (the viewer's time zone).
function shortTime(iso, now) {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const time = at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (at.toDateString() === now.toDateString()) return time;
  return `${at.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}

function headline(data) {
  const { sites, affected } = data.summary;
  if (!affected) return sites === 1 ? "Operational" : `All ${sites} sites operational`;
  return `${affected} of ${sites} site${sites === 1 ? "" : "s"} affected`;
}

function siteLine(site, now) {
  const label = STATE_LABELS[site.state] || STATE_LABELS.unknown;
  let when = "";
  if (site.since) when = `since ${shortTime(site.since, now)}`;
  else if (site.until) when = `until ${shortTime(site.until, now)}`;
  return `<li class="status-site status-${escHtml(site.state)}">
    <span class="status-dot" aria-hidden="true"></span>
    <span class="status-name">${escHtml(site.name)}</span>
    <span class="status-state">${escHtml(label)}${when ? ` <span class="status-when">${escHtml(when)}</span>` : ""}</span>
  </li>`;
}

function statusKey(hash) {
  return new URLSearchParams(String(hash || "").replace(/^#/, "")).get("key") || "";
}

function errorText(code) {
  if (code === 401 || code === 403) return "This link doesn't work (anymore). Ask your contact for a new one.";
  if (code === 404) return "No sites found.";
  return "Status is unavailable right now. Retrying…";
}

function applyTheme() {
  const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
}

async function refresh() {
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
      return response.status >= 500;
    }
    const data = await response.json();
    const now = new Date();
    document.getElementById("status-tenant").textContent = data.tenant;
    document.title = `${data.tenant} · Status`;
    headlineEl.textContent = headline(data);
    headlineEl.className = `status-headline${data.summary.affected ? " status-affected" : " status-ok"}`;
    document.getElementById("status-sites").innerHTML = data.sites.map((site) => siteLine(site, now)).join("");
    document.getElementById("status-updated").textContent = `Updated ${shortTime(data.checked_at, now)}`;
  } catch (_err) {
    headlineEl.textContent = errorText(0);
    headlineEl.className = "status-headline status-error";
  }
  return true;
}

if (typeof document !== "undefined" && document.body && document.body.classList.contains("status-page")) {
  applyTheme();
  const tick = async () => {
    // A refused link stays refused; stop asking.
    if (await refresh()) window.setTimeout(tick, REFRESH_MS);
  };
  tick();
}
