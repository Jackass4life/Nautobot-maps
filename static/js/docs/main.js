// The API explorer page (templates/docs.html, #230).
import { initThemeToggle } from "../lib/theme.js";
import { filterEndpoints, tryEndpoint } from "./explorer.js";

const endpointFilter = document.getElementById("endpoint-filter");
const endpointCount = document.getElementById("endpoint-count");
const endpointCards = Array.from(document.querySelectorAll(".endpoint"));
const endpointGroups = Array.from(document.querySelectorAll(".docs-group"));

function applyFilter() {
  const shown = filterEndpoints(endpointCards, endpointGroups, endpointFilter?.value);
  if (endpointCount) endpointCount.textContent = `${shown} of ${endpointCards.length} shown`;
}

document.addEventListener("submit", (event) => {
  const form = event.target.closest(".try-form");
  if (!form) return;
  event.preventDefault();
  tryEndpoint(form.closest(".endpoint"), form);
});

endpointFilter?.addEventListener("input", applyFilter);
applyFilter();

initThemeToggle(document.getElementById("theme-toggle"));
