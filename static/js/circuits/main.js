// The Circuit mapping page (#385): an entry module, like */main.js.
import { searchWords } from "../lib/format.js";
import { getJson } from "../lib/http.js";
import { initTopBar } from "../lib/nav.js";
import { groupsHtml, matches, prepare, STEP, summaryText } from "./list.js";

const els = {
  groups: document.getElementById("circuit-groups"),
  search: document.getElementById("circuit-search"),
  status: document.getElementById("circuit-status"),
};
const nautobotUrl = document.getElementById("app-config")?.dataset.nautobotUrl || "";
const state = { circuits: [], summary: {}, limits: {}, open: new Set() };

function render() {
  const words = searchWords(els.search.value);
  const shown = words.length ? state.circuits.filter((circuit) => matches(circuit, words)) : state.circuits;
  // A search unfolds the groups it found something in.
  const open = words.length ? new Set(shown.map((circuit) => circuit.group)) : state.open;
  els.groups.innerHTML = groupsHtml(shown, { limits: state.limits, open, nautobotUrl });
  els.status.textContent = summaryText(state.summary, words.length ? shown.length : null);
}

els.groups.addEventListener("click", (event) => {
  const more = event.target.closest(".circuit-more");
  if (!more) return;
  const { group } = more.dataset;
  state.limits[group] = (state.limits[group] || STEP) + STEP;
  // A search's unfolding is the search's: only a plain list remembers it.
  if (!els.search.value.trim()) state.open.add(group);
  render();
  // The button is redrawn: keep the keyboard on it, or on the group when all are shown.
  const next = els.groups.querySelector(`.circuit-more[data-group="${group}"]`)
    || els.groups.querySelector(`details[data-group="${group}"] > summary`);
  next?.focus();
});
// Remember which groups the operator unfolded, so a re-render keeps them.
els.groups.addEventListener("toggle", (event) => {
  const { group } = event.target.dataset || {};
  if (!group || els.search.value.trim()) return;
  if (event.target.open) state.open.add(group);
  else state.open.delete(group);
}, true);
// A new search starts each group at its first STEP again.
els.search.addEventListener("input", () => {
  state.limits = {};
  render();
});

initTopBar();
try {
  const data = await getJson("/api/circuit-mapping");
  state.circuits = prepare(data.circuits || []);
  state.summary = data.summary || {};
  render();
} catch (err) {
  els.status.textContent = `Could not load the circuits: ${err.message}`;
}
