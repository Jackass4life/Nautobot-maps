<!-- What changed (#180; static/js/alerts/feed.js): devices down and up, and
     sites' severity changes, newest first; for a link's sites only (#369).
     Loaded with the board (*version*), not while it is hidden. -->
<script setup lang="ts">
import { feedEntry, type Feed } from "~/utils/board";
import { errorMessage } from "~/utils/format";

const props = defineProps<{ title: string; version: number; open: boolean; scopeQuery: string }>();
const emit = defineEmits<{ hide: [] }>();

const FEED_LIMIT = 100;
const KINDS = [
  { kind: "down", label: "Down" },
  { kind: "up", label: "Up" },
  { kind: "severity", label: "Severity" },
] as const;
const kinds = ref<string[]>(["down", "up", "severity"]);
const feed = shallowRef<Feed | null>(null);
const failed = ref("");

let seq = 0;
async function load() {
  // Hidden: nothing to show; it loads again when shown.
  if (!props.open || !kinds.value.length) return;
  const mine = ++seq;
  const params = new URLSearchParams(props.scopeQuery);
  params.set("limit", String(FEED_LIMIT));
  params.set("kinds", kinds.value.join(","));
  try {
    const answer = await $fetch<Feed>(`/api/alert-feed?${params}`);
    if (mine !== seq) return;
    feed.value = answer;
    failed.value = "";
  } catch (err) {
    // The board refreshes often: no toast for every failed feed load.
    if (mine === seq) failed.value = errorMessage(err);
  }
}
function toggle(kind: string) {
  kinds.value = kinds.value.includes(kind) ? kinds.value.filter((k) => k !== kind) : [...kinds.value, kind];
  load();
}
watch(() => [props.version, props.open], load);
onMounted(load);

const entries = computed(() => {
  const now = new Date();
  return (feed.value?.events || []).map((event) => ({ event, ...feedEntry(event, now) }));
});
</script>

<template>
  <aside id="feed-panel" class="feed-panel" aria-labelledby="feed-title">
    <div class="flex items-center justify-between gap-2 border-b border-default px-3 py-2">
      <h2 id="feed-title" class="truncate text-sm font-semibold text-highlighted">{{ title }}</h2>
      <UButton id="feed-toggle" class="feed-toggle" size="xs" color="neutral" variant="ghost" aria-controls="feed-panel" @click="emit('hide')">Hide</UButton>
    </div>
    <div id="feed-body" class="feed-body space-y-2 p-3">
      <div class="feed-filters flex gap-1" role="group" aria-label="Show in activity">
        <UButton
          v-for="{ kind, label } in KINDS"
          :key="kind"
          class="feed-filter-btn"
          :data-feed-kind="kind"
          size="xs"
          :color="kinds.includes(kind) ? 'primary' : 'neutral'"
          :variant="kinds.includes(kind) ? 'soft' : 'outline'"
          :aria-pressed="kinds.includes(kind) ? 'true' : 'false'"
          @click="toggle(kind)"
        >{{ label }}</UButton>
      </div>
      <ol id="feed-list" class="feed-list space-y-2 text-sm">
        <li v-if="!kinds.length" class="feed-empty text-muted">Choose what to show.</li>
        <li v-else-if="failed" class="feed-empty text-muted">Could not load activity: {{ failed }}</li>
        <li v-else-if="!feed" class="feed-empty text-muted">Loading activity…</li>
        <li v-else-if="feed.persistence_configured === false" class="feed-empty text-muted">The activity log needs the PostgreSQL database.</li>
        <li v-else-if="!entries.length" class="feed-empty text-muted">No changes yet.</li>
        <template v-else>
          <li v-for="({ event, title: what, text, meta }, index) in entries" :key="index" class="feed-entry" :class="`feed-${event.kind}`">
            <div class="flex items-baseline gap-1.5">
              <span
                class="feed-icon text-xs"
                :class="event.kind === 'down' ? 'text-error' : event.kind === 'up' ? 'text-success' : 'text-muted'"
                aria-hidden="true"
              >{{ event.kind === "down" ? "▼" : event.kind === "up" ? "▲" : "●" }}</span>
              <span class="min-w-0">
                <strong class="font-medium text-highlighted">{{ what }}</strong>
                <template v-if="text"> {{ text }}</template>
                <span v-else class="sr-only"> severity changed</span>
              </span>
            </div>
            <div class="feed-meta pl-4 text-xs text-muted">
              <template v-if="event.kind === 'severity'">
                <LevelBadge :level="event.from_level" /> → <LevelBadge :level="event.to_level" /> ·
              </template>
              <time :datetime="event.at">{{ meta }}</time>
            </div>
          </li>
        </template>
      </ol>
    </div>
  </aside>
</template>
