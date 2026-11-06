<!-- One batch of maintenance windows (#343): planned together, one line;
     "Show N" lists its sites or devices, each with its own End.  Ending a
     batch of more than one asks for a second click (no dialogs). -->
<script setup lang="ts">
import { BADGES, batchTitle, CONFIRM_MS, endLabel, siteLabel, spanText, whenText, type Batch } from "~/utils/maintenance";

const props = defineProps<{
  batch: Batch;
  descriptions: Record<string, string>;
  now: Date;
  // The history: read only, and when it ran.
  history?: boolean;
}>();
const emit = defineEmits<{ end: [ids: number[]] }>();

const expanded = ref(false);
const confirming = ref(false);
const busy = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;

const actions = computed(() => !props.history && props.batch.group !== "past");
const ids = computed(() => props.batch.windows.map((window) => window.id));
const many = computed(() => props.batch.windows.length > 1);
const when = computed(() => (props.history ? spanText(props.batch, props.now) : whenText(props.batch, props.now)));
const preview = computed(() => {
  const names = props.batch.sites;
  if (!many.value || names.length < 2) return "";
  return `${names.slice(0, 3).join(", ")}${names.length > 3 ? ` +${names.length - 3}` : ""}`;
});

function end(list: number[], confirm: boolean) {
  if (confirm && !confirming.value) {
    confirming.value = true;
    clearTimeout(timer);
    timer = setTimeout(() => (confirming.value = false), CONFIRM_MS);
    return;
  }
  clearTimeout(timer);
  confirming.value = false;
  busy.value = true;
  emit("end", list);
}
onBeforeUnmount(() => clearTimeout(timer));
</script>

<template>
  <li class="maint-batch px-4 py-3" :data-key="batch.key">
    <div class="flex flex-wrap items-start gap-3">
      <UBadge color="info" variant="subtle" size="sm" class="mt-0.5 shrink-0 font-semibold">{{ BADGES[batch.state] || "PLANNED" }}</UBadge>
      <div class="min-w-0 flex-1 basis-72 text-sm">
        <div><strong class="text-highlighted">{{ batchTitle(batch) }}</strong> · {{ when }}</div>
        <div class="text-muted">{{ batch.reason }}<template v-if="batch.created_by"> · by {{ batch.created_by }}</template></div>
        <div v-if="preview" class="text-muted">{{ preview }}</div>
        <TenantList v-if="batch.tenants.length" class="text-muted" :tenants="batch.tenants" :descriptions="descriptions" />
      </div>
      <div class="flex shrink-0 gap-2">
        <UButton
          v-if="many"
          class="maint-expand"
          size="xs"
          color="neutral"
          variant="outline"
          :aria-expanded="expanded"
          @click="expanded = !expanded"
        >{{ expanded ? "Hide" : "Show" }} {{ batch.windows.length }}</UButton>
        <UButton
          v-if="actions"
          class="maint-end"
          :class="{ 'is-confirm': confirming }"
          size="xs"
          :color="confirming ? 'error' : 'neutral'"
          :variant="confirming ? 'solid' : 'outline'"
          :loading="busy"
          :data-ids="ids.join(',')"
          @click="end(ids, many)"
        >{{ confirming ? "Sure? " : "" }}{{ endLabel(batch, batch.windows.length) }}</UButton>
      </div>
    </div>
    <ul v-if="expanded" class="maint-batch-windows mt-2 ml-2 divide-y divide-default border-l-2 border-default pl-3 text-sm">
      <li v-for="window in batch.windows" :key="window.id" class="flex flex-wrap items-center gap-3 py-1.5">
        <span class="min-w-0 flex-1">
          {{ siteLabel(window) }}<template v-if="window.device_id"> · {{ window.device_name || window.device_id }}</template>
        </span>
        <TenantList class="text-muted" :tenants="window.tenants || []" :descriptions="descriptions" />
        <UButton
          v-if="actions"
          class="maint-end"
          size="xs"
          color="neutral"
          variant="outline"
          :data-ids="String(window.id)"
          @click="end([window.id], false)"
        >{{ endLabel(batch, 1) }}</UButton>
      </li>
    </ul>
  </li>
</template>
