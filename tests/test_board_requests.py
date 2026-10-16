"""The board's requests read the API's answer the same way (#350): lib/http.js getJson."""

from tests.js_modules import run_js

PRELUDE = """
import { sendAck } from "@js/alerts/case-panel.js";
import { loadFeed } from "@js/alerts/feed.js";
import { openHistory } from "@js/alerts/panels.js";
import { els } from "@js/alerts/state.js";

const calls = [];
let answer = null;
globalThis.fetch = async (url, options = {}) => {
  calls.push({ url, ...options });
  return answer();
};
const json = (body, status = 200) => () => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const toast = element("error-toast");
document.getElementById = (id) => (id === "error-toast" ? toast : null);
"""


def test_an_acknowledgement_shows_the_apis_error():
    run_js(
        PRELUDE
        + """
answer = json({ error: "Not down any more", missing_device_ids: ["d1"] }, 409);
let message = "";
let payload = null;
try { await sendAck("s1", ["d1"], "on it", false); } catch (err) { ({ message, payload } = err); }
check(message === "Not down any more", message);
// The answer goes with the error, for its details (the case save names recovered devices).
check(payload?.missing_device_ids?.[0] === "d1", JSON.stringify(payload));
check(calls.length === 1 && calls[0].url === "/api/alert-acks" && calls[0].method === "POST", JSON.stringify(calls));
check(JSON.parse(calls[0].body).device_ids[0] === "d1", calls[0].body);
// An error page that isn't JSON: its text, not a parse error.
answer = () => new Response("Bad gateway", { status: 502 });
message = "";
try { await sendAck("s1", null, "", true); } catch (err) { message = err.message; }
check(message === "Bad gateway" && calls[1].url === "/api/alert-acks/remove", message);
"""
    )


def test_the_feed_and_the_history_show_a_failed_load():
    run_js(
        PRELUDE
        + """
const kind = element("down", { dataset: { feedKind: "down" } });
kind.setAttribute("aria-pressed", "true");
Object.assign(els, { feedList: element("feed"), feedPanel: element("panel"), feedFilterButtons: [kind] });
answer = () => new Response("", { status: 503 });
await loadFeed();
check(els.feedList.innerHTML.includes("Could not load activity: HTTP 503"), els.feedList.innerHTML);
check(calls[0].url.startsWith("/api/alert-feed?") && calls[0].cache === "no-store", JSON.stringify(calls[0]));

answer = json({ error: "operator role required" }, 403);
const button = element("history", { dataset: { siteId: "s 1" } });
await openHistory(button);
check(toast.textContent === "Failed to load history: operator role required", toast.textContent);
check(calls[1].url === "/api/alert-history?site_id=s%201" && !button.disabled, JSON.stringify(calls[1]));
"""
    )
