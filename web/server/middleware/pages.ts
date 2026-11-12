// The web image's pages (#672). Nuxt builds a single-page app (ssr: false): every page
// route answers the same shell, /200.html, rendered at build time without inline
// scripts (scripts/no-inline-scripts.mjs; the CSP allows none). Files that exist, such
// as /_nuxt/…, are served before this runs. The API's paths go to API_URL when it is set
// (the image on its own, without the reverse proxy, which sends them to the API itself).
import {
  createError,
  defineEventHandler,
  getRequestURL,
  proxyRequest,
  setResponseHeader,
} from "h3";
import { isApiPath } from "../utils/security";

let shell: string | undefined;

const SHELL = "/200.html";

export default defineEventHandler(async (event) => {
  // Not while Nuxt renders the shell at build time, nor for the shell itself (which
  // this answers with, from the files).
  if (import.meta.dev || import.meta.prerender) return;
  const path = getRequestURL(event).pathname;
  if (path === SHELL) return;
  if (isApiPath(path)) {
    const api = (process.env.API_URL ?? "").trim().replace(/\/+$/, "");
    if (!api) throw createError({ statusCode: 404, statusMessage: "Not found" });
    return proxyRequest(event, `${api}${event.path}`);
  }
  if (path.startsWith("/_nuxt/")) {
    throw createError({ statusCode: 404, statusMessage: "Not found" });
  }
  if (event.method !== "GET" && event.method !== "HEAD") {
    throw createError({ statusCode: 405, statusMessage: "Method not allowed" });
  }
  shell ??= await (await useNitroApp().localFetch(SHELL)).text();
  // Always asked again: a new build shows at once (its files are named by content).
  setResponseHeader(event, "cache-control", "no-cache");
  setResponseHeader(event, "content-type", "text/html; charset=utf-8");
  // The customer status page's link holds its key in the fragment; no referrer at all.
  if (path.startsWith("/status/")) setResponseHeader(event, "referrer-policy", "no-referrer");
  return shell;
});
