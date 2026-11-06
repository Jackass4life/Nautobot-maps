// A page for some sites only (#364, #367): ?tenant= (repeatable) and ?q= on
// its link, read as the board and the wall read them (static/js/lib/scope.js).
import { siteSearchText, type SearchableSite } from "./sitesearch";

export interface Scope {
  tenants: string[];
  texts: string[];
  lowerTenants: string[];
  lowerTexts: string[];
}

export interface TenantedSite extends SearchableSite {
  tenant?: string;
  tenants?: string[] | null;
}

// After "No site matches …" on the board, the wall and the map.
export const SCOPE_NO_MATCH_HINT = "Check the link's tenant= and q=.";

// Every tenant of the site: its own, then those linked by a Nautobot
// Relationship (#238).  Data cached before #238 only has `tenant`.
export function siteTenants(site: TenantedSite): string[] {
  if (Array.isArray(site.tenants) && site.tenants.length) return site.tenants;
  return site.tenant ? [site.tenant] : [];
}

// {tenants, texts} as typed, plus their lower case for matching; null with
// neither.  ?tenant= is any of them, ?q= every one of them (#366).
export function readScope(search: string): Scope | null {
  const params = new URLSearchParams(search || "");
  const clean = (name: string) => params.getAll(name).map((value) => value.trim()).filter(Boolean);
  const tenants = clean("tenant");
  const texts = clean("q");
  if (!tenants.length && !texts.length) return null;
  return {
    tenants,
    texts,
    lowerTenants: tenants.map((name) => name.toLowerCase()),
    lowerTexts: texts.map((text) => text.toLowerCase()),
  };
}

// Typed by hand into a link, so case doesn't matter.
export function inScope(site: TenantedSite, scope: Scope | null): boolean {
  if (!scope) return true;
  const { lowerTenants, lowerTexts } = scope;
  if (lowerTenants.length && !siteTenants(site).some((name) => lowerTenants.includes(name.toLowerCase()))) return false;
  if (!lowerTexts.length) return true;
  const text = siteSearchText(site);
  return lowerTexts.every((needle) => text.includes(needle));
}

// The scope as ?tenant= / ?q= again, for a request or link for the same sites.
export function scopeParams(scope: Scope | null): URLSearchParams {
  const params = new URLSearchParams();
  scope?.tenants.forEach((name) => params.append("tenant", name));
  scope?.texts.forEach((text) => params.append("q", text));
  return params;
}

// The query the map's and the board's links carry: the tenants picked in the
// filters, else the link's, and the link's text (#367).
export function linkParams(tenants: string[], scope: Scope | null): URLSearchParams {
  const params = scopeParams(scope);
  if (tenants.length) {
    params.delete("tenant");
    tenants.forEach((name) => params.append("tenant", name));
  }
  return params;
}

// "Acme Corp, Nordic Net · “DNK”": each tenant as Nautobot spells it when
// one of *sites* has it.
export function scopeLabel(scope: Scope | null, sites: TenantedSite[] = []): string {
  if (!scope) return "";
  const known = new Map(sites.flatMap(siteTenants).map((name) => [name.toLowerCase(), name]));
  const tenants = scope.tenants.map((name) => known.get(name.toLowerCase()) || name);
  return [tenants.join(", "), ...scope.texts.map((text) => `“${text}”`)].filter(Boolean).join(" · ");
}
