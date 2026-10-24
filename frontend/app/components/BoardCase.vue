<!-- Add a case to down devices (#179, #273), or acknowledge them without one
     (#288).  One case often covers several devices, so every down device has
     a tick; those that already have a case start unticked and show it. -->
<script setup lang="ts">
import { ackRequestBody, caseButtonLabel, caseDevices, caseTargets, type BoardSite } from "~/utils/board";
import { apiError, errorMessage } from "~/utils/format";

const props = defineProps<{ item: BoardSite; caseTemplate: string }>();
const emit = defineEmits<{ done: []; error: [text: string] }>();

const devices = computed(() => caseDevices(props.item));
const ticked = ref(new Set(devices.value.filter((device) => !device.case_numbers?.length).map((device) => device.device_id)));
const caseNumber = ref("");
const note = ref("");
const busy = ref(false);
const input = useTemplateRef<{ inputRef: HTMLInputElement | null }>("caseInput");
onMounted(() => input.value?.inputRef?.focus());

const siteCases = computed(() => [...new Set(devices.value.flatMap((device) => device.case_numbers || []))]);
const allHaveOne = computed(() => devices.value.length > 0 && devices.value.every((device) => device.case_numbers?.length));
const targets = computed(() => caseTargets(devices.value, ticked.value, caseNumber.value));
const already = computed(() => ticked.value.size - targets.value.length);
const allTicked = computed(() => ticked.value.size === devices.value.length);
const someTicked = computed(() => ticked.value.size > 0 && !allTicked.value);

function tick(id: string, on: boolean) {
  const next = new Set(ticked.value);
  if (on) next.add(id);
  else next.delete(id);
  ticked.value = next;
}
function tickAll(on: boolean) {
  ticked.value = new Set(on ? devices.value.map((device) => device.device_id) : []);
}

async function saveCase() {
  const number = caseNumber.value.trim();
  if (!targets.value.length || !number) {
    emit("error", "Select at least one device and enter a case number first.");
    return;
  }
  busy.value = true;
  try {
    await $fetch("/api/alert-cases", {
      method: "POST",
      body: { site_id: props.item.id, device_ids: targets.value, case_number: number },
    });
    emit("done");
  } catch (err) {
    // Devices that came back up meanwhile: the API names them.
    const missingIds = (err as { data?: { missing_device_ids?: string[] } }).data?.missing_device_ids;
    const names = Object.fromEntries(devices.value.map((device) => [device.device_id, device.device_name || device.device_id]));
    const missing = missingIds?.length
      ? ` (no longer down: ${missingIds.map((id) => names[id] || id).join(", ")} – untick and try again)`
      : "";
    emit("error", `Failed to add case: ${apiError(err) || errorMessage(err)}${missing}`);
  } finally {
    busy.value = false;
  }
}

async function acknowledge() {
  if (!ticked.value.size) {
    emit("error", "Tick at least one device to acknowledge.");
    return;
  }
  busy.value = true;
  try {
    await $fetch("/api/alert-acks", { method: "POST", body: ackRequestBody(props.item.id, [...ticked.value], note.value) });
    emit("done");
  } catch (err) {
    emit("error", `Could not acknowledge: ${errorMessage(err)}`);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <form class="case-form space-y-4" @submit.prevent="saveCase">
    <p v-if="siteCases.length" class="case-existing text-sm">
      Open cases on this site: <CasePills :cases="siteCases" :template="caseTemplate" />
    </p>
    <p v-if="allHaveOne" class="case-note text-sm text-muted">
      Every down device already has a case. Ticking a device adds a second case next to it; it doesn't replace it.
    </p>
    <fieldset class="case-devices space-y-1">
      <legend class="mb-1 text-sm font-semibold text-highlighted">Devices for this case</legend>
      <UCheckbox
        v-if="devices.length > 1"
        class="case-select-all"
        :model-value="someTicked ? 'indeterminate' : allTicked"
        :label="`All down devices (${devices.length})`"
        @update:model-value="tickAll($event === true)"
      />
      <div class="case-device-list max-h-72 space-y-1 overflow-y-auto">
        <label v-for="device in devices" :key="device.device_id" class="case-device-option flex flex-wrap items-center gap-2 text-sm">
          <input
            type="checkbox"
            class="case-device-checkbox accent-(--ui-primary)"
            :value="device.device_id"
            :checked="ticked.has(device.device_id)"
            @change="tick(device.device_id, ($event.target as HTMLInputElement).checked)"
          >
          <span>{{ device.device_name || device.device_id }}</span>
          <CasePills :cases="device.case_numbers" :template="caseTemplate" />
        </label>
      </div>
    </fieldset>
    <div class="space-y-1">
      <label class="block text-sm font-medium text-highlighted" for="case-input">Case number</label>
      <UInput id="case-input" ref="caseInput" v-model="caseNumber" class="case-input w-full" autocomplete="off" placeholder="Case #" />
    </div>
    <UButton
      class="case-save-btn"
      type="submit"
      :disabled="busy || ticked.size === 0 || already === ticked.size"
    >{{ caseButtonLabel(ticked.size, already) }}</UButton>
    <div class="ack-form space-y-2 border-t border-default pt-4">
      <label class="block text-sm font-medium text-highlighted" for="ack-note">Or acknowledge without a case (note, optional)</label>
      <UInput
          id="ack-note"
          v-model="note"
          class="ack-note-input w-full"
          autocomplete="off"
          :maxlength="200"
          placeholder="e.g. On my way"
          @keydown.enter.prevent="acknowledge"
        />
      <UButton class="ack-form-btn" color="neutral" variant="outline" :disabled="busy" @click="acknowledge">Acknowledge ticked</UButton>
    </div>
  </form>
</template>
