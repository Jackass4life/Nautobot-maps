// The browser security policy every page carries (#672): the same headers the API
// sets on the pages it serves (api/app/frontend/security.py). Both are checked against
// tests/csp-cases.json, so the two can't drift apart.

/** The CSP source for MAP_TILE_URL's host, with `{s}` subdomains as `*`: the scheme and
 * host part as written (Python's urlsplit netloc), not normalised. */
export function tileSource(mapTileUrl: string): string {
  const [, scheme, host] = /^\s*([A-Za-z][A-Za-z0-9+.-]*):\/\/([^/?#]*)/.exec(mapTileUrl) ?? [];
  if (!scheme || !host) return ""; // a relative URL: same origin, covered by 'self'
  return `${scheme.toLowerCase()}://${host.replaceAll("{s}", "*")}`;
}

/** Only this app's own scripts and styles; images may also come from the tile server. */
export function contentSecurityPolicy(mapTileUrl: string): string {
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    `img-src 'self' data: ${tileSource(mapTileUrl)}`.trim(),
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

export function securityHeaders(mapTileUrl: string): Record<string, string> {
  return {
    "content-security-policy": contentSecurityPolicy(mapTileUrl),
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
  };
}

// The API's default (api/app/core/settings.py DEFAULT_MAP_TILE_URL).
export const DEFAULT_MAP_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

/** MAP_TILE_URL as the API reads it: the environment's, else the default. */
export function mapTileUrl(env: Record<string, string | undefined> = process.env): string {
  return (env.MAP_TILE_URL ?? "").trim() || DEFAULT_MAP_TILE_URL;
}

/** The API's paths, not pages (/api/docs is a page): what the reverse proxy sends to the
 * API, and /metrics, which it doesn't serve (#674: through the web image, as the one
 * image answered it, for Prometheus when there is no proxy). */
export function isApiPath(path: string): boolean {
  return (
    /^\/api\/(?!docs(?:[/?#]|$))/.test(path) ||
    /^\/(?:healthz|metrics|mcp|docs|openapi\.json)(?:[/?#]|$)/.test(path) ||
    path.startsWith("/help/images/") ||
    path.startsWith("/static/")
  );
}
