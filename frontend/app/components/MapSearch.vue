<!-- The map panel's one search (#392, #411): the loaded sites whose name,
     path or address match, as you type (no request per key, at 1,200 sites
     too); then the map's place or coordinates search within 5 km.  Enter
     follows the first row: a full site name, otherwise the map search. -->
<script setup lang="ts">
import type { MapLocation, NearbySite } from "~/utils/map";
import { siteAddress, matchSites } from "~/utils/sitesearch";

const props = defineProps<{
  locations: MapLocation[];
  levels: Record<string, string>;
  // A place search's answer: what was found within 5 km; null when none is shown.
  results: NearbySite[] | null;
  busy: boolean;
}>();
const emit = defineEmits<{
  // A site picked from the list.
  pick: [id: string];
  // The map search for the text.
  search: [query: string];
  // The text cleared: back to the whole map.
  clear: [];
  // A row of the place search's answer.
  open: [id: string, element: HTMLElement];
}>();

const query = ref("");
const listOpen = ref(false);
const section = useTemplateRef<HTMLElement>("section");
const list = useTemplateRef<HTMLUListElement>("list");
const inputBox = useTemplateRef<{ inputRef: HTMLInputElement | null }>("input");

const found = computed(() => (listOpen.value ? matchSites(props.locations, query.value) : null));
const resultsHeader = computed(() => {
  const count = props.results?.length ?? 0;
  return `${count} location${count === 1 ? "" : "s"} within 5 km`;
});

function buttons(): HTMLButtonElement[] {
  return Array.from(list.value?.querySelectorAll("button") ?? []);
}
function hideList() {
  listOpen.value = false;
}
function mapSearch() {
  hideList();
  if (query.value.trim()) emit("search", query.value.trim());
}
function pick(id: string) {
  hideList();
  query.value = "";
  emit("pick", id);
}
function onInput(value: string | number) {
  if (!String(value ?? "").trim()) {
    hideList();
    emit("clear");
    return;
  }
  listOpen.value = true;
}
function onInputKey(event: KeyboardEvent) {
  if (event.key === "Enter") {
    const first = listOpen.value ? buttons()[0] : undefined;
    if (first?.dataset.locationId) pick(first.dataset.locationId);
    else mapSearch();
  } else if (event.key === "ArrowDown" && listOpen.value) {
    event.preventDefault();
    buttons()[0]?.focus();
  } else if (event.key === "Escape" && listOpen.value) {
    event.stopPropagation(); // the list, not also the site panel
    hideList();
  }
}
function onListKey(event: KeyboardEvent) {
  const all = buttons();
  const at = all.indexOf(document.activeElement as HTMLButtonElement);
  if (event.key === "ArrowDown" && at < all.length - 1) all[at + 1]!.focus();
  else if (event.key === "ArrowUp") (at > 0 ? all[at - 1] : inputBox.value?.inputRef)?.focus();
  else if (event.key === "Escape") {
    event.stopPropagation();
    hideList();
    inputBox.value?.inputRef?.focus();
  } else return;
  event.preventDefault();
}
// Leaving the search (not into its list) closes the list.
function onFocusOut(event: FocusEvent) {
  if (!section.value?.contains(event.relatedTarget as Node | null)) hideList();
}
// Picking a site elsewhere (the alarm list) empties the search.
function reset() {
  query.value = "";
  hideList();
}
defineExpose({ reset });
</script>

<template>
  <div id="search-section" ref="section" class="relative" @focusout="onFocusOut">
    <div id="search-row" class="flex gap-1.5">
      <UInput
        id="search-input"
        ref="input"
        v-model="query"
        type="search"
        autocomplete="off"
        :spellcheck="false"
        placeholder="Find a site or place"
        aria-label="Find a site or place"
        aria-controls="site-matches"
        aria-describedby="search-hint"
        icon="i-lucide-search"
        class="min-w-0 flex-1"
        @update:model-value="onInput"
        @keydown="onInputKey"
      />
      <UButton
        id="search-btn"
        icon="i-lucide-map-pin"
        color="neutral"
        variant="outline"
        :loading="busy"
        aria-label="Search the map"
        title="Search the map for an address or coordinates"
        @click="mapSearch"
      />
    </div>
    <p id="search-hint" class="mt-1 text-xs text-muted">A site's name or address, a place, or <span class="whitespace-nowrap">55.6761, 12.5683</span>.</p>

    <!-- A click on the list keeps focus in the field (Safari doesn't focus buttons). -->
    <ul
      v-if="found"
      id="site-matches"
      ref="list"
      class="site-matches absolute inset-x-0 top-10 z-20 max-h-96 overflow-y-auto rounded-md border border-default bg-default py-1 shadow-lg"
      aria-label="Matching sites"
      @mousedown.prevent
      @keydown="onListKey"
    >
      <li v-if="!found.nameMatch">
        <button class="site-match site-match-map w-full px-3 py-1.5 text-left text-sm text-primary hover:bg-elevated focus:bg-elevated focus:outline-none" type="button" @click="mapSearch">
          Search the map for “{{ query.trim() }}”
        </button>
      </li>
      <li v-for="site in found.matches" :key="site.id">
        <button
          class="site-match flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-elevated focus:bg-elevated focus:outline-none"
          type="button"
          :data-location-id="site.id"
          @click="pick(site.id)"
        >
          <span class="legend-dot" :class="`dot-${levels[site.id] || 'no-data'}`" aria-hidden="true" />
          <span class="min-w-0 flex-1">
            <span class="site-match-name block truncate text-sm font-medium text-highlighted">{{ site.name || site.id }}</span>
            <span v-if="site.ancestor_path || siteAddress(site) || site.location_type" class="site-match-meta block truncate text-xs text-muted">
              {{ site.ancestor_path || siteAddress(site) || site.location_type }}
            </span>
          </span>
        </button>
      </li>
      <li v-if="found.more" class="site-match-more px-3 py-1.5 text-xs text-muted">{{ found.more }} more: keep typing</li>
      <li v-if="found.nameMatch">
        <button class="site-match site-match-map w-full px-3 py-1.5 text-left text-sm text-primary hover:bg-elevated focus:bg-elevated focus:outline-none" type="button" @click="mapSearch">
          Search the map for “{{ query.trim() }}”
        </button>
      </li>
    </ul>

    <div id="search-results" class="mt-3" :class="{ hidden: !results }">
      <div id="search-results-header" class="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{{ resultsHeader }}</div>
      <ul id="search-results-list" class="max-h-48 overflow-y-auto">
        <li v-if="results && !results.length" class="search-none text-sm text-muted">No Nautobot locations found within 5 km.</li>
        <li v-for="site in results ?? []" :key="site.id">
          <button
            class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-elevated"
            type="button"
            @click="emit('open', site.id, $event.currentTarget as HTMLElement)"
          >
            <span class="min-w-0 flex-1">
              <span class="result-name block truncate text-sm font-medium text-highlighted">{{ site.name }}</span>
              <span class="result-meta block truncate text-xs text-muted">{{ [site.location_type, site.tenant].filter(Boolean).join(" · ") || "—" }}</span>
            </span>
            <span class="result-distance shrink-0 text-xs text-muted">{{ site.distance_km }} km</span>
          </button>
        </li>
      </ul>
    </div>
  </div>
</template>
