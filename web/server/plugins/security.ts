// Every answer of the web image's server carries the pages' security headers (#672),
// the static files under /_nuxt/ too: set as each request comes in, before any handler.
// Not in `bun run dev`, whose Vite client runs inline scripts; not on the API's paths,
// which the API answers with its own headers (pages.ts forwards them).
import { setResponseHeaders } from "h3";
import { isApiPath, mapTileUrl, securityHeaders } from "../utils/security";

export default defineNitroPlugin((nitroApp) => {
  if (import.meta.dev || import.meta.prerender) return;
  const headers = securityHeaders(mapTileUrl());
  nitroApp.hooks.hook("request", (event) => {
    if (!isApiPath(event.path)) setResponseHeaders(event, headers);
  });
});
