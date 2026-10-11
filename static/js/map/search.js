// Search a place or address: the Nautobot locations within 5 km (/api/search).
import { escHtml } from "../lib/html.js";
import { showError } from "../lib/toast.js";
import { openLocationById } from "./inspector.js";
import { renderMarkers } from "./markers.js";
import { els, state } from "./state.js";

export async function doSearch() {
  const { searchInput, searchBtn, searchResults, searchResultsHeader, searchResultsList } = els;
  const query = searchInput.value.trim();
  if (!query) return;

  searchBtn.disabled = true;
  searchBtn.style.opacity = "0.5";

  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    const data = await resp.json();

    if (!resp.ok || data.error) {
      showError(data.error || `Search failed (HTTP ${resp.status})`);
      searchResults.classList.add("hidden");
      renderMarkers(state.allLocations);
      return;
    }

    const { search_lat, search_lon, count, locations } = data;

    // Re-render map with only the nearby locations highlighted
    renderMarkers(locations, { lat: search_lat, lon: search_lon });

    // Fly to search point
    state.map.flyTo([search_lat, search_lon], 11, { duration: 1.2 });

    // Populate sidebar results
    searchResultsHeader.textContent = `${count} location${count !== 1 ? "s" : ""} within 5 km`;
    searchResultsList.innerHTML = "";

    if (count === 0) {
      searchResultsList.innerHTML =
        '<li style="padding:10px 16px;color:var(--color-text-muted);font-size:.85rem">No Nautobot locations found within 5 km.</li>';
    } else {
      for (const loc of locations) {
        const li = document.createElement("li");
        li.innerHTML = `
          <div class="result-name">${escHtml(loc.name)}</div>
          <div class="result-meta">${[loc.location_type, loc.tenant].filter(Boolean).map(escHtml).join(" · ") || "—"}</div>
          <span class="result-distance">${loc.distance_km} km</span>`;
        li.addEventListener("click", () => {
          openLocationById(loc.id, { focusInspector: false, focusElement: li });
        });
        searchResultsList.appendChild(li);
      }
    }

    searchResults.classList.remove("hidden");
  } catch (err) {
    showError("Search error: " + err.message);
  } finally {
    searchBtn.disabled = false;
    searchBtn.style.opacity = "1";
  }
}
