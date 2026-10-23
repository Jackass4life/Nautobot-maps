<!-- The sites that need action (#334, #392): "28 in alarm", the count per
     level, then the sites worst first and newest down, the first eight and
     "Show all 28".  A row opens the site; "Alarms only" hides the rest of the
     map. -->
<script setup lang="ts">
import { ALARM_LIST_SHOWN, LEVEL_NAMES, alarmMeta, levelBreakdown, type AlarmSite } from "~/utils/map";

const props = defineProps<{ sites: AlarmSite[]; loaded: boolean; title: string; alarmsOnly: boolean }>();
const emit = defineEmits<{ open: [id: string, element: HTMLElement]; "update:alarmsOnly": [on: boolean] }>();

const expanded = ref(false);
const shown = computed(() => (expanded.value ? props.sites : props.sites.slice(0, ALARM_LIST_SHOWN)));
const breakdown = computed(() => levelBreakdown(props.sites));
// The times against now, taken again with each refresh of the list.
const now = computed(() => {
  void props.sites;
  return new Date();
});

function label(entry: AlarmSite) {
  const meta = alarmMeta(entry, now.value);
  return [`${LEVEL_NAMES[entry.level]}: ${entry.loc.name || entry.loc.id}`, meta].filter(Boolean).join(", ");
}
</script>

<template>
  <section id="alarm-section" aria-labelledby="alarm-title" class="min-h-0">
    <div class="alarm-header flex items-center justify-between gap-2">
      <h2 id="alarm-title" class="text-base font-semibold text-highlighted">
        <span id="alarm-count" class="alarm-count">{{ loaded ? title : "Alarms" }}</span>
      </h2>
      <UButton
        id="alarms-only"
        class="quick-filter-btn"
        :class="{ active: alarmsOnly }"
        size="xs"
        :color="alarmsOnly ? 'primary' : 'neutral'"
        :variant="alarmsOnly ? 'soft' : 'outline'"
        :aria-pressed="alarmsOnly ? 'true' : 'false'"
        title="Show only the sites in alarm on the map"
        @click="emit('update:alarmsOnly', !alarmsOnly)"
      >Alarms only</UButton>
    </div>
    <p id="alarm-breakdown" class="alarm-breakdown text-xs text-muted">{{ breakdown }}</p>
    <ol id="alarm-list" class="alarm-list mt-2 space-y-0.5">
      <li v-if="!loaded" class="alarm-empty text-sm text-muted">Alert levels not loaded yet.</li>
      <li v-for="entry in shown" :key="entry.loc.id">
        <button
          class="alarm-item grid w-full grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2 rounded-md px-2 py-1.5 text-left hover:bg-elevated"
          type="button"
          :data-location-id="entry.loc.id"
          :aria-label="label(entry)"
          @click="emit('open', entry.loc.id, $event.currentTarget as HTMLElement)"
        >
          <span class="legend-dot" :class="`dot-${entry.level}`" aria-hidden="true" />
          <span class="alarm-name truncate text-sm font-medium text-highlighted">{{ entry.loc.name || entry.loc.id }}</span>
          <span v-if="alarmMeta(entry, now)" class="alarm-meta col-start-2 text-xs text-muted">{{ alarmMeta(entry, now) }}</span>
        </button>
      </li>
    </ol>
    <UButton
      v-if="sites.length > ALARM_LIST_SHOWN"
      id="alarm-more"
      class="alarm-more mt-1"
      size="xs"
      color="neutral"
      variant="link"
      aria-controls="alarm-list"
      :aria-expanded="expanded ? 'true' : 'false'"
      @click="expanded = !expanded"
    >{{ expanded ? "Show fewer" : `Show all ${sites.length.toLocaleString("en")}` }}</UButton>
  </section>
</template>
