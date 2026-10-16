// An HTML error page (a proxy's 502, say) as its words, not its markup (#350);
// the first 200 characters are enough for a toast.
function pageText(html) {
  return html
    .replace(/<(script|style)[^>]*>[\s\S]*?<\/\1>/gi, " ")
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 200);
}

// The JSON of an API answer; an error page's text as {error} (#294).
export async function readJsonResponse(resp) {
  const contentType = resp.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return resp.json();
  }
  const text = await resp.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch (_err) {
    return { error: contentType.includes("html") ? pageText(text) : text };
  }
}

// fetch() that returns the JSON, or throws the API's error (#343), with the
// answer as err.payload for its details (#350).
export async function getJson(url, options) {
  const resp = await fetch(url, { cache: "no-store", ...options });
  const payload = await readJsonResponse(resp);
  if (!resp.ok || payload.error) {
    throw Object.assign(new Error(payload.error || `HTTP ${resp.status}`), { payload });
  }
  return payload;
}
