<!-- The map (#334, #392, #420; on Nuxt since #463): every site, the sites
     that need action first in the panel, one search, the filters folded
     away.  ?tenant= / ?q= show some sites only (#367); ?location_id= opens
     a site.  Alert levels every minute while the tab is visible. -->
<script setup lang="ts">
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import type { components } from "~/api/schema";
import { SiteMap, loadLeaflet } from "~/utils/leafletmap";
import {
  alarmSites,
  alarmTitle,
  filterChoices,
  filterLocations,
  isAlarm,
  isPanelShortcut,
  knownLevel,
  noCriteria,
  panelToggleLabel,
  siteCount,
  type Criteria,
  type LevelInfo,
  type MapLocation,
  type NearbySite,
} from "~/utils/map";
import { SCOPE_NO_MATCH_HINT, inScope, linkParams, readScope, scopeLabel, scopeParams } from "~/utils/scope";
import type { LocationDetail } from "~/utils/sitepanel";

type LocationAlerts = components["schemas"]["LocationAlerts"];
type SearchAnswer = components["schemas"]["Search"];

useHead({ title: "Map · Nautobot Maps" });

const pageConfig = usePageConfig();
const config = pageConfig.data;
const toast = useToast();
function showError(text: string) {
  toast.add({ title: text, color: "error", icon: "i-lucide-circle-alert" });
}
function failure(err: unknown): string {
  const fetchError = err as { data?: { error?: string }; message?: string };
  return fetchError.data?.error || fetchError.message || "unknown error";
}

const ALERT_REFRESH_MS = 60 * 1000;
const ALARMS_ONLY_KEY = "nautobot-maps-map-alarms-only";
const PANEL_HIDDEN_KEY = "nautobot-maps-map-panel-hidden";

function stored(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}
function store(key: string, on: boolean) {
  try {
    if (on) window.localStorage.setItem(key, "1");
    else window.localStorage.removeItem(key);
  } catch {
    // Not remembered; works for this page.
  }
}

// ?tenant= (repeatable) and ?q=: the map for some sites only, as the board (#367).
const scope = readScope(window.location.search);
const scopeQuery = scopeParams(scope).toString();
const linkedId = new URLSearchParams(window.location.search).get("location_id");
let linkedOpened = false;

// Up to 1,200 sites: not deeply reactive.
const all = shallowRef<MapLocation[]>([]);
const everyCount = ref(0);
const loading = ref(true);
const loadingText = ref("Loading locations from Nautobot…");
// What /api/location-alerts says about each site, and the level each shows:
// a site panel's fresher level can be newer than the last refresh.
const levelInfo = shallowRef<Record<string, LevelInfo>>({});
const levels = shallowRef<Record<string, string>>({});
const levelsLoaded = ref(false);
const levelsFailed = ref(false);
const levelOf = (id: string) => knownLevel(levels.value[id]);

const criteria = ref<Criteria>(noCriteria());
// "Alarms only" (#334), remembered in this browser; a link to a site shows it, even a quiet one.
const alarmsOnly = ref(stored(ALARMS_ONLY_KEY) && !linkedId);
// A place search's answer, while it is shown.
const results = shallowRef<NearbySite[] | null>(null);
const searching = ref(false);
// The latest place search; an older answer is dropped.
let searchTicket = 0;

const choices = computed(() => filterChoices(all.value));
const filtered = computed(() => filterLocations(all.value, criteria.value));
// Once the levels are in: before (or if they fail) every site, with the note, not an empty map (#337).
const alarmsOnlyApplies = computed(() => alarmsOnly.value && levelsLoaded.value);
const drawn = computed(() => (alarmsOnlyApplies.value ? filtered.value.filter((loc) => isAlarm(levels.value[loc.id])) : filtered.value));
const countText = computed(() => siteCount(drawn.value.length, alarmsOnlyApplies.value));
const alarms = computed(() => {
  if (!levelsLoaded.value) return [];
  const info = Object.fromEntries(Object.entries(levelInfo.value).map(([id, entry]) => [id, { ...entry, level: levels.value[id] as LevelInfo["level"] }]));
  return alarmSites(filtered.value, info);
});
const alarmHeading = computed(() =>
  alarmTitle(alarms.value.length, !scope && filtered.value.length === all.value.length, all.value.length > 0),
);
const linkQuery = computed(() => linkParams(criteria.value.tenant, scope).toString());

// "Only Acme Corp · Show all sites"; a link that matches no site says so (#367).
const scopeText = computed(() => scopeLabel(scope, all.value));
const scopeNoMatch = computed(() => everyCount.value > 0 && !all.value.length);

// --- The map -------------------------------------------------------------

const mapElement = useTemplateRef<HTMLElement>("map");
let siteMap: SiteMap | null = null;

// Draw the sites the filters let through, unless a place search is shown.
function draw() {
  if (!siteMap) return;
  siteMap.render(drawn.value);
  openLinkedSite();
}
// Back to the whole map: a place search still on its way is dropped, so
// its answer can't take the map away from what was picked since.
function leaveSearch() {
  searchTicket += 1;
  searching.value = false;
  results.value = null;
  draw();
}
function setCriteria(next: Criteria) {
  criteria.value = next;
  leaveSearch();
}
function setAlarmsOnly(on: boolean, remember = true) {
  alarmsOnly.value = on;
  if (remember) store(ALARMS_ONLY_KEY, on);
  leaveSearch();
}
// *remember* false: for the moment only, so a saved Alarms only stays saved.
function clearFilters(remember = true) {
  criteria.value = noCriteria();
  alarmsOnly.value = false;
  if (remember) store(ALARMS_ONLY_KEY, false);
  leaveSearch();
}

async function loadLevels() {
  let data: LocationAlerts;
  try {
    data = await $fetch<LocationAlerts>("/api/location-alerts");
  } catch (err) {
    // Markers keep their last colours; never loaded: grey would read as "no data", so say so (#311).
    console.warn("Could not load location alert levels:", err);
    if (!levelsLoaded.value) levelsFailed.value = true;
    return;
  }
  const first = !levelsLoaded.value;
  const next = Object.fromEntries(Object.entries(data.levels).map(([id, entry]) => [id, entry.level as string]));
  const old = levels.value;
  const changed = [...new Set([...Object.keys(old), ...Object.keys(next)])].filter((id) => old[id] !== next[id]);
  levelInfo.value = data.levels;
  levels.value = next;
  levelsLoaded.value = true;
  levelsFailed.value = false;
  // Site details loaded earlier may now hold an old level.
  cacheGeneration += 1;
  for (const id of detailCache.keys()) if (id !== selectedId.value) detailCache.delete(id);
  if (siteMap && all.value.length) {
    // A site that started or stopped needing attention: one redraw for all.
    // With Alarms only, any change may add or remove a site, and the first
    // levels turn it on.  Not while a place search is shown.
    const redraw = changed.some((id) => siteMap!.crossesClusterLine(id)) || (alarmsOnly.value && (changed.length > 0 || first));
    if (redraw && !siteMap.showingSearch) draw();
    else changed.forEach((id) => siteMap!.refreshIcon(id));
  }
  applyLevelToOpenPanel(data.levels);
}

// A level the site panel loaded colours the site's marker too; a site in
// maintenance stays blue (#283).
function updateMarkerLevel(id: string, level: string) {
  if (levels.value[id] === "maintenance" || levels.value[id] === level) return;
  levels.value = { ...levels.value, [id]: level };
  if (!siteMap) return;
  if (siteMap.crossesClusterLine(id)) draw();
  else siteMap.refreshIcon(id);
}

// --- The site panel ------------------------------------------------------

const panel = useTemplateRef<{ focus: () => void }>("panel");
const selectedId = ref<string | null>(null);
const groupIds = ref<string[]>([]);
const detail = shallowRef<LocationDetail | null>(null);
const detailLoading = ref(false);
const detailError = ref("");
const detailCache = new Map<string, LocationDetail>();
// Counts the refreshes that empty the cache: an answer asked for before one isn't kept.
let cacheGeneration = 0;
const inFlight = new Map<string, Promise<LocationDetail>>();
let lastTrigger: HTMLElement | null = null;

const byId = computed(() => new Map(all.value.map((loc) => [loc.id, loc])));
const selected = computed(() => (selectedId.value ? byId.value.get(selectedId.value) ?? null : null));
const group = computed(() => groupIds.value.map((id) => byId.value.get(id)).filter((loc): loc is MapLocation => Boolean(loc)));

// The site's details, once per site until the levels refresh; one request for concurrent calls.
async function loadDetail(id: string): Promise<LocationDetail> {
  const cached = detailCache.get(id);
  if (cached) return cached;
  let request = inFlight.get(id);
  if (!request) {
    const type = byId.value.get(id)?.location_type;
    const generation = cacheGeneration;
    request = $fetch<LocationDetail>(`/api/locations/${encodeURIComponent(id)}/detail`, {
      query: type ? { location_type: type } : undefined,
    })
      .then((answer) => {
        if (generation === cacheGeneration) detailCache.set(id, answer);
        return answer;
      })
      .finally(() => inFlight.delete(id));
    inFlight.set(id, request);
  }
  return request;
}

function setLocationQuery(id: string | null) {
  const params = new URLSearchParams(window.location.search);
  if (id) params.set("location_id", id);
  else params.delete("location_id");
  const query = params.toString();
  window.history.replaceState(window.history.state, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
}

function openSite(id: string, { ids = [id], trigger = null as HTMLElement | null, focusPanel = true } = {}) {
  if (!byId.value.has(id)) return;
  selectedId.value = id;
  groupIds.value = ids.length ? [...ids] : [id];
  if (trigger) lastTrigger = trigger;
  const cached = detailCache.get(id);
  detail.value = cached ?? null;
  detailError.value = "";
  detailLoading.value = !cached;
  setLocationQuery(id);
  siteMap?.resize();
  if (focusPanel) nextTick(() => panel.value?.focus());
  if (cached) {
    updateMarkerLevel(id, cached.alert.level);
    return;
  }
  loadDetail(id)
    .then((answer) => {
      if (selectedId.value !== id) return;
      detail.value = answer;
      detailLoading.value = false;
      updateMarkerLevel(id, answer.alert.level);
    })
    .catch((err) => {
      if (selectedId.value !== id) return;
      detailLoading.value = false;
      detailError.value = failure(err);
    });
}

function closeSite(restoreFocus = true) {
  if (!selectedId.value) return;
  selectedId.value = null;
  groupIds.value = [];
  detail.value = null;
  detailLoading.value = false;
  detailError.value = "";
  setLocationQuery(null);
  siteMap?.resize();
  if (restoreFocus && lastTrigger?.isConnected) lastTrigger.focus();
}

function retryDetail() {
  if (!selectedId.value) return;
  detailCache.delete(selectedId.value);
  openSite(selectedId.value, { ids: groupIds.value, focusPanel: false });
}

// Show *id* on the map and open its panel; false when it isn't drawn.
function showSite(id: string, { trigger = null as HTMLElement | null, focusPanel = false } = {}): boolean {
  const loc = byId.value.get(id);
  const marker = loc && siteMap ? siteMap.show(loc) : false;
  if (!loc || marker === false) return false;
  openSite(id, { ids: siteMap!.idsAt(id), trigger: trigger ?? marker, focusPanel });
  return true;
}

function openLinkedSite() {
  if (!linkedId || linkedOpened) return;
  if (showSite(linkedId)) linkedOpened = true;
}

// The open panel follows a refreshed level (#316), redrawn only on a change.
function applyLevelToOpenPanel(info: Record<string, LevelInfo>) {
  const id = selectedId.value;
  const old = detail.value;
  if (!id || !old || detailLoading.value || detailError.value) return;
  const level = info[id] ?? { level: "no_data", reason: "No monitored devices", down: 0, monitored: Number.NaN };
  const counts = Number.isFinite(level.monitored) ? { down: level.down || 0, monitored: level.monitored } : old.counts;
  const alert = { level: level.level, reason: level.reason || "" };
  if (old.alert.level === alert.level && old.alert.reason === alert.reason && old.counts.down === counts.down && old.counts.monitored === counts.monitored) return;
  detail.value = { ...old, alert, counts };
  // The devices' notes (maintenance, not monitored) may have changed too.
  detailCache.delete(id);
  loadDetail(id)
    .then((answer) => {
      if (selectedId.value === id && !detailLoading.value) detail.value = answer;
    })
    .catch((err) => console.warn("Could not reload the site panel:", err));
}

// --- The panel's lists and search ----------------------------------------

const searchBox = useTemplateRef<{ reset: () => void }>("searchBox");

// A row of the alarm list; during a place search a site outside it goes back to the whole map (#337).
function openAlarm(id: string, element: HTMLElement) {
  if (showSite(id, { trigger: element })) return;
  searchBox.value?.reset();
  leaveSearch();
  showSite(id, { trigger: element });
}

// A site picked from the search's list: back to the whole map, and every
// site for now when the filters leave it out (a saved Alarms only stays).
function pickSite(id: string) {
  leaveSearch();
  if (showSite(id)) return;
  clearFilters(false);
  showSite(id);
}

function endSearch() {
  if (results.value || searching.value) leaveSearch();
}

async function mapSearch(query: string) {
  if (!siteMap) return;
  const ticket = ++searchTicket;
  searching.value = true;
  try {
    const answer = await $fetch<SearchAnswer>("/api/search", { query: { q: query } });
    if (ticket !== searchTicket) return;
    // On a map for some sites only, the nearby ones among them (#367).
    const found = answer.locations.filter((loc) => inScope(loc, scope));
    results.value = found;
    siteMap.renderSearch(found, { lat: answer.search_lat, lon: answer.search_lon });
  } catch (err) {
    if (ticket !== searchTicket) return;
    // The API's own message ("Address not found: …") as it is (#350).
    const own = (err as { data?: { error?: string } }).data?.error;
    showError(own || `Search failed: ${failure(err)}`);
    results.value = null;
    draw();
  } finally {
    if (ticket === searchTicket) searching.value = false;
  }
}

function showMisplaced(id: string) {
  if (!showSite(id, { focusPanel: true })) {
    showError("That site isn't shown on the map now: clear the filters (or Alarms only) to see it.");
  }
}

// --- The folding panel (#420) ----------------------------------------------

const panelHidden = ref(stored(PANEL_HIDDEN_KEY));
const barToggle = useTemplateRef<{ $el: HTMLElement }>("barToggle");
const sidebar = useTemplateRef<HTMLElement>("sidebar");
const tab = computed(() => panelToggleLabel(panelHidden.value, levelsLoaded.value ? alarms.value.length : null));
const tabHasAlarms = computed(() => levelsLoaded.value && alarms.value.length > 0);

function setPanelHidden(on: boolean) {
  // Out of the tab order while folded.
  if (on && sidebar.value?.contains(document.activeElement)) barToggle.value?.$el.focus();
  panelHidden.value = on;
  store(PANEL_HIDDEN_KEY, on);
  siteMap?.resize(true);
}

function onKey(event: KeyboardEvent) {
  if (isPanelShortcut(event)) {
    event.preventDefault();
    setPanelHidden(!panelHidden.value);
  } else if (event.key === "Escape" && selectedId.value && !document.querySelector("[data-dismissable-layer]")) {
    // A menu, a filter list or a tip that is open closes on its own Escape, not the panel too.
    closeSite();
  }
}

// --- Loading -----------------------------------------------------------------

let timer: ReturnType<typeof setInterval> | undefined;
function onVisible() {
  if (!document.hidden) loadLevels();
}

onMounted(async () => {
  document.addEventListener("keydown", onKey);
  try {
    await Promise.all([loadLeaflet(), pageConfig]);
    // Drawn anyway, with the default tiles and no fixed view: said, not silent.
    if (pageConfig.error.value) showError(`The map's settings could not be loaded: ${failure(pageConfig.error.value)}`);
    const settings = config.value.map;
    siteMap = new SiteMap(mapElement.value!, {
      tileUrl: settings.tile_url,
      attribution: settings.tile_attribution,
      view: settings.view,
      levelOf,
      onActivate: (id, ids, element) => {
        siteMap?.panTo(id);
        openSite(id, { ids, trigger: element, focusPanel: true });
      },
    });
  } catch (err) {
    showError(`The map could not be drawn: ${failure(err)}`);
    loading.value = false;
    return;
  }
  // The first levels at the same time: the map is drawn once they are in,
  // so a site in alarm is a pin from the start, not first in a cluster (#334).
  const levelsLoad = loadLevels();
  try {
    const data = await $fetch<{ locations: MapLocation[] }>("/api/locations");
    const every = data.locations || [];
    // Only the sites the link asks for: everything follows from here (#367).
    all.value = every.filter((loc) => inScope(loc, scope));
    everyCount.value = every.length;
    if (linkedId && !byId.value.has(linkedId) && every.some((loc) => loc.id === linkedId)) {
      showError("The linked site isn't among the sites this link shows (tenant= / q=): Show all sites to open it.");
    }
    loadingText.value = `Processing ${all.value.length.toLocaleString("en")} locations…`;
    await levelsLoad;
    // Every site, not only the filtered ones; a link to a site flies on from here.
    if (!config.value.map.view) siteMap.fit(all.value);
    draw();
  } catch (err) {
    showError(`Failed to load locations: ${failure(err)}`);
  } finally {
    loading.value = false;
  }
  timer = setInterval(onVisible, ALERT_REFRESH_MS);
  document.addEventListener("visibilitychange", onVisible);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  document.removeEventListener("keydown", onKey);
  document.removeEventListener("visibilitychange", onVisible);
  siteMap?.map.remove();
});
</script>

<template>
  <div class="map-page flex h-dvh flex-col overflow-hidden">
    <AppBar active="map" :search="false" :link-query="linkQuery">
      <template #start>
        <UButton
          id="panel-bar-toggle"
          ref="barToggle"
          class="panel-bar-toggle"
          :icon="panelHidden ? 'i-lucide-panel-left-open' : 'i-lucide-panel-left-close'"
          color="neutral"
          variant="ghost"
          aria-controls="sidebar"
          :aria-expanded="panelHidden ? 'false' : 'true'"
          :aria-label="panelHidden ? 'Show the panel' : 'Hide the panel'"
          :title="`${panelHidden ? 'Show the panel' : 'Hide the panel'} ([)`"
          @click="setPanelHidden(!panelHidden)"
        />
      </template>
    </AppBar>

    <main class="relative min-h-0 flex-1">
      <!-- The left panel (#392): what needs action first, one search, the rest a
           click away.  On a phone, the bottom half. -->
      <div
        id="sidebar"
        ref="sidebar"
        class="absolute z-20 flex flex-col gap-4 overflow-y-auto border-default bg-default p-4 transition-transform duration-200 motion-reduce:transition-none max-sm:inset-x-0 max-sm:bottom-0 max-sm:top-1/2 max-sm:border-t sm:inset-y-0 sm:left-0 sm:w-80 sm:border-r"
        :class="{ 'max-sm:translate-y-full sm:-translate-x-full': panelHidden }"
        :inert="panelHidden"
      >
        <h1 class="sr-only">Map</h1>
        <p v-if="scope && everyCount" id="map-scope" class="map-scope text-sm" :class="{ 'scope-no-match text-warning': scopeNoMatch }">
          <template v-if="scopeNoMatch">No site matches <strong>{{ scopeText }}</strong>. {{ SCOPE_NO_MATCH_HINT }} </template>
          <template v-else>Only <strong>{{ scopeText }}</strong> · </template>
          <a href="/" class="text-primary hover:underline">Show all sites</a>
        </p>
        <MapSearch
          ref="searchBox"
          :locations="all"
          :levels="levels"
          :results="results"
          :busy="searching"
          @pick="pickSite"
          @search="mapSearch"
          @clear="endSearch"
          @open="(id, element) => showSite(id, { trigger: element })"
        />
        <MapAlarms
          :sites="alarms"
          :loaded="levelsLoaded"
          :title="alarmHeading"
          :alarms-only="alarmsOnly"
          @update:alarms-only="setAlarmsOnly"
          @open="openAlarm"
        />
        <MapFilters
          class="mt-auto"
          :model-value="criteria"
          :choices="choices"
          :count="countText"
          @update:model-value="setCriteria"
          @clear="clearFilters()"
        >
          <CoordinateNote v-if="!loading" :query="scopeQuery" :nautobot-url="config.nautobot_url" @show="showMisplaced" />
          <p v-if="levelsFailed" id="alert-levels-note" class="alert-levels-note text-xs text-warning" role="status">
            Alert levels could not be loaded: markers are grey until they can be.
          </p>
        </MapFilters>
      </div>

      <!-- The map's box: beside the panels on a computer, above the panel on a phone. -->
      <div
        class="absolute top-0 right-0 left-0"
        :class="[panelHidden ? 'bottom-0' : 'bottom-1/2 sm:bottom-0 sm:left-80', { 'sm:right-96': selected }]"
      >
        <div id="map" ref="map" class="absolute inset-0 isolate" />
        <MapLegend />
        <!-- Folded, a tab brings the panel back, with the alarm count so nothing is missed. -->
        <button
          v-if="panelHidden"
          id="panel-toggle"
          class="panel-toggle absolute z-10 flex h-10 items-center gap-2 rounded-r-full border border-l-0 border-default bg-default px-3.5 text-sm font-medium shadow-lg hover:bg-elevated max-sm:top-2.5 max-sm:right-2.5 max-sm:rounded-full max-sm:border-l sm:left-0 sm:top-1/2 sm:-translate-y-1/2"
          :class="{ 'has-alarms': tabHasAlarms }"
          type="button"
          aria-controls="sidebar"
          :aria-label="tab.label"
          :title="`${tab.label} ([)`"
          @click="setPanelHidden(false)"
        >
          <span class="size-2.5 rounded-full" :class="tabHasAlarms ? 'bg-error' : 'bg-primary'" aria-hidden="true" />{{ tab.text }}
        </button>
        <div v-if="loading" id="loading-overlay" class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-4 bg-default/60">
          <UIcon name="i-lucide-loader-circle" class="size-10 animate-spin text-primary" />
          <div id="loading-text" class="text-sm text-muted">{{ loadingText }}</div>
        </div>
      </div>

      <!-- On a phone the panel is a sheet over the dimmed map; a tap there closes it (#311). -->
      <div v-if="selected" id="inspector-backdrop" class="fixed inset-0 z-20 bg-black/35 sm:hidden" aria-hidden="true" @click="closeSite(false)" />
      <SitePanel
        ref="panel"
        :loc="selected"
        :group="group"
        :detail="detail"
        :loading="detailLoading"
        :error="detailError"
        :since="selectedId ? levelInfo[selectedId]?.since : null"
        :nautobot-url="config.nautobot_url"
        @close="closeSite()"
        @select="(id) => openSite(id, { ids: groupIds, focusPanel: false })"
        @retry="retryDetail"
      />
    </main>
  </div>
</template>
