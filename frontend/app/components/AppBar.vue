<!-- One top bar on every staff page (#392, #419; on Nuxt since #463): the
     app's name, find a site, how fresh the data is, who is signed in, the
     theme and the pages in a menu.  The ids and classes the e2e tests use are
     the old bar's (templates/_nav.html). -->
<script setup lang="ts">
import type { DropdownMenuItem } from "@nuxt/ui";
import { APP_STATUS_REFRESH_MS, appStatusView, isSearchShortcut, type AppStatus } from "~/utils/appstatus";
import { matchSites, type SearchableSite } from "~/utils/sitesearch";

const props = withDefaults(
  defineProps<{
    active: string;
    // The map's panel has its own search, for places too.
    search?: boolean;
    // ?tenant= / ?q= the map's and the board's links keep (#367).
    linkQuery?: string;
  }>(),
  { search: true, linkQuery: "" },
);

const { data: config } = usePageConfig();
const { dark, toggle: toggleTheme } = useTheme();

// The menu: every page, this one marked.
const menu = computed<DropdownMenuItem[]>(() =>
  (config.value?.pages ?? []).map((page) => ({
    label: page.label,
    to: scoped(page.href),
    external: true,
    active: page.key === props.active,
    "aria-current": page.key === props.active ? "page" : undefined,
    class: "app-nav-link",
  })),
);

// The map and the board keep the scope; the wall ("view=wall") too, after it.
function scoped(href: string): string {
  const [path, query = ""] = href.split("?");
  if (!props.linkQuery || (path !== "/" && path !== "/alerts")) return href;
  return `${path}?${[query, props.linkQuery].filter(Boolean).join("&")}`;
}

// How fresh the data is: once a minute, not while the tab is hidden.
const status = shallowRef<AppStatus | null | undefined>(undefined);
const view = computed(() => (status.value === undefined ? null : appStatusView(status.value)));
async function refreshStatus() {
  try {
    status.value = await $fetch<AppStatus>("/api/app-status");
  } catch {
    status.value = null;
  }
}
const DOT: Record<string, string> = { ok: "bg-success", stale: "bg-warning", down: "bg-error" };

// Find a site: the sites fetched once, the first time the search is used.
const query = ref("");
const searchOpen = ref(false);
// Nuxt UI's UInput; its <input> is its inputRef.
const inputBox = useTemplateRef<{ inputRef: HTMLInputElement | null }>("input");
const input = computed(() => inputBox.value?.inputRef ?? null);
const results = useTemplateRef<HTMLUListElement>("results");
const sites = shallowRef<SearchableSite[] | null>(null);
const loadError = ref(false);
let loading: Promise<void> | null = null;
function loadSites() {
  loading ??= $fetch<{ locations: SearchableSite[] }>("/api/locations")
    .then((data) => {
      sites.value = data.locations || [];
      loadError.value = false;
    })
    .catch(() => {
      loadError.value = true;
      loading = null;
    });
  return loading;
}
const found = computed(() => (sites.value && query.value.trim() ? matchSites(sites.value, query.value) : null));
const showResults = computed(() => Boolean(query.value.trim()));

function mapHref(site: SearchableSite) {
  return `/?location_id=${encodeURIComponent(site.id)}`;
}
function boardHref(site: SearchableSite) {
  return `/alerts?q=${encodeURIComponent(site.name || site.id)}`;
}
function hits(): HTMLAnchorElement[] {
  return Array.from(results.value?.querySelectorAll<HTMLAnchorElement>(".bar-hit-map") ?? []);
}
function openSearch() {
  searchOpen.value = true;
  nextTick(() => input.value?.focus());
}
function closeSearch() {
  query.value = "";
  searchOpen.value = false;
}
function onInputKey(event: KeyboardEvent) {
  if (event.key === "ArrowDown" && showResults.value) {
    event.preventDefault();
    hits()[0]?.focus();
  } else if (event.key === "Enter") {
    // The first site, as the map's search does.
    const first = hits()[0];
    if (first) window.location.href = first.href;
  } else if (event.key === "Escape") {
    event.stopPropagation();
    closeSearch();
    input.value?.blur();
  }
}
function onResultsKey(event: KeyboardEvent) {
  const all = hits();
  const at = all.indexOf(document.activeElement as HTMLAnchorElement);
  if (event.key === "ArrowDown" && at < all.length - 1) all[at + 1]!.focus();
  else if (event.key === "ArrowUp") (at > 0 ? all[at - 1]! : input.value)?.focus();
  else if (event.key === "Escape") {
    event.stopPropagation();
    closeSearch();
    input.value?.focus();
  } else return;
  event.preventDefault();
}
function onSearchFocusOut(event: FocusEvent) {
  const box = (event.currentTarget as HTMLElement) ?? null;
  if (!box?.contains(event.relatedTarget as Node | null)) closeSearch();
}
function onShortcut(event: KeyboardEvent) {
  // No search here (the map has its own): "/" is left alone.
  if (!props.search || !isSearchShortcut(event)) return;
  event.preventDefault();
  openSearch();
}

let timer: ReturnType<typeof setInterval> | undefined;
function onVisible() {
  if (!document.hidden) refreshStatus();
}
onMounted(() => {
  refreshStatus();
  timer = setInterval(onVisible, APP_STATUS_REFRESH_MS);
  document.addEventListener("visibilitychange", onVisible);
  document.addEventListener("keydown", onShortcut);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  document.removeEventListener("visibilitychange", onVisible);
  document.removeEventListener("keydown", onShortcut);
});
</script>

<template>
  <header class="app-bar sticky top-0 z-40 flex h-13 items-center gap-2 border-b border-default bg-default/95 px-3 backdrop-blur sm:gap-4 sm:px-4">
    <slot name="start" />
    <a class="app-brand flex shrink-0 items-center gap-1.5 font-semibold text-primary" href="/" title="Map">
      <UIcon name="i-lucide-map-pin" class="size-5" />
      <span class="hidden sm:inline">Nautobot Maps</span>
    </a>

    <!-- Find a site: an icon on a phone, opening the field over the bar. -->
    <div
      v-if="search"
      class="bar-search relative min-w-0 flex-1 sm:max-w-md"
      role="search"
      @focusout="onSearchFocusOut"
    >
      <UButton
        id="bar-search-open"
        class="sm:hidden"
        :class="{ hidden: searchOpen }"
        icon="i-lucide-search"
        color="neutral"
        variant="ghost"
        aria-label="Find a site"
        title="Find a site (/)"
        @click="openSearch"
      />
      <div
        :class="searchOpen ? 'absolute inset-x-0 top-1/2 z-10 -translate-y-1/2' : 'hidden sm:block'"
      >
        <UInput
          id="bar-search"
          ref="input"
          v-model="query"
          type="search"
          icon="i-lucide-search"
          placeholder="Find a site  /"
          autocomplete="off"
          :spellcheck="false"
          aria-label="Find a site"
          aria-controls="bar-search-results"
          class="w-full"
          @focus="loadSites"
          @keydown="onInputKey"
        />
      </div>
      <ul
        v-if="showResults"
        id="bar-search-results"
        ref="results"
        class="bar-search-results absolute inset-x-0 top-full z-50 mt-1 max-h-96 overflow-y-auto rounded-md border border-default bg-default py-1 shadow-lg"
        aria-label="Sites"
        @keydown="onResultsKey"
      >
        <li v-if="loadError" class="px-3 py-2 text-sm text-muted">The sites could not be loaded: try again.</li>
        <li v-else-if="!found" class="px-3 py-2 text-sm text-muted">Loading the sites…</li>
        <li v-else-if="!found.matches.length" class="px-3 py-2 text-sm text-muted">No site matches</li>
        <template v-else>
          <li v-for="site in found.matches" :key="site.id" class="bar-hit flex items-center gap-2 px-1">
            <a
              class="bar-hit-map min-w-0 flex-1 rounded px-2 py-1.5 hover:bg-elevated focus:bg-elevated focus:outline-none"
              :href="mapHref(site)"
              title="Show on the map"
            >
              <span class="block truncate font-medium text-highlighted">{{ site.name || site.id }}</span>
              <span v-if="site.ancestor_path || site.physical_address" class="block truncate text-xs text-muted">
                {{ site.ancestor_path || site.physical_address }}
              </span>
            </a>
            <a
              class="bar-hit-board shrink-0 rounded px-2 py-1 text-xs text-primary hover:bg-elevated"
              :href="boardHref(site)"
              :aria-label="`${site.name || site.id} on the board`"
            >Board</a>
          </li>
          <li v-if="found.more" class="px-3 py-2 text-sm text-muted">
            {{ found.more.toLocaleString("en") }} more: keep typing
          </li>
        </template>
      </ul>
    </div>

    <div class="app-actions ml-auto flex shrink-0 items-center gap-1 sm:gap-2" :class="{ 'max-sm:hidden': searchOpen }">
      <!-- How fresh the data is; a click explains it. -->
      <div v-if="view" id="app-status" :class="`app-status ${view.state}`">
      <UPopover>
        <UButton color="neutral" variant="ghost" :aria-label="`Data: ${view.text}`" title="How fresh the data is">
          <span class="size-2 shrink-0 rounded-full" :class="DOT[view.state]" aria-hidden="true" />
          <span
            id="app-status-text"
            class="max-w-72 truncate max-sm:hidden"
            :class="view.state === 'down' ? 'text-error' : view.state === 'stale' ? 'text-warning' : 'text-muted'"
          >{{ view.text }}</span>
        </UButton>
        <template #content>
          <div id="app-status-detail" class="w-72 space-y-2 p-3 text-sm">
            <p>{{ view.intro }}</p>
            <p v-for="line in view.lines" :key="line.what" class="flex justify-between gap-3" :class="`app-status-${line.state}`">
              <span :class="line.state === 'ok' ? '' : line.state === 'down' ? 'text-error' : 'text-warning'">{{ line.what }}</span>
              <span v-if="line.when" class="shrink-0 text-muted">{{ line.when }}</span>
            </p>
          </div>
        </template>
      </UPopover>
      </div>

      <span
        v-if="config?.user"
        class="app-user truncate text-sm text-muted max-md:hidden"
        :title="`Signed in as ${config.user.name}${config.user.role ? ` (${config.user.role})` : ''}`"
      >
        <span class="font-medium text-toned">{{ config.user.name }}</span><span v-if="config.user.role"> · {{ config.user.role }}</span>
      </span>

      <UButton
        id="theme-toggle"
        :icon="dark ? 'i-lucide-sun' : 'i-lucide-moon'"
        color="neutral"
        variant="ghost"
        :aria-label="dark ? 'Switch to light mode' : 'Switch to dark mode'"
        :aria-pressed="dark"
        :title="dark ? 'Light mode' : 'Dark mode'"
        @click="toggleTheme"
      />

      <!-- Not modal: a click outside closes it and still reaches the page, as the
           old menu's did (a modal one, after a filter list was used, could miss it). -->
      <div class="app-menu">
        <UDropdownMenu :items="menu" :modal="false" :content="{ align: 'end' }">
          <UButton icon="i-lucide-menu" color="neutral" variant="ghost" aria-label="Pages" title="Pages" />
        </UDropdownMenu>
      </div>
    </div>
  </header>
</template>
