// "Find a site" (#392, #411; static/js/map/sitesearch.js): the loaded sites
// whose name, path or address has every word, names first.  In the browser,
// from the sites loaded once: no request per key, at 1,200 sites too.
import { hasAllWords, searchWords } from "./format";

export const SITE_MATCHES_SHOWN = 8;

export interface SearchableSite {
  id: string;
  name: string;
  ancestor_path?: string;
  parent?: string;
  physical_address?: string;
  facility?: string;
  country?: string;
}

// "lat, lon": the map's search, not a site name.
const COORDINATES = /^\s*-?\d+(\.\d+)?\s*[,\s]\s*-?\d+(\.\d+)?\s*$/;

export function siteAddress(site: SearchableSite): string {
  return site.physical_address || site.facility || "";
}

// What a filter box (and a link's ?q=) searches: name, path, address, country.
export function siteSearchText(site: SearchableSite): string {
  return [site.name, site.ancestor_path || site.parent, siteAddress(site), site.country].join(" ").toLowerCase();
}

const texts = new WeakMap<SearchableSite, { name: string; all: string }>();
function textsOf(site: SearchableSite) {
  let found = texts.get(site);
  if (!found) {
    found = { name: (site.name || "").toLowerCase(), all: siteSearchText(site) };
    texts.set(site, found);
  }
  return found;
}

// The first *limit* matches (a full name first, then names starting with
// the first word, other names, then path or address), and how many more.
export function matchSites<T extends SearchableSite>(
  sites: T[],
  query: string,
  limit = SITE_MATCHES_SHOWN,
): { matches: T[]; more: number } {
  const words = searchWords(query);
  if (!words.length || COORDINATES.test(query)) return { matches: [], more: 0 };
  const exact = query.trim().toLowerCase();
  const found: { site: T; rank: number }[] = [];
  for (const site of sites) {
    const { name, all } = textsOf(site);
    if (name === exact) found.push({ site, rank: -1 });
    else if (hasAllWords(name, words)) found.push({ site, rank: name.startsWith(words[0]!) ? 0 : 1 });
    else if (hasAllWords(all, words)) found.push({ site, rank: 2 });
  }
  found.sort((a, b) => a.rank - b.rank || (a.site.name || "").localeCompare(b.site.name || ""));
  return { matches: found.slice(0, limit).map((entry) => entry.site), more: Math.max(0, found.length - limit) };
}
