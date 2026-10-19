// The panel's one search (#392): as you type, the loaded sites whose name,
// path or address has every word, names first; the last row searches the
// map for the text (an address or coordinates, within 5 km).  In the browser,
// from the sites already loaded: no request per key, at 1,200 sites too.
import { hasAllWords, searchWords } from "../lib/format.js";
import { escHtml } from "../lib/html.js";
import { formatLocationAddress, siteSearchText } from "../lib/scope.js";

export const SITE_MATCHES_SHOWN = 8;

// "lat, lon": the map search, not a site name.
const COORDINATES = /^\s*-?\d+(\.\d+)?\s*[,\s]\s*-?\d+(\.\d+)?\s*$/;

// A site's lower-case name and search text (the board's: name, path, address,
// country), worked out once per site, not on every key.
const searchTexts = new WeakMap();
function textsOf(loc) {
  let texts = searchTexts.get(loc);
  if (!texts) {
    texts = { name: (loc.name || "").toLowerCase(), all: siteSearchText(loc) };
    searchTexts.set(loc, texts);
  }
  return texts;
}

// {matches: the first *limit*, more: how many others}.
export function matchSites(locations, query, limit = SITE_MATCHES_SHOWN) {
  const words = searchWords(query);
  if (!words.length || COORDINATES.test(query)) return { matches: [], more: 0 };
  const found = [];
  for (const loc of locations) {
    const { name, all } = textsOf(loc);
    if (hasAllWords(name, words)) found.push({ loc, rank: name.startsWith(words[0]) ? 0 : 1 });
    else if (hasAllWords(all, words)) found.push({ loc, rank: 2 });
  }
  found.sort((a, b) => a.rank - b.rank || (a.loc.name || "").localeCompare(b.loc.name || ""));
  return { matches: found.slice(0, limit).map((entry) => entry.loc), more: Math.max(0, found.length - limit) };
}

// The list's rows: each site (name; path or address), "N more: keep typing",
// and the map search.  *levels*: location id -> alert level, for its dot.
export function siteMatchesHtml({ matches, more }, query, levels = {}) {
  const rows = matches.map((loc) => {
    const level = levels[loc.id] || "";
    const meta = loc.ancestor_path || formatLocationAddress(loc) || loc.location_type || "";
    return `<li><button class="site-match" type="button" data-location-id="${escHtml(loc.id)}">`
      + `<span class="legend-dot dot-${escHtml(level || "no-data")}" aria-hidden="true"></span>`
      + `<span class="site-match-name">${escHtml(loc.name || loc.id)}</span>`
      + `${meta ? `<span class="site-match-meta">${escHtml(meta)}</span>` : ""}</button></li>`;
  });
  if (more) rows.push(`<li class="site-match-more">${more} more: keep typing</li>`);
  rows.push(`<li><button class="site-match site-match-map" type="button">Search the map for “${escHtml(query.trim())}”</button></li>`);
  return rows.join("");
}
