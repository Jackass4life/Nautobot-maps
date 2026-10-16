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
    return { error: text };
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
