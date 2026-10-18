// The activity feed next to the board (#180).
import { formatFeedTime } from "../lib/format.js";
import { escHtml } from "../lib/html.js";
import { getJson } from "../lib/http.js";
import { scopeParams } from "../lib/scope.js";
import { alertBadge } from "./rows.js";
import { config, els } from "./state.js";

const FEED_LIMIT = 100;
export const FEED_COLLAPSED_KEY = "nautobot-maps-feed-collapsed";
// Below this width the open feed leaves the table too little room (it then
// scrolls sideways), so the feed starts collapsed unless the user opened it.
const FEED_OPEN_MIN_WIDTH_PX = 1700;

export function renderFeedEvent(event, now = new Date()) {
  const at = `<time datetime="${escHtml(event.at)}">${escHtml(formatFeedTime(event.at, now))}</time>`;
  const site = escHtml(event.site_name || event.site_id || "Unknown site");
  const device = escHtml(event.device_name || event.device_id || "Unknown device");
  if (event.kind === "severity") {
    return `
      <li class="feed-entry feed-severity">
        <span class="feed-icon" aria-hidden="true">●</span>
        <span><strong>${site}</strong><span class="visually-hidden"> severity changed</span></span>
        <span class="feed-meta">${alertBadge(event.from_level)} → ${alertBadge(event.to_level)} · ${at}</span>
      </li>`;
  }
  if (event.kind === "up") {
    return `
      <li class="feed-entry feed-up">
        <span class="feed-icon" aria-hidden="true">▲</span>
        <span><strong>${device}</strong> back up</span>
        <span class="feed-meta">${at} · ${site}</span>
      </li>`;
  }
  // Down: also when it went down, if that was before it was noticed (#166).
  const since = new Date(event.down_since);
  const noticed = new Date(event.at);
  const earlier = !Number.isNaN(since.valueOf()) && noticed - since >= 60000
    ? ` · down since ${escHtml(formatFeedTime(event.down_since, now))}`
    : "";
  return `
    <li class="feed-entry feed-down">
      <span class="feed-icon" aria-hidden="true">▼</span>
      <span><strong>${device}</strong> down</span>
      <span class="feed-meta">${at} · ${site}${earlier}</span>
    </li>`;
}

export function renderFeed(payload, kinds) {
  if (!kinds.length) return '<li class="feed-empty">Choose what to show.</li>';
  if (payload.persistence_configured === false) {
    return '<li class="feed-empty">The activity log needs the PostgreSQL database.</li>';
  }
  const events = Array.isArray(payload.events) ? payload.events : [];
  if (!events.length) return '<li class="feed-empty">No changes yet.</li>';
  const now = new Date();
  return events.map((event) => renderFeedEvent(event, now)).join("");
}

function selectedFeedKinds() {
  return (els.feedFilterButtons || [])
    .filter((button) => button.getAttribute("aria-pressed") === "true")
    .map((button) => button.dataset.feedKind);
}

export async function loadFeed() {
  const { feedList, feedPanel } = els;
  // Hidden: nothing to show; it loads again when shown.
  if (!feedList || feedPanel?.hidden) return;
  const kinds = selectedFeedKinds();
  if (!kinds.length) {
    feedList.innerHTML = renderFeed({}, kinds);
    return;
  }
  try {
    // A board for some sites only: their activity (#369).
    const params = scopeParams(config.scope);
    params.set("limit", String(FEED_LIMIT));
    params.set("kinds", kinds.join(","));
    const payload = await getJson(`/api/alert-feed?${params}`);
    feedList.innerHTML = renderFeed(payload, kinds);
  } catch (err) {
    // The board refreshes often; no toast for every failed feed load.
    feedList.innerHTML = `<li class="feed-empty">Could not load activity: ${escHtml(err.message)}</li>`;
  }
}

// The user's last Hide/Show choice; without one, whether there is room.
export function feedStartsCollapsed(stored, viewportWidth) {
  if (stored === "1" || stored === "0") return stored === "1";
  return viewportWidth < FEED_OPEN_MIN_WIDTH_PX;
}

export function setFeedCollapsed(collapsed) {
  const { boardLayout, feedPanel, feedShowBtn } = els;
  if (!boardLayout || !feedPanel || !feedShowBtn) return;
  boardLayout.classList.toggle("feed-collapsed", collapsed);
  feedPanel.hidden = collapsed;
  feedShowBtn.hidden = !collapsed;
}

export function rememberFeedCollapsed(collapsed) {
  try {
    localStorage.setItem(FEED_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch (_err) {
    // noop
  }
}
