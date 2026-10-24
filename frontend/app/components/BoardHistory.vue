<!-- A site's incidents (#179), newest first, and their CSV export (#277). -->
<script setup lang="ts">
import { HISTORY_PERIODS, formatDuration, historyExportUrl, type Incident } from "~/utils/board";

const props = defineProps<{ siteId: string; incidents: Incident[]; caseTemplate: string }>();
const days = ref("30");
const exports = computed(() => [
  { view: "incidents" as const, label: "Incidents CSV", href: historyExportUrl(props.siteId, "incidents", days.value) },
  { view: "devices" as const, label: "Per-device CSV", href: historyExportUrl(props.siteId, "devices", days.value) },
]);
function at(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}
</script>

<template>
  <div class="space-y-3">
    <div class="history-export flex flex-wrap items-center gap-2 text-sm">
      <USelect v-model="days" class="history-export-days w-36" size="sm" :items="HISTORY_PERIODS" aria-label="Export period" />
      <UButton
        v-for="link in exports"
        :key="link.view"
        class="history-export-link"
        :data-view="link.view"
        size="sm"
        color="neutral"
        variant="outline"
        icon="i-lucide-download"
        :href="link.href"
        download
      >{{ link.label }}</UButton>
    </div>
    <p v-if="!incidents.length" class="text-sm text-muted">No incidents found.</p>
    <article v-for="incident in incidents" :key="incident.id" class="history-instance space-y-0.5 border-t border-default pt-2 text-sm">
      <div>
        <strong class="text-highlighted">{{ incident.device_name || incident.device_id || "Unknown device" }}</strong>
        · {{ incident.status || "unknown" }} · {{ incident.alert_level || "unknown" }}
      </div>
      <div class="history-line text-muted">Downtime: {{ formatDuration(incident.total_downtime_seconds) }}</div>
      <div class="history-line text-muted">Opened: {{ at(incident.down_started_at) }} · Resolved: {{ at(incident.resolved_at) }}</div>
      <div class="history-line text-muted">
        Cases: <CasePills :cases="incident.cases.map((entry) => entry.case_number).filter(Boolean)" :template="caseTemplate" empty="—" />
      </div>
      <div v-if="incident.probable_cause" class="history-line text-muted">Likely caused by: {{ incident.probable_cause }}</div>
      <div class="history-line text-muted">
        Events: {{ incident.events.map((event) => `${event.event_type} @ ${at(event.event_at)}`).join(", ") || "—" }}
      </div>
    </article>
  </div>
</template>
