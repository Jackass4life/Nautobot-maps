// The web image's security headers (#672) match the API's: both are checked against
// tests/csp-cases.json (api/tests/test_csp_cases.py checks the API).
import { describe, expect, it } from "vitest";
import cases from "./csp-cases.json";
import {
  contentSecurityPolicy,
  DEFAULT_MAP_TILE_URL,
  isApiPath,
  mapTileUrl,
  securityHeaders,
} from "../server/utils/security";

describe("the pages' security headers", () => {
  it.each(cases.cases)("CSP for $map_tile_url", ({ map_tile_url, csp }) => {
    expect(contentSecurityPolicy(map_tile_url)).toBe(csp);
  });

  it("the other headers", () => {
    const { "content-security-policy": _csp, ...rest } = securityHeaders(DEFAULT_MAP_TILE_URL);
    expect(rest).toEqual(cases.headers);
  });

  it("MAP_TILE_URL as the API reads it: the default when empty", () => {
    expect(mapTileUrl({})).toBe(DEFAULT_MAP_TILE_URL);
    expect(mapTileUrl({ MAP_TILE_URL: "  " })).toBe(DEFAULT_MAP_TILE_URL);
    expect(mapTileUrl({ MAP_TILE_URL: " /tiles/{z} " })).toBe("/tiles/{z}");
  });
});

describe("the API's paths, which the reverse proxy sends to the API", () => {
  it.each([
    "/api/v1/alerts",
    "/api/alerts",
    "/api/openapi.json",
    "/healthz",
    "/metrics",
    "/mcp",
    "/help/images/board.png",
    "/docs",
    "/openapi.json",
    "/static/app.css",
  ])("%s is the API's", (path) => {
    expect(isApiPath(path)).toBe(true);
  });

  it.each([
    "/",
    "/alerts",
    "/api/docs",
    "/api/docs/",
    "/help",
    "/help/install",
    "/status/x",
    "/_nuxt/a.js",
    "/apidocs",
    "/healthzz",
  ])("%s is a page", (path) => {
    expect(isApiPath(path)).toBe(false);
  });
});
