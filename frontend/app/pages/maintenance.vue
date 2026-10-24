<!-- The Maintenance page (#343, #353; on Nuxt since #463): what is in
     maintenance now, what is planned, what ended lately, as batches; and
     planning more for many sites at once.  ?view=history: every window of a
     period, for one site or all, and its export. -->
<script setup lang="ts">
import { addDays, formatRange } from "~/utils/calendar";
import { errorMessage, searchWords } from "~/utils/format";
import {
  batchesOf,
  batchMatches,
  countText,
  groupsFor,
  matchPlannableSites,
  prepareSites,
  MAINTENANCE_STEP,
  windowRequest,
  type Batch,
  type BatchGroup,
  type MaintenanceDevice,
  type MaintenanceWindow,
  type PickerSite,
} from "~/utils/maintenance";
import { defaultPlan, localDay, type WhenValues } from "~/utils/when";

useHead({ title: "Maintenance · Nautobot Maps" });

// How often the list refreshes by itself; windows start and end on their own.
const REFRESH_MS = 60 * 1000;

const { data: config } = usePageConfig();
const limits = computed(() => config.value?.maintenance ?? { recent_days: 7, history_days: 30, max_sites: 200 });

// The history's period and site, from the address (a link from the board or
// the map opens one site's); the last 30 days by default.
const params = new URLSearchParams(window.location.search);
const historyView = params.get("view") === "history";
const today = localDay(new Date());
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const history = reactive({
  from: params.get("from") || "",
  to: params.get("to") || today,
  siteId: params.get("site_id") || "",
  siteName: "",
});

// --- the list ---------------------------------------------------------------

// Hundreds of windows: not deeply reactive.
const batches = shallowRef<Batch[]>([]);
const descriptions = shallowRef<Record<string, string>>({});
const status = ref("Loading…");
const statusError = ref(false);
const now = ref(new Date());
const query = ref("");
const tenantFilter = ref<string[]>([]);
const shownCount = ref<Partial<Record<BatchGroup | "history", number>>>({});
const pastOpen = ref(false);

const tenants = computed(() => [...new Set(batches.value.flatMap((batch) => batch.tenants))].sort());
const filtered = computed(() => {
  const criteria = { words: searchWords(query.value), tenants: tenantFilter.value };
  return batches.value.filter((batch) => batchMatches(batch, criteria));
});
const groups = computed(() =>
  groupsFor(limits.value.recent_days).map((group) => {
    const members = filtered.value.filter((batch) => batch.group === group.id);
    const limit = shownCount.value[group.id] || MAINTENANCE_STEP;
    return { ...group, members, shown: members.slice(0, limit), rest: Math.max(0, members.length - limit) };
  }),
);
const historyList = computed(() => {
  const limit = shownCount.value.history || MAINTENANCE_STEP;
  return { shown: filtered.value.slice(0, limit), rest: Math.max(0, filtered.value.length - limit) };
});
watch([query, tenantFilter], () => {
  shownCount.value = {};
});
function showMore(group: BatchGroup | "history") {
  shownCount.value = { ...shownCount.value, [group]: (shownCount.value[group] || MAINTENANCE_STEP) + MAINTENANCE_STEP };
}

function historyQuery(extra: Record<string, string> = {}) {
  const search = new URLSearchParams({ from: history.from, to: history.to, tz: zone, ...extra });
  if (history.siteId) search.set("site_id", history.siteId);
  return search;
}
// The export: the same period, site and tenants (the search is the page's own).
const csvHref = computed(() => {
  const search = historyQuery();
  tenantFilter.value.forEach((tenant) => search.append("tenant", tenant));
  return `/api/maintenance/history.csv?${search}`;
});

function setStatus(text: string, isError = false) {
  status.value = text;
  statusError.value = isError;
}

async function loadList() {
  try {
    const url = historyView ? `/api/maintenance/history?${historyQuery()}` : `/api/maintenance?recent_days=${limits.value.recent_days}`;
    const payload = await $fetch<{ windows: MaintenanceWindow[]; tenant_descriptions: Record<string, string>; truncated: boolean }>(url);
    const windows = payload.windows || [];
    batches.value = batchesOf(windows, { keepOrder: historyView });
    descriptions.value = payload.tenant_descriptions || {};
    now.value = new Date();
    if (historyView && windows[0]?.site_name) history.siteName = windows[0].site_name;
    const cut = payload.truncated ? " · only the first windows are shown: choose a shorter period or a site" : "";
    if (historyView) {
      setStatus(`${windows.length.toLocaleString("en-GB")} window${windows.length === 1 ? "" : "s"} · ${formatRange(history.from, history.to)}${cut}`);
      return;
    }
    const count = (group: BatchGroup) => batches.value.filter((batch) => batch.group === group).length;
    const time = now.value.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    setStatus(`${count("active")} active · ${count("upcoming")} planned · updated ${time}${cut}`);
  } catch (err) {
    setStatus(`Could not load maintenance: ${errorMessage(err)}`, true);
  }
}

// The address keeps the period and site, so a history can be shared or bookmarked.
function historyChanged() {
  const search = historyQuery({ view: "history" });
  search.delete("tz");
  window.history.replaceState(null, "", `/maintenance?${search}`);
  shownCount.value = {};
  loadList();
}
function pickPeriod(from: string, to: string) {
  Object.assign(history, { from, to });
  historyChanged();
}
function everySite() {
  history.siteId = "";
  historyChanged();
}

async function endWindows(ids: number[]) {
  try {
    await $fetch("/api/maintenance/end", { method: "POST", body: { ids } });
  } catch (err) {
    setStatus(`Could not end maintenance: ${errorMessage(err)}`, true);
  }
  await loadList();
}

// --- planning ---------------------------------------------------------------

const planOpen = ref(false);
const sites = shallowRef<PickerSite[] | null>(null);
const byId = computed(() => new Map((sites.value ?? []).map((site) => [site.id, site])));
const siteQuery = ref("");
const matchesOpen = ref(false);
const chosen = ref<string[]>([]);
const showAllChosen = ref(false);
const scope = ref<"site" | "devices">("site");
const devices = shallowRef<MaintenanceDevice[] | null>(null);
const devicesError = ref("");
const deviceIds = ref<string[]>([]);
const reason = ref("");
const planError = ref("");
const saving = ref(false);
const when = ref<WhenValues>({ when: "now", duration: 120, ...defaultPlan(new Date()) });
const siteSearch = useTemplateRef<{ inputRef: HTMLInputElement | null }>("siteSearch");
const matchList = useTemplateRef<HTMLUListElement>("matchList");

const matches = computed(() =>
  sites.value && siteQuery.value.trim() ? matchPlannableSites(sites.value, siteQuery.value, new Set(chosen.value)) : null,
);
const chosenShown = computed(() => (showAllChosen.value ? chosen.value : chosen.value.slice(0, 12)));

async function openPlan() {
  planOpen.value = true;
  await nextTick();
  siteSearch.value?.inputRef?.focus();
  if (sites.value) return;
  try {
    const data = await $fetch<{ sites: PickerSite[] }>("/api/maintenance/sites");
    sites.value = prepareSites(data.sites || []);
  } catch (err) {
    planError.value = `Could not load the sites: ${errorMessage(err)}`;
  }
}
function closePlan() {
  planOpen.value = false;
}

function addIds(ids: string[]) {
  chosen.value = [...chosen.value, ...ids.filter((id) => !chosen.value.includes(id))];
  siteQuery.value = "";
  matchesOpen.value = false;
  siteSearch.value?.inputRef?.focus();
}
function removeSite(id: string) {
  chosen.value = chosen.value.filter((chosenId) => chosenId !== id);
}
function onSiteKey(event: KeyboardEvent) {
  if (event.key === "Escape") matchesOpen.value = false;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    matchList.value?.querySelector<HTMLElement>(".maint-match")?.focus();
  }
  if (event.key === "Enter") {
    event.preventDefault();
    matchList.value?.querySelector<HTMLElement>(".maint-match")?.click();
  }
}
function onMatchKey(event: KeyboardEvent) {
  const match = (event.target as HTMLElement).closest<HTMLElement>(".maint-match");
  if (!match) return;
  if (event.key === "Enter") {
    event.preventDefault();
    match.click();
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const next = (event.key === "ArrowDown" ? match.nextElementSibling : match.previousElementSibling) as HTMLElement | null;
    if (next?.matches(".maint-match")) next.focus();
    else if (event.key === "ArrowUp") siteSearch.value?.inputRef?.focus();
  } else if (event.key === "Escape") {
    matchesOpen.value = false;
    siteSearch.value?.inputRef?.focus();
  }
}

// "Some devices": one site's, loaded when chosen.
watch([scope, chosen], async () => {
  if (chosen.value.length !== 1 && scope.value === "devices") scope.value = "site";
  if (scope.value !== "devices") return;
  const siteId = chosen.value[0]!;
  devices.value = null;
  devicesError.value = "";
  deviceIds.value = [];
  try {
    const data = await $fetch<{ devices: MaintenanceDevice[] }>(`/api/maintenance/devices?${new URLSearchParams({ site_id: siteId })}`);
    if (chosen.value[0] === siteId) devices.value = data.devices || [];
  } catch (err) {
    devicesError.value = `Could not load devices: ${errorMessage(err)}`;
  }
});
function toggleDevice(id: string, on: boolean) {
  deviceIds.value = on ? [...deviceIds.value, id] : deviceIds.value.filter((deviceId) => deviceId !== id);
}

async function save() {
  const body = windowRequest(
    { siteIds: chosen.value, scope: scope.value, deviceIds: deviceIds.value, reason: reason.value, ...when.value },
    limits.value.max_sites,
  );
  if (body.error) {
    planError.value = body.error;
    return;
  }
  planError.value = "";
  saving.value = true;
  try {
    const { windows } = await $fetch<{ windows: unknown[] }>("/api/maintenance", { method: "POST", body });
    chosen.value = [];
    showAllChosen.value = false;
    reason.value = "";
    await loadList();
    setStatus(`Saved: ${windows.length} window${windows.length === 1 ? "" : "s"}. ${status.value}`);
  } catch (err) {
    planError.value = `Could not save: ${errorMessage(err)}`;
  } finally {
    saving.value = false;
  }
}

// --- start ------------------------------------------------------------------

let timer: ReturnType<typeof setInterval> | undefined;
function onVisible() {
  if (!document.hidden) loadList();
}
onMounted(async () => {
  // The page's limits first: the Ended group's days, the history's default period.
  await until(() => config.value?.maintenance);
  if (historyView && !history.from) history.from = addDays(today, 1 - limits.value.history_days);
  loadList();
  if (historyView) return;
  timer = setInterval(() => {
    // Not in a hidden tab, nor while a batch waits for its second click: the redraw would drop it.
    if (!document.hidden && !document.querySelector(".maint-end.is-confirm")) loadList();
  }, REFRESH_MS);
  document.addEventListener("visibilitychange", onVisible);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  document.removeEventListener("visibilitychange", onVisible);
});

function until(ready: () => unknown): Promise<void> {
  return new Promise((resolve) => {
    if (ready()) return resolve();
    const stop = watch(ready, (value) => {
      if (value) {
        stop();
        resolve();
      }
    });
  });
}
</script>

<template>
  <div>
    <AppBar active="maintenance" />
    <main class="mx-auto max-w-6xl space-y-4 px-4 py-6">
      <h1 class="text-2xl font-bold text-highlighted">Maintenance</h1>

      <!-- Now and planned (#343), or the history of any period (#353). -->
      <nav class="maint-tabs flex gap-1 border-b border-default" aria-label="Maintenance views">
        <a
          v-for="tab in [{ href: '/maintenance', label: 'Now & planned', on: !historyView }, { href: '/maintenance?view=history', label: 'History', on: historyView }]"
          :key="tab.href"
          class="maint-tab -mb-px border-b-2 px-3 py-2 text-sm font-medium"
          :class="tab.on ? 'border-primary text-primary' : 'border-transparent text-muted hover:text-highlighted'"
          :href="tab.href"
          :aria-current="tab.on ? 'page' : undefined"
        >{{ tab.label }}</a>
      </nav>

      <!-- The history's period, site and export (#353). -->
      <section v-if="historyView" id="maint-history-bar" class="flex flex-wrap items-center gap-3">
        <span class="text-sm text-muted">Period</span>
        <PeriodPicker v-if="history.from" :from="history.from" :to="history.to" :max="today" :today="today" @pick="pickPeriod" />
        <span v-if="history.siteId" id="maint-site-filter" class="maint-chip inline-flex items-center gap-1 rounded-full bg-elevated px-3 py-1 text-sm">
          <span>Site: {{ history.siteName || history.siteId }}</span>
          <button type="button" class="maint-chip-remove text-muted hover:text-highlighted" aria-label="Show every site" @click="everySite">
            <UIcon name="i-lucide-x" class="size-3.5" />
          </button>
        </span>
        <UButton id="maint-history-csv" :to="csvHref" external download color="neutral" variant="outline" icon="i-lucide-download">
          Download CSV
        </UButton>
      </section>

      <!-- Search, tenants, plan. -->
      <section class="flex flex-wrap items-center gap-3">
        <UInput
          id="maint-search"
          v-model="query"
          type="search"
          icon="i-lucide-search"
          placeholder="Search site, device, reason or person"
          aria-label="Search maintenance"
          autocomplete="off"
          class="w-full sm:w-80"
        />
        <USelectMenu
          id="maint-tenant"
          v-model="tenantFilter"
          :items="tenants"
          multiple
          placeholder="All tenants"
          aria-label="Tenant"
          class="w-full sm:w-56"
        />
        <UButton
          v-if="!historyView"
          id="maint-plan-open"
          icon="i-lucide-calendar-plus"
          :aria-expanded="planOpen"
          aria-controls="maint-plan"
          @click="planOpen ? closePlan() : openPlan()"
        >Plan maintenance</UButton>
        <p id="maint-status" class="text-sm" :class="statusError ? 'text-error' : 'text-muted'" role="status">{{ status }}</p>
      </section>

      <!-- Plan: sites (search, or every site of a tenant), what, when, why (#343). -->
      <UCard v-if="planOpen" id="maint-plan" aria-labelledby="maint-plan-title">
        <template #header>
          <h2 id="maint-plan-title" class="font-semibold text-highlighted">Plan maintenance</h2>
        </template>
        <form id="maint-plan-form" class="space-y-5" novalidate @submit.prevent="save">
          <fieldset class="space-y-2">
            <legend class="mb-1 text-sm font-semibold text-highlighted">Sites</legend>
            <div class="relative max-w-md" @focusout="($event.currentTarget as HTMLElement).contains($event.relatedTarget as Node) || (matchesOpen = false)">
              <UInput
                id="maint-site-search"
                ref="siteSearch"
                v-model="siteQuery"
                type="search"
                icon="i-lucide-search"
                :placeholder="sites ? `Site, path or tenant (${sites.length.toLocaleString('en-GB')} sites)` : 'Loading sites…'"
                aria-label="Find a site"
                aria-controls="maint-site-matches"
                autocomplete="off"
                class="w-full"
                @input="matchesOpen = true"
                @keydown="onSiteKey"
              />
              <ul
                v-if="matchesOpen && matches"
                id="maint-site-matches"
                ref="matchList"
                class="absolute inset-x-0 top-full z-20 mt-1 max-h-80 overflow-y-auto rounded-md border border-default bg-default py-1 shadow-lg"
                role="listbox"
                aria-label="Matching sites"
                @keydown="onMatchKey"
              >
                <li
                  v-for="tenant in matches.tenants"
                  :key="`t-${tenant.name}`"
                  role="option"
                  class="maint-match maint-match-tenant cursor-pointer px-3 py-1.5 text-sm hover:bg-elevated focus:bg-elevated focus:outline-none"
                  tabindex="-1"
                  @click="addIds(tenant.ids)"
                >Add all {{ tenant.ids.length }} sites of <strong>{{ tenant.name }}</strong></li>
                <li
                  v-for="site in matches.sites"
                  :key="site.id"
                  role="option"
                  class="maint-match cursor-pointer px-3 py-1.5 text-sm hover:bg-elevated focus:bg-elevated focus:outline-none"
                  tabindex="-1"
                  @click="addIds([site.id])"
                >
                  <strong>{{ site.name }}</strong>
                  <span v-if="site.path" class="text-muted"> {{ site.path }}</span>
                  <span v-if="site.maintenance" class="text-muted"> · in maintenance now</span>
                </li>
                <li v-if="matches.more" class="px-3 py-1.5 text-sm text-muted" aria-disabled="true">
                  {{ matches.more }} more: type more of the name
                </li>
                <li v-if="!matches.tenants.length && !matches.sites.length" class="px-3 py-1.5 text-sm text-muted" aria-disabled="true">
                  No site matches.
                </li>
              </ul>
            </div>
            <ul id="maint-chosen" class="flex flex-wrap items-center gap-1.5" aria-label="Chosen sites">
              <li v-for="id in chosenShown" :key="id" class="maint-chip inline-flex items-center gap-1 rounded-full bg-elevated px-3 py-1 text-sm">
                <span>{{ byId.get(id)?.name || id }}</span>
                <button type="button" class="maint-chip-remove text-muted hover:text-highlighted" :aria-label="`Remove ${byId.get(id)?.name || id}`" @click="removeSite(id)">
                  <UIcon name="i-lucide-x" class="size-3.5" />
                </button>
              </li>
              <li v-if="chosen.length > chosenShown.length">
                <UButton size="xs" color="neutral" variant="link" @click="showAllChosen = true">+{{ chosen.length - chosenShown.length }} more</UButton>
              </li>
              <li v-if="chosen.length > 1">
                <UButton size="xs" color="neutral" variant="link" @click="chosen = []">Clear</UButton>
              </li>
            </ul>
          </fieldset>

          <fieldset class="space-y-2">
            <legend class="mb-1 text-sm font-semibold text-highlighted">What</legend>
            <label class="flex items-center gap-2 text-sm">
              <input v-model="scope" type="radio" name="maint-scope" value="site" class="accent-(--ui-primary)" > Whole sites
            </label>
            <label class="flex items-center gap-2 text-sm" :class="{ 'opacity-50': chosen.length !== 1 }">
              <input v-model="scope" type="radio" name="maint-scope" value="devices" class="accent-(--ui-primary)" :disabled="chosen.length !== 1" >
              Some devices <span class="text-muted">(one site)</span>
            </label>
            <div v-if="scope === 'devices'" class="maint-devices max-h-64 space-y-1 overflow-y-auto rounded-md border border-default p-2 text-sm">
              <p v-if="devicesError" class="text-error">{{ devicesError }}</p>
              <p v-else-if="!devices" class="text-muted">Loading devices…</p>
              <p v-else-if="!devices.length" class="text-muted">No monitored devices.</p>
              <label v-for="device in devices" v-else :key="device.id" class="flex flex-wrap items-center gap-2">
                <input
                  type="checkbox"
                  class="maint-device accent-(--ui-primary)"
                  :value="device.id"
                  :checked="deviceIds.includes(device.id)"
                  @change="toggleDevice(device.id, ($event.target as HTMLInputElement).checked)"
                >
                <span>{{ device.name || device.id }}</span>
                <span class="text-muted">{{ [device.location_path, device.role, device.status].filter(Boolean).join(" · ") }}</span>
              </label>
            </div>
          </fieldset>

          <WhenField v-model="when" />

          <div class="max-w-md space-y-1">
            <label class="text-sm font-semibold text-highlighted" for="maint-plan-reason">Reason</label>
            <UInput id="maint-plan-reason" v-model="reason" maxlength="200" autocomplete="off" placeholder="e.g. Core switch upgrade" class="w-full" />
          </div>
          <p v-if="planError" class="maint-error text-sm text-error" role="alert">{{ planError }}</p>
          <div class="flex gap-2">
            <UButton class="maint-save-btn" type="submit" :loading="saving">
              {{ when.when === "planned" ? "Plan maintenance" : "Start maintenance" }}
            </UButton>
            <UButton id="maint-plan-cancel" color="neutral" variant="outline" @click="closePlan">Close</UButton>
          </div>
        </form>
      </UCard>

      <!-- The history: every batch in the period, newest first, read only. -->
      <section v-if="historyView" class="maint-group overflow-hidden rounded-lg border border-default" data-group="history">
        <h2 class="flex items-center gap-2 bg-elevated/50 px-4 py-3 font-semibold text-highlighted">
          History · {{ formatRange(history.from, history.to) }}
          <UBadge v-if="filtered.length" color="neutral" variant="subtle" size="sm">{{ countText(filtered) }}</UBadge>
        </h2>
        <p v-if="!filtered.length" class="border-t border-default px-4 py-3 text-sm text-muted">No maintenance in this period.</p>
        <ul v-else class="divide-y divide-default border-t border-default">
          <MaintenanceBatch v-for="batch in historyList.shown" :key="batch.key" :batch="batch" :descriptions="descriptions" :now="now" history />
        </ul>
        <p v-if="historyList.rest" class="border-t border-default px-4 py-2 text-sm text-muted">
          {{ historyList.rest }} more
          <UButton class="maint-more ml-2" size="xs" color="neutral" variant="outline" @click="showMore('history')">
            Show {{ Math.min(MAINTENANCE_STEP, historyList.rest) }} more
          </UButton>
        </p>
      </section>

      <!-- Active now, Planned, and Ended (folded unless opened). -->
      <div v-else id="maint-groups" class="space-y-3">
        <component
          :is="group.id === 'past' ? 'details' : 'section'"
          v-for="group in groups"
          :key="group.id"
          class="maint-group group overflow-hidden rounded-lg border border-default"
          :data-group="group.id"
          :open="group.id === 'past' ? pastOpen : undefined"
          @toggle="group.id === 'past' && (pastOpen = ($event.target as HTMLDetailsElement).open)"
        >
          <component
            :is="group.id === 'past' ? 'summary' : 'h2'"
            class="flex items-center gap-2 bg-elevated/50 px-4 py-3 font-semibold text-highlighted"
            :class="{ 'cursor-pointer list-none': group.id === 'past' }"
          >
            <UIcon v-if="group.id === 'past'" name="i-lucide-chevron-right" class="size-4 text-muted transition-transform group-open:rotate-90" />
            {{ group.title }}
            <UBadge v-if="group.members.length" color="neutral" variant="subtle" size="sm">{{ countText(group.members) }}</UBadge>
          </component>
          <p v-if="!group.members.length" class="border-t border-default px-4 py-3 text-sm text-muted">{{ group.empty }}</p>
          <ul v-else class="divide-y divide-default border-t border-default">
            <MaintenanceBatch
              v-for="batch in group.shown"
              :key="batch.key"
              :batch="batch"
              :descriptions="descriptions"
              :now="now"
              @end="endWindows"
            />
          </ul>
          <p v-if="group.rest" class="border-t border-default px-4 py-2 text-sm text-muted">
            {{ group.rest }} more
            <UButton class="maint-more ml-2" size="xs" color="neutral" variant="outline" @click="showMore(group.id)">
              Show {{ Math.min(MAINTENANCE_STEP, group.rest) }} more
            </UButton>
          </p>
        </component>
      </div>
    </main>
  </div>
</template>
