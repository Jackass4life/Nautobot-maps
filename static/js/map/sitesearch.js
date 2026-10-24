// The top bar's "Find a site" on the pages not yet on Nuxt (#392, #411): the
// loaded sites whose name, path or address has every word, names first.  In
// the browser, from the sites loaded once: no request per key, at 1,200 sites
// too.  The map's own is frontend/app/utils/sitesearch.ts (#463).
import { hasAllWords, searchWords } from "../lib/format.js";
import { siteSearchText } from "../lib/scope.js";

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

// {matches: the first *limit*, more: how many others, nameMatch: a full name}.
export function matchSites(locations, query, limit = SITE_MATCHES_SHOWN) {
  const words = searchWords(query);
  if (!words.length || COORDINATES.test(query)) return { matches: [], more: 0, nameMatch: false };
  const exactName = query.trim().toLowerCase();
  const found = [];
  for (const loc of locations) {
    const { name, all } = textsOf(loc);
    if (name === exactName) found.push({ loc, rank: -1 });
    else if (hasAllWords(name, words)) found.push({ loc, rank: name.startsWith(words[0]) ? 0 : 1 });
    else if (hasAllWords(all, words)) found.push({ loc, rank: 2 });
  }
  found.sort((a, b) => a.rank - b.rank || (a.loc.name || "").localeCompare(b.loc.name || ""));
  return {
    matches: found.slice(0, limit).map((entry) => entry.loc),
    more: Math.max(0, found.length - limit),
    nameMatch: found[0]?.rank === -1,
  };
}
