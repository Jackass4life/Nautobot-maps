<!-- A site's row on the board (#311; static/js/alerts/rows.js), and its down
     devices under it when opened.  One visible action, "+ Case" when
     something is down; the rest (acknowledge, copy, maintenance, history)
     behind "⋯". -->
<script setup lang="ts">
import type { DropdownMenuItem } from "@nuxt/ui";
import {
  ackText,
  caseDevices,
  deviceDowntimeSeconds,
  deviceMeta,
  flapTitle,
  maintenanceNote,
  onTheMap,
  setLevel,
  siteAlarmSeconds,
  sinceLabel,
  type BoardSite,
} from "~/utils/board";
import { siteTenants } from "~/utils/scope";
import { siteAddress } from "~/utils/sitesearch";
import { maintenanceHistoryHref } from "~/utils/sitepanel";

const props = defineProps<{ item: BoardSite; expanded: boolean; caseTemplate: string; copied: boolean; now: number }>();
const emit = defineEmits<{
  toggle: [];
  case: [];
  ack: [remove: boolean];
  copy: [];
  maintenance: [];
  history: [];
}>();

const name = computed(() => props.item.name || props.item.id || "Unknown site");
const devices = computed(() => props.item.down_devices || []);
const withCase = computed(() => caseDevices(props.item));
const tenants = computed(() => siteTenants(props.item));
const restShown = ref(false);
const descriptionOf = (tenant: string) =>
  Object.hasOwn(props.item.tenant_descriptions || {}, tenant) ? props.item.tenant_descriptions[tenant] || "" : "";
const address = computed(() => siteAddress(props.item));
const meta = computed(() => props.item.ancestor_path || props.item.parent || "");

// Nothing to put in maintenance without monitored devices (still there while a window is on).
const canMaintain = computed(
  () => Boolean(props.item.device_count || props.item.maintenance_device_count || props.item.maintenance),
);

const menu = computed<DropdownMenuItem[]>(() => {
  const items: DropdownMenuItem[] = [];
  if (devices.value.length) {
    if (withCase.value.length) {
      const all = withCase.value.every((device) => device.acknowledged);
      items.push(
        all
          ? { label: "Remove ack", class: "unack-btn", onSelect: () => emit("ack", true) }
          : { label: "Acknowledge", class: "ack-btn", title: "Someone is on it, without a case number", onSelect: () => emit("ack", false) },
      );
    }
    // The site and its down devices as text for an ITSM ticket (#227).
    items.push({ label: "Copy", class: "copy-site-btn", title: "Copy the site and its down devices as text", onSelect: () => emit("copy") });
  }
  if (canMaintain.value) items.push({ label: "Maint.", class: "maint-open-btn", title: "Maintenance window", onSelect: () => emit("maintenance") });
  items.push({ label: "History", class: "history-btn", onSelect: () => emit("history") });
  // The site's maintenance history (#353).
  if (props.item.id) items.push({ label: "Maint. history", class: "maint-history-link", to: maintenanceHistoryHref(props.item.id), external: true });
  return items;
});
</script>

<template>
  <tr class="site-row" :data-site-id="item.id" :class="{ 'in-maintenance': item.alert_level === 'maintenance' }">
    <td>
      <div class="flex items-start gap-1.5">
        <UButton
          v-if="devices.length"
          class="site-toggle-btn -ml-1 shrink-0"
          size="xs"
          color="neutral"
          variant="ghost"
          :icon="expanded ? 'i-lucide-chevron-down' : 'i-lucide-chevron-right'"
          :aria-expanded="expanded ? 'true' : 'false'"
          :aria-label="`${expanded ? 'Collapse' : 'Expand'} ${name}`"
          @click="emit('toggle')"
        />
        <span v-else class="w-6 shrink-0" aria-hidden="true" />
        <div class="min-w-0">
          <div class="site-title font-medium text-highlighted">
            <a v-if="onTheMap(item)" class="site-name hover:underline" :href="`/?location_id=${encodeURIComponent(item.id)}`" title="Show on the map">{{ name }}</a>
            <span v-else class="site-name">{{ name }}</span>
            <!-- The site's importance (#293); the most important tier is accented. -->
            <span v-if="item.tier" class="tier-pill" :class="{ 'tier-top': item.tier_rank === 0 }" title="Importance tier">{{ item.tier.toUpperCase() }}</span>
          </div>
          <div class="site-summary text-xs text-muted">{{ item.down_device_count || 0 }} down · {{ item.device_count || 0 }} monitored</div>
          <!-- The first tenant, and "+N" that shows the rest in place (#279). -->
          <div v-if="tenants.length" class="site-tenants text-xs text-toned">
            <template v-for="(tenant, index) in restShown ? tenants : tenants.slice(0, 1)" :key="tenant">
              <span v-if="index">, </span>{{ tenant }}<HelpTip v-if="descriptionOf(tenant)" class="ml-0.5" :text="descriptionOf(tenant)" />
            </template>
            <button
              v-if="!restShown && tenants.length > 1"
              type="button"
              class="tenant-more-btn ml-1 rounded px-1 text-primary hover:bg-elevated"
              :title="tenants.slice(1).join(', ')"
              @click="restShown = true"
            >+{{ tenants.length - 1 }}</button>
          </div>
          <div v-if="address" class="site-address text-xs text-muted">{{ address }}</div>
          <div v-if="meta" class="site-meta text-xs text-dimmed">{{ meta }}</div>
        </div>
      </div>
    </td>
    <td>
      <LevelBadge :level="item.alert_level" :reason="item.alert_reason" />
      <div v-if="item.maintenance" class="maintenance-note mt-1 text-xs text-muted">
        {{ maintenanceNote(item.maintenance.until, item.maintenance.reason) }}
      </div>
    </td>
    <td class="since-cell whitespace-nowrap text-sm text-toned">{{ sinceLabel("in alarm", siteAlarmSeconds(item)) }}</td>
    <td class="cases-cell col-cases"><CasePills :cases="item.active_cases" :template="caseTemplate" empty="—" /></td>
    <td class="col-action">
      <div class="action-row flex items-center justify-end gap-1">
        <UButton
          v-if="withCase.length"
          class="case-open-btn"
          size="xs"
          color="primary"
          variant="soft"
          aria-haspopup="dialog"
          :aria-label="`+ Case for ${name}`"
          @click="emit('case')"
        >+ Case</UButton>
        <span v-if="copied" class="text-xs text-success" role="status">Copied</span>
        <UDropdownMenu :items="menu" :modal="false" :content="{ align: 'end' }">
          <UButton
            class="row-menu-btn"
            size="xs"
            color="neutral"
            variant="ghost"
            icon="i-lucide-ellipsis"
            :aria-label="`More actions for ${name}`"
            title="More actions"
          />
        </UDropdownMenu>
      </div>
    </td>
  </tr>
  <template v-if="expanded">
    <tr v-for="device in devices" :key="device.device_id" class="down-device-row">
      <td class="down-device-cell pl-9" :aria-label="`Down device for ${name}`">
        <div class="down-device-name text-sm">
          <span class="sr-only">Down device for {{ name }}: </span>↳ {{ device.device_name || device.device_id || "Unknown device" }}
          <span v-if="device.device_ip" class="device-ip text-xs text-muted">{{ device.device_ip }}</span>
          <span v-if="device.core" class="core-badge" title="A core device: the likely cause of the other devices down here">CORE</span>
          <span
            v-if="setLevel(device)"
            class="set-level-badge"
            :title="`Set ${setLevel(device)} in Nautobot: what this device makes its site when down`"
          >SET {{ setLevel(device).toUpperCase() }}</span>
          <span v-if="device.flapping" class="flap-badge" :title="flapTitle(device)">FLAPPING</span>
        </div>
        <div class="site-meta text-xs text-muted">{{ deviceMeta(device) }}</div>
        <div v-if="ackText(device)" class="ack-note text-xs text-success">{{ ackText(device) }}</div>
        <div v-if="device.probable_cause" class="cause-note text-xs text-muted">Likely caused by {{ device.probable_cause }}</div>
        <div v-if="device.maintenance_until" class="maintenance-note mt-1 text-xs text-muted">
          <LevelBadge level="maintenance" /> {{ maintenanceNote(device.maintenance_until, device.maintenance_reason) }}
        </div>
      </td>
      <td />
      <td class="since-cell whitespace-nowrap text-sm text-toned">{{ sinceLabel("down", deviceDowntimeSeconds(device, now)) }}</td>
      <td class="cases-cell col-cases"><CasePills :cases="device.case_numbers" :template="caseTemplate" empty="—" /></td>
      <td class="col-action" />
    </tr>
  </template>
</template>
