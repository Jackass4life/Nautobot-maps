<!-- The alert board (#311; on Nuxt since #463): one line of what is wrong,
     the chips that choose which sites are listed (Alarms first), a filter
     box and the tenants; the rest of the filters folded away.  Each row has
     one action, "+ Case"; the rest is behind "⋯".  ?tenant= / ?q= show some
     sites only (#364).  The wall is AlertWall.vue. -->
<script setup lang="ts">
import {
  DEFAULT_SORT,
  FEED_COLLAPSED_KEY,
  ROWS_STEP,
  shownChips,
  SORTS,
  SORT_KEY,
  ackRequestBody,
  boardChoices,
  boardStatusText,
  boardSummaryText,
  chipCounts,
  emptyBoardText,
  feedStartsCollapsed,
  filterSites,
  filtersInUse,
  filtersLeftOffTheWall,
  formatCountdown,
  hasActiveAlarm,
  isSort,
  moreFiltersInUse,
  noBoardCriteria,
  scopeSummary,
  siteReport,
  wallLinkFor,
  type Board,
  type BoardCriteria,
  type BoardSite,
  type Incident,
  type SeverityFilter,
} from "~/utils/board";
import { errorMessage } from "~/utils/format";
import { inScope, linkParams, readScope, scopeLabel, scopeParams } from "~/utils/scope";
import { CHOICE_SEARCH_FIELDS, choiceItems, mergedDescriptions } from "~/utils/tenants";

useHead({ title: "Alert Board · Nautobot Maps" });

const { data: config } = usePageConfig();
const caseTemplate = computed(() => config.value?.board?.case_url_template || "");
const levels = computed<Record<string, string>>(() => config.value?.board?.levels || {});
const toast = useToast();
function showError(text: string) {
  toast.add({ title: text, color: "error", icon: "i-lucide-circle-alert" });
}

function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function writeStored(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // Not remembered; works for this page.
  }
}

// ?tenant= (repeatable) and ?q=: a board for some sites only (#364).
const scope = readScope(window.location.search);
const scopeQuery = scopeParams(scope).toString();

// The operator's last Sort choice (#228).
const storedSort = readStored(SORT_KEY);
const criteria = ref<BoardCriteria>(noBoardCriteria(isSort(storedSort) ? storedSort : DEFAULT_SORT));
watch(
  () => criteria.value.sort,
  (sort) => writeStored(SORT_KEY, sort === DEFAULT_SORT ? null : sort),
);
const moreOpen = ref(false);

// Up to 1,200 sites: not deeply reactive.
const board = shallowRef<Board | null>(null);
const sites = shallowRef<BoardSite[]>([]);
const loading = ref(false);
const loadFailed = ref("");
const loadedAt = ref(Date.now());
const feedVersion = ref(0);

const scopeText = computed(() => scopeLabel(scope, sites.value));
const summary = computed(() => (scope ? scopeSummary(sites.value) : board.value?.summary || {}));
const counts = computed(() => chipCounts(summary.value));
const choices = computed(() => boardChoices(sites.value));
// A tenant is found by its description too (#497).
const tenantItems = computed(() => choiceItems(choices.value.tenant, mergedDescriptions(sites.value)));
const shown = computed(() => filterSites(sites.value, criteria.value));
const rowLimit = ref(ROWS_STEP);
const rows = computed(() => shown.value.slice(0, rowLimit.value));
const empty = computed(() =>
  board.value && !shown.value.length
    ? emptyBoardText(board.value, { inScope: sites.value.length, severity: criteria.value.severity, scopeText: scopeText.value }, sites.value.some(hasActiveAlarm))
    : null,
);
const statusText = computed(() => {
  if (loading.value && !board.value) return "Loading alert board…";
  if (loadFailed.value) return "Alert board unavailable";
  return board.value ? boardStatusText(board.value, shown.value.length, criteria.value.severity === "alarms") : "";
});
const wallLink = computed(() => wallLinkFor({ tenant: criteria.value.tenant, text: criteria.value.site, scope }));
const linkQuery = computed(() => linkParams(criteria.value.tenant, scope).toString());
const moreCount = computed(() => moreFiltersInUse(criteria.value));
// New filters start again at the first rows; a refresh keeps what was opened.
watch(criteria, () => (rowLimit.value = ROWS_STEP));

function set<K extends keyof BoardCriteria>(key: K, value: BoardCriteria[K]) {
  criteria.value = { ...criteria.value, [key]: value };
}
function setSeverity(filter: SeverityFilter) {
  set("severity", filter);
}

// Sites the operator opened.
const expanded = ref(new Set<string>());
function toggleSite(id: string) {
  const next = new Set(expanded.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  expanded.value = next;
}

// Board loads can overlap (the countdown, sync polling, Update now); only the
// newest may change the page.
let loadSeq = 0;
// While a sync runs, poll quietly so fresh data appears on its own.
const SYNC_POLL_MS = 5000;
const SYNC_POLL_MAX = 36; // ~3 minutes
let syncTimer: ReturnType<typeof setTimeout> | null = null;
let syncPolls = 0;
function stopSyncPolling() {
  if (syncTimer) clearTimeout(syncTimer);
  syncTimer = null;
}

async function load(force = false, { background = false } = {}) {
  const seq = ++loadSeq;
  if (!background) {
    // A load the operator asked for restarts the polling budget.
    stopSyncPolling();
    syncPolls = 0;
    loading.value = true;
  }
  try {
    const params = new URLSearchParams();
    // The server only accepts 1/true/yes/refresh (#121).
    if (force) params.set("refresh", "1");
    if (criteria.value.nonOperational) params.set("include_non_operational", "1");
    const query = params.toString();
    const answer = await $fetch<Board>(`/api/alerts${query ? `?${query}` : ""}`);
    if (seq !== loadSeq) return;
    board.value = answer;
    // Only the sites a link's ?tenant= / ?q= asks for (#364).
    sites.value = scope ? answer.alerts.filter((site) => inScope(site, scope)) : answer.alerts;
    loadedAt.value = Date.now();
    loadFailed.value = "";
    stopSyncPolling();
    if (answer.sync_pending && syncPolls < SYNC_POLL_MAX) {
      syncPolls += 1;
      syncTimer = setTimeout(() => load(false, { background: true }), SYNC_POLL_MS);
    }
    setNextUpdate(answer.next_update_in_seconds);
    // The feed refreshes with the board (#180).
    feedVersion.value += 1;
  } catch (err) {
    if (seq !== loadSeq) return;
    stopSyncPolling();
    loadFailed.value = errorMessage(err);
    showError(`Failed to load alert board: ${loadFailed.value}`);
  } finally {
    if (seq === loadSeq) loading.value = false;
  }
}

// The countdown to the next automatic update (#152): the server sends seconds
// left, so a skewed browser clock doesn't matter.  At zero the board reloads
// in the background, which starts the due sync; at most once per gap, so a
// sync that keeps failing isn't a request loop.
const AUTO_UPDATE_MIN_GAP_MS = 30000;
const now = ref(Date.now());
let dueAt: number | null = null;
let lastAutoUpdate = 0;
function setNextUpdate(seconds: number | null) {
  dueAt = typeof seconds === "number" && seconds >= 0 ? Date.now() + seconds * 1000 : null;
}
const nextUpdate = computed(() => {
  if (board.value?.sync_pending) return "Updating…";
  if (dueAt === null || !board.value) return "";
  const left = dueAt - now.value;
  return left > 0 ? `Next update in ${formatCountdown(left / 1000)}` : "Updating…";
});
function tick() {
  now.value = Date.now();
  if (dueAt === null || now.value < dueAt || board.value?.sync_pending) return;
  if (loading.value || syncTimer || now.value - lastAutoUpdate < AUTO_UPDATE_MIN_GAP_MS) return;
  lastAutoUpdate = now.value;
  load(false, { background: true });
}

function resetFilters() {
  if (loading.value) return;
  const reload = criteria.value.nonOperational;
  criteria.value = noBoardCriteria();
  // Non-operational sites came from the server: reload without them.
  if (reload) {
    expanded.value = new Set();
    load();
  }
}
function setNonOperational(on: boolean) {
  if (loading.value) return;
  set("nonOperational", on);
  expanded.value = new Set();
  load();
}

// The clipboard API needs HTTPS (or localhost); plain HTTP falls back to the
// older copy command.  Whether the text was copied.
async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Denied: try the fallback.
    }
  }
  const buffer = document.createElement("textarea");
  buffer.className = "clipboard-buffer";
  buffer.value = text;
  buffer.setAttribute("readonly", "");
  document.body.appendChild(buffer);
  buffer.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  buffer.remove();
  return copied;
}

const COPIED_MS = 2000;
const announce = ref("");
// Copy wall link (#366): the full address, for a screen's browser; what the
// wall can't filter by is said, not dropped silently.
const wallCopyLabel = ref("Copy wall link");
async function copyWallLink() {
  if (!wallLink.value) return;
  const copied = await copyText(new URL(wallLink.value, window.location.origin).href);
  const leftOff = filtersLeftOffTheWall(criteria.value);
  const without = leftOff.length ? ` (without ${leftOff.join(" and ")})` : "";
  wallCopyLabel.value = copied ? `Copied${without}` : "Copy failed";
  announce.value = copied ? `Copied the wall link${without}` : "Could not copy the wall link";
  setTimeout(() => (wallCopyLabel.value = "Copy wall link"), COPIED_MS);
}

// The side panel: one at a time; Escape or Close returns focus to the row.
type Panel =
  | { kind: "case"; site: BoardSite }
  | { kind: "maintenance"; site: BoardSite }
  | { kind: "history"; site: BoardSite; incidents: Incident[] }
  | { kind: "copy"; site: BoardSite; text: string };
const panel = shallowRef<Panel | null>(null);
const panelBox = useTemplateRef<HTMLElement>("panelBox");
const panelTitle = computed(() => {
  if (!panel.value) return "";
  const name = panel.value.site.name || panel.value.site.id;
  return { case: "Add case", maintenance: "Maintenance", history: "History", copy: "Copy site details" }[panel.value.kind] + ` · ${name}`;
});
function openPanel(next: Panel) {
  panel.value = next;
  nextTick(() => {
    const box = panelBox.value;
    if (!box) return;
    const target = next.kind === "copy" ? box.querySelector<HTMLTextAreaElement>("#copy-text") : null;
    if (target) {
      target.focus();
      target.select();
    } else if (next.kind !== "case") box.querySelector<HTMLElement>(".panel-close-btn")?.focus();
  });
}
// Rows are drawn again on every update, so the row's button is found again.
function closePanel() {
  const site = panel.value?.site;
  const kind = panel.value?.kind;
  panel.value = null;
  if (!site) return;
  nextTick(() => {
    const row = document.querySelector(`tr[data-site-id="${CSS.escape(site.id)}"]`);
    (row?.querySelector<HTMLElement>(kind === "case" ? ".case-open-btn" : ".row-menu-btn") ?? row?.querySelector<HTMLElement>(".row-menu-btn"))?.focus();
  });
}
// The open panel's site, as the newest board has it.
watch(sites, (list) => {
  const open = panel.value;
  if (!open) return;
  const fresh = list.find((site) => site.id === open.site.id);
  if (fresh && fresh !== open.site) panel.value = { ...open, site: fresh } as Panel;
});

async function openHistory(site: BoardSite) {
  try {
    const answer = await $fetch<{ instances: Incident[] }>(`/api/alert-history?${new URLSearchParams({ site_id: site.id })}`);
    openPanel({ kind: "history", site, incidents: answer.instances || [] });
  } catch (err) {
    showError(`Failed to load history: ${errorMessage(err)}`);
  }
}

const copiedId = ref("");
async function copySite(site: BoardSite) {
  const text = siteReport(site, { nautobotUrl: config.value?.nautobot_url || "", caseTemplate: caseTemplate.value });
  if (await copyText(text)) {
    announce.value = `Copied ${site.name || site.id} to the clipboard`;
    copiedId.value = site.id;
    setTimeout(() => {
      if (copiedId.value === site.id) copiedId.value = "";
    }, COPIED_MS);
    return;
  }
  // Not allowed: the text, selected, to copy by hand.
  openPanel({ kind: "copy", site, text });
}

// Acknowledge every down device (#288), or remove it.
async function acknowledge(site: BoardSite, remove: boolean) {
  try {
    await $fetch(remove ? "/api/alert-acks/remove" : "/api/alert-acks", { method: "POST", body: ackRequestBody(site.id, []) });
    await load();
  } catch (err) {
    showError(`Could not ${remove ? "remove the acknowledgement" : "acknowledge"}: ${errorMessage(err)}`);
  }
}

async function caseDone() {
  closePanel();
  await load();
}

// The activity feed (#180): the operator's Hide/Show; without one, whether there is room.
const feedCollapsed = ref(feedStartsCollapsed(readStored(FEED_COLLAPSED_KEY), window.innerWidth));
function setFeedCollapsed(collapsed: boolean) {
  feedCollapsed.value = collapsed;
  writeStored(FEED_COLLAPSED_KEY, collapsed ? "1" : "0");
  nextTick(() => document.getElementById(collapsed ? "feed-show" : "feed-toggle")?.focus());
}
const feedTitle = computed(() => (scope ? `Activity · ${scopeText.value}` : "Activity"));

function onKey(event: KeyboardEvent) {
  if (event.key !== "Escape" || !panel.value) return;
  // A menu, list or tip open over the page closes first, on its own.
  if (document.querySelector("[data-dismissable-layer]")) return;
  event.preventDefault();
  closePanel();
}

let timer: ReturnType<typeof setInterval> | undefined;
function onVisible() {
  // Browsers slow timers in background tabs: catch up when it is visible.
  if (!document.hidden) tick();
}
onMounted(() => {
  load();
  timer = setInterval(tick, 1000);
  document.addEventListener("visibilitychange", onVisible);
  document.addEventListener("keydown", onKey);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  stopSyncPolling();
  document.removeEventListener("visibilitychange", onVisible);
  document.removeEventListener("keydown", onKey);
});

const levelTip = computed(() =>
  ["critical", "medium", "low"].map((level) => `${level[0]!.toUpperCase()}${level.slice(1)}: ${levels.value[level] || ""}`).join(" "),
);
// The filter lists' search fields, named for screen readers.
function searchInput(label: string) {
  return { placeholder: "Search…", "aria-label": `Search ${label}` };
}
// "Acme Corp +1".
function shortLabel(values: string[], all: string) {
  if (!values.length) return all;
  return values.length === 1 ? values[0] : `${values[0]} +${values.length - 1}`;
}
</script>

<template>
  <div>
    <AppBar active="alerts" :link-query="linkQuery" />
    <main class="mx-auto max-w-[1600px] space-y-3 px-4 py-5">
      <h1 class="text-2xl font-bold text-highlighted">Alert Board</h1>

      <!-- One line of what is wrong, and the chips that choose the sites (#311). -->
      <section class="summary-bar space-y-2" aria-label="Summary">
        <p class="summary-line flex items-center gap-1.5 text-lg font-semibold" :class="counts.alarms ? 'has-alarms text-error' : 'text-highlighted'">
          <span id="summary-line-text">{{ board ? boardSummaryText(summary) : "Loading…" }}</span>
          <HelpTip :text="`Alert levels: ${levelTip}`" />
        </p>
        <!-- A link for some sites only says so (#364). -->
        <p v-if="scope" id="board-scope" class="board-scope text-sm text-toned">Only <strong id="board-scope-label">{{ scopeText }}</strong> · <a class="text-primary hover:underline" href="/alerts">Show all sites</a></p>
        <div class="severity-chips flex flex-wrap gap-2" role="group" aria-label="Show sites">
          <UButton
            v-for="chip in shownChips(counts, criteria.severity)"
            :key="chip.filter"
            class="severity-chip"
            :data-severity-filter="chip.filter"
            size="sm"
            :color="criteria.severity === chip.filter ? 'primary' : 'neutral'"
            :variant="criteria.severity === chip.filter ? 'soft' : 'outline'"
            :aria-pressed="criteria.severity === chip.filter ? 'true' : 'false'"
            :title="levels[chip.key]"
            @click="setSeverity(chip.filter)"
          ><span>{{ chip.label }} <strong :id="`summary-${chip.key}`">{{ counts[chip.key] ?? 0 }}</strong></span></UButton>
        </div>
      </section>

      <section class="toolbar space-y-2" aria-label="Filters">
        <div class="flex flex-wrap items-center gap-2">
          <UInput
            id="filter-site"
            :model-value="criteria.site"
            class="w-72 max-w-full"
            icon="i-lucide-search"
            placeholder="Filter by site, address, or country"
            aria-label="Site, address, or country"
            @update:model-value="set('site', String($event))"
          />
          <USelectMenu
            v-if="choices.tenant.length"
            :model-value="criteria.tenant"
            :items="tenantItems"
            value-key="value"
            :filter-fields="CHOICE_SEARCH_FIELDS"
            multiple
            placeholder="All tenants"
            aria-label="Tenant"
            :search-input="searchInput('Tenant')"
            class="w-56"
            @update:model-value="set('tenant', $event as string[])"
          >
            <span class="truncate" :class="{ 'text-dimmed': !criteria.tenant.length }" :title="criteria.tenant.join(', ')">
              {{ shortLabel(criteria.tenant, "All tenants") }}
            </span>
          </USelectMenu>
          <UButton
            id="more-filters-toggle"
            color="neutral"
            variant="ghost"
            icon="i-lucide-sliders-horizontal"
            :aria-expanded="moreOpen ? 'true' : 'false'"
            aria-controls="more-filters"
            @click="moreOpen = !moreOpen"
          ><span>More filters<template v-if="moreCount"> ({{ moreCount }})</template></span></UButton>
          <UButton v-if="filtersInUse(criteria)" id="clear-alert-filters" color="neutral" variant="ghost" :disabled="loading" @click="resetFilters">Reset</UButton>
          <!-- The wall for what the board shows (#366). -->
          <UButton
            v-if="wallLink"
            id="copy-wall-link"
            color="neutral"
            variant="ghost"
            icon="i-lucide-monitor"
            title="Copy the link to a wall screen of these sites"
            @click="copyWallLink"
          ><span>{{ wallCopyLabel }}</span></UButton>
        </div>
        <div v-show="moreOpen" id="more-filters" class="more-filters flex flex-wrap items-center gap-2">
          <USelect
            id="sort-by"
            :model-value="criteria.sort"
            :items="SORTS"
            class="w-60"
            aria-label="Sort"
            @update:model-value="set('sort', $event as BoardCriteria['sort'])"
          />
          <USelectMenu
            v-for="filter in ([['status', 'Status', 'All statuses'], ['type', 'Type', 'All location types']] as const)"
            :key="filter[0]"
            :model-value="criteria[filter[0]]"
            :items="choices[filter[0]]"
            multiple
            :placeholder="filter[2]"
            :aria-label="filter[1]"
            :search-input="searchInput(filter[1])"
            class="w-52"
            @update:model-value="set(filter[0], $event as string[])"
          >
            <span class="truncate" :class="{ 'text-dimmed': !criteria[filter[0]].length }">{{ shortLabel(criteria[filter[0]], filter[2]) }}</span>
          </USelectMenu>
          <UCheckbox
            id="toggle-non-operational"
            :model-value="criteria.nonOperational"
            :disabled="loading"
            label="Show non-operational sites"
            @update:model-value="setNonOperational($event === true)"
          />
        </div>
        <div class="toolbar-meta flex flex-wrap items-center gap-3 text-sm text-muted">
          <span id="board-status" :class="{ 'text-warning': board?.stale }">{{ statusText }}</span>
          <span v-if="nextUpdate" id="next-update" class="next-update">{{ nextUpdate }}</span>
          <UButton
            v-if="feedCollapsed"
            id="feed-show"
            size="xs"
            color="neutral"
            variant="outline"
            aria-controls="feed-panel"
            @click="setFeedCollapsed(false)"
          >Show activity</UButton>
          <UButton
            id="refresh-alerts"
            size="xs"
            color="neutral"
            variant="ghost"
            icon="i-lucide-refresh-cw"
            title="Update now"
            aria-label="Update now"
            :disabled="loading"
            @click="load(true)"
          />
        </div>
      </section>

      <div id="board-layout" class="board-layout flex flex-col gap-4 lg:flex-row lg:items-start" :class="{ 'feed-collapsed': feedCollapsed }">
        <section class="table-shell relative min-w-0 flex-1 overflow-x-auto rounded-lg border border-default" aria-label="Alert table">
          <table class="board-table w-full text-left text-sm">
            <thead class="bg-elevated/50 text-xs text-muted uppercase">
              <tr>
                <th class="px-3 py-2">Location</th>
                <th class="px-3 py-2">Alert</th>
                <th class="px-3 py-2">Since</th>
                <th class="col-cases px-3 py-2">Cases</th>
                <th class="col-action px-3 py-2"><span class="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody id="alerts-table-body" class="divide-y divide-default">
              <tr v-if="!board">
                <td colspan="5" class="empty-state px-3 py-6 text-center text-muted">
                  {{ loadFailed ? `Could not load alerts: ${loadFailed}` : "Loading alerts…" }}
                </td>
              </tr>
              <tr v-else-if="empty">
                <td
                  colspan="5"
                  class="empty-state px-3 py-6 text-center"
                  :class="{ 'scope-no-match text-warning': empty.kind === 'no-match', 'all-clear text-success': empty.kind === 'all-clear', 'text-muted': empty.kind === 'plain' }"
                >{{ empty.text }}</td>
              </tr>
              <BoardRow
                v-for="site in rows"
                v-else
                :key="site.id"
                :item="site"
                :expanded="expanded.has(site.id)"
                :case-template="caseTemplate"
                :copied="copiedId === site.id"
                :now="loadedAt"
                @toggle="toggleSite(site.id)"
                @case="openPanel({ kind: 'case', site })"
                @ack="acknowledge(site, $event)"
                @copy="copySite(site)"
                @maintenance="openPanel({ kind: 'maintenance', site })"
                @history="openHistory(site)"
              />
              <tr v-if="shown.length > rows.length">
                <td colspan="5" class="px-3 py-3 text-center">
                  <UButton id="board-more" color="neutral" variant="outline" size="sm" @click="rowLimit += ROWS_STEP">
                    Show {{ Math.min(ROWS_STEP, shown.length - rows.length) }} more of {{ (shown.length - rows.length).toLocaleString("en") }}
                  </UButton>
                </td>
              </tr>
            </tbody>
          </table>
        </section>
        <BoardFeed
          v-show="!feedCollapsed"
          class="shrink-0 rounded-lg border border-default lg:w-80"
          :title="feedTitle"
          :version="feedVersion"
          :open="!feedCollapsed"
          :scope-query="scopeQuery"
          @hide="setFeedCollapsed(true)"
        />
      </div>
    </main>

    <!-- The side panel (#179): not modal, one at a time. -->
    <section
      v-if="panel"
      id="side-panel"
      ref="panelBox"
      class="history-panel fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-default bg-default shadow-xl"
      :class="`${panel.kind}-panel`"
      role="dialog"
      aria-modal="false"
      aria-labelledby="panel-title"
      tabindex="-1"
    >
      <div class="flex items-center justify-between gap-2 border-b border-default px-4 py-3">
        <h2 id="panel-title" class="truncate font-semibold text-highlighted">{{ panelTitle }}</h2>
        <UButton class="panel-close-btn" color="neutral" variant="ghost" @click="closePanel">Close</UButton>
      </div>
      <div class="min-h-0 flex-1 overflow-y-auto p-4">
        <BoardCase
          v-if="panel.kind === 'case'"
          :key="panel.site.id"
          :item="panel.site"
          :case-template="caseTemplate"
          @done="caseDone"
          @error="showError"
        />
        <BoardMaintenance
          v-else-if="panel.kind === 'maintenance'"
          :key="panel.site.id"
          :item="panel.site"
          :non-operational="criteria.nonOperational"
          @changed="load()"
          @error="showError"
        />
        <BoardHistory v-else-if="panel.kind === 'history'" :site-id="panel.site.id" :incidents="panel.incidents" :case-template="caseTemplate" />
        <template v-else>
          <!-- Copy by hand when the browser won't (#227). -->
          <p id="copy-hint" class="copy-hint mb-2 text-sm text-muted">The browser did not allow copying. The text is selected: press Ctrl+C (⌘C on a Mac).</p>
          <textarea id="copy-text" class="copy-text w-full rounded-md border border-default bg-default p-2 font-mono text-xs" readonly rows="14" aria-label="Site details" :value="panel.text" />
        </template>
      </div>
    </section>
    <div id="copy-status" class="sr-only" role="status" aria-live="polite">{{ announce }}</div>
  </div>
</template>
