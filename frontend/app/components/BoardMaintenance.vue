<!-- Put a site or some of its devices in maintenance (#283; the Maintenance
     page's form for one site, #343): its current and planned windows, with
     End now / Cancel, and a form for a new one. -->
<script setup lang="ts">
import type { BoardSite } from "~/utils/board";
import { errorMessage, formatFeedTime } from "~/utils/format";
import { windowRequest, type MaintenanceDevice, type MaintenanceWindow } from "~/utils/maintenance";
import { defaultPlan, type WhenValues } from "~/utils/when";

const props = defineProps<{ item: BoardSite; nonOperational: boolean }>();
const emit = defineEmits<{ changed: []; error: [text: string] }>();

const windows = shallowRef<MaintenanceWindow[] | null>(null);
const devices = shallowRef<MaintenanceDevice[]>([]);
const loadError = ref("");
const scope = ref<"site" | "devices">("site");
const deviceIds = ref<string[]>([]);
const reason = ref("");
const when = ref<WhenValues>({ when: "now", duration: 120, ...defaultPlan(new Date()) });
const formError = ref("");
const saving = ref(false);
const ending = ref<number | null>(null);

const deviceNames = computed(() => Object.fromEntries(devices.value.map((device) => [device.id, device.name])));

// A slower answer for a site opened before must not fill this one's panel.
let seq = 0;
async function load() {
  const mine = ++seq;
  const params = new URLSearchParams({ site_id: props.item.id });
  const deviceParams = new URLSearchParams(params);
  if (props.nonOperational) deviceParams.set("include_non_operational", "1");
  try {
    const [list, found] = await Promise.all([
      $fetch<{ windows: MaintenanceWindow[] }>(`/api/maintenance?${params}`),
      $fetch<{ devices: MaintenanceDevice[] }>(`/api/maintenance/devices?${deviceParams}`),
    ]);
    if (mine !== seq) return;
    windows.value = list.windows || [];
    devices.value = found.devices || [];
    loadError.value = "";
  } catch (err) {
    if (mine === seq) loadError.value = `Could not load maintenance: ${errorMessage(err)}`;
  }
}
watch(() => props.item.id, load, { immediate: true });

function windowWhen(window: MaintenanceWindow): string {
  return window.state === "upcoming"
    ? `${formatFeedTime(window.starts_at)} – ${formatFeedTime(window.ends_at)}`
    : `until ${formatFeedTime(window.ends_at)}`;
}

async function save() {
  const body = windowRequest({
    ...when.value,
    siteIds: [props.item.id],
    scope: scope.value,
    deviceIds: deviceIds.value,
    reason: reason.value,
  });
  if (body.error) {
    formError.value = body.error;
    return;
  }
  formError.value = "";
  saving.value = true;
  try {
    await $fetch("/api/maintenance", { method: "POST", body });
    reason.value = "";
    deviceIds.value = [];
    emit("changed");
    await load();
  } catch (err) {
    formError.value = `Could not save: ${errorMessage(err)}`;
  } finally {
    saving.value = false;
  }
}

async function end(window: MaintenanceWindow) {
  ending.value = window.id;
  try {
    await $fetch(`/api/maintenance/${encodeURIComponent(window.id)}/end`, { method: "POST" });
    emit("changed");
    await load();
  } catch (err) {
    emit("error", `Could not end maintenance: ${errorMessage(err)}`);
  } finally {
    ending.value = null;
  }
}
</script>

<template>
  <div class="space-y-4">
    <p v-if="loadError" class="maint-error text-sm text-error">{{ loadError }}</p>
    <p v-else-if="!windows" class="maint-empty text-sm text-muted">Loading…</p>
    <template v-else>
      <ul v-if="windows.length" class="maint-windows space-y-2">
        <li v-for="window in windows" :key="window.id" class="maint-window flex items-start gap-2 text-sm">
          <span class="alert-badge alert-maintenance shrink-0">{{ window.state === "upcoming" ? "PLANNED" : "ACTIVE" }}</span>
          <span class="maint-window-text min-w-0 flex-1">
            <strong class="text-highlighted">{{ window.device_id ? deviceNames[window.device_id] || window.device_name || window.device_id : "Whole site" }}</strong>
            · {{ windowWhen(window) }}<br>
            {{ window.reason }}<template v-if="window.created_by"> · by {{ window.created_by }}</template>
          </span>
          <UButton
            class="maint-end-btn shrink-0"
            size="xs"
            color="neutral"
            variant="outline"
            :loading="ending === window.id"
            @click="end(window)"
          >{{ window.state === "upcoming" ? "Cancel" : "End now" }}</UButton>
        </li>
      </ul>
      <p v-else class="maint-empty text-sm text-muted">No maintenance now or planned.</p>

      <form class="maint-form space-y-4 border-t border-default pt-4" @submit.prevent="save">
        <fieldset class="space-y-1 text-sm">
          <legend class="mb-1 font-semibold text-highlighted">What</legend>
          <label class="maint-choice flex items-center gap-2">
            <input v-model="scope" type="radio" name="maint-scope" value="site" class="accent-(--ui-primary)"> Whole site
          </label>
          <label class="maint-choice flex items-center gap-2">
            <input v-model="scope" type="radio" name="maint-scope" value="devices" class="accent-(--ui-primary)"> Some devices
          </label>
          <div v-if="scope === 'devices'" class="maint-devices max-h-64 space-y-1 overflow-y-auto rounded-md border border-default p-2">
            <p v-if="!devices.length" class="text-muted">No monitored devices.</p>
            <label v-for="device in devices" v-else :key="device.id" class="flex flex-wrap items-center gap-2">
              <input v-model="deviceIds" type="checkbox" class="maint-device accent-(--ui-primary)" :value="device.id">
              <span>{{ device.name || device.id }}</span>
              <span class="text-xs text-muted">{{ [device.location_path, device.role, device.status].filter(Boolean).join(" · ") }}</span>
            </label>
          </div>
        </fieldset>
        <WhenField v-model="when" />
        <div class="space-y-1">
          <label class="block text-sm font-medium text-highlighted" for="maint-reason">Reason</label>
          <UInput id="maint-reason" v-model="reason" class="maint-reason w-full" :maxlength="200" autocomplete="off" placeholder="e.g. Core switch upgrade" />
        </div>
        <p v-if="formError" class="maint-error text-sm text-error" role="alert">{{ formError }}</p>
        <UButton class="maint-save-btn" type="submit" :loading="saving">
          {{ when.when === "planned" ? "Plan maintenance" : "Start maintenance" }}
        </UButton>
      </form>
    </template>
  </div>
</template>
