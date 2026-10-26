<!-- The site panel (#311, #394, #398): opens from a marker, the alarm list,
     a search or ?location_id=.  What is wrong first (one status line, the
     down devices, core first), the site's facts, then the rest folded with
     counts.  A new site starts with everything closed. -->
<script setup lang="ts">
import { formatFeedTime, nautobotCircuitUrl, nautobotLocationUrl } from "~/utils/format";
import type { MapLocation } from "~/utils/map";
import { tenantDescriptionIn } from "~/utils/tenants";
import { caseUrl } from "~/utils/board";
import {
  casesShown,
  circuitMatchNote,
  circuitTermination,
  deviceHealth,
  deviceKey,
  filterDevices,
  groupStacks,
  healthCounts,
  maintenanceHistoryHref,
  noCaseText,
  sortDownDevices,
  statusLine,
  type Health,
  type LocationDetail,
  type SiteDevice,
} from "~/utils/sitepanel";

const props = defineProps<{
  loc: MapLocation | null;
  // Every site at its coordinates (tabs), itself included.
  group: MapLocation[];
  detail: LocationDetail | null;
  loading: boolean;
  error: string;
  since: string | null | undefined;
  nautobotUrl: string;
  // The ITSM's ticket link for a case number (#289, #512); "" for none.
  caseUrlTemplate?: string;
}>();
const emit = defineEmits<{ close: []; select: [id: string]; retry: [] }>();

// The down devices first: the worst eight, then "Show all 34".
const DOWN_SHOWN = 8;
// Filters and a search only where they help.
const TOOLBAR_ABOVE = 10;

const closeButton = useTemplateRef<HTMLButtonElement>("close");
defineExpose({ focus: () => closeButton.value?.focus() });

// What the operator opened, kept while the panel is redrawn (#394).
const folds = ref(new Map<string, boolean>());
const openDevices = ref(new Set<string>());
const downExpanded = ref(false);
const deviceFilter = ref<"all" | Health>("all");
const deviceSearch = ref("");
watch(
  () => props.loc?.id,
  () => {
    folds.value = new Map();
    openDevices.value = new Set();
    downExpanded.value = false;
    deviceFilter.value = "all";
    deviceSearch.value = "";
  },
);

const subtitle = computed(() => {
  const loc = props.loc;
  if (!loc) return "";
  const here = props.group.length > 1 ? `${props.group.length} locations here` : "";
  return [loc.location_type, loc.ancestor_path || loc.parent, here].filter(Boolean).join(" · ");
});
const tenantDescription = computed(() => {
  const loc = props.loc;
  return loc?.tenant ? tenantDescriptionIn(loc.tenant_descriptions, loc.tenant) : "";
});
// The site's facts that have a value (#398); the ASN is left to the ASNs fold when that lists them.
const info = computed(() => {
  const loc = props.loc;
  if (!loc) return [];
  const asnListed = (props.detail?.asns?.length ?? 0) > 0;
  return [
    ["Address", loc.physical_address],
    ["Tenant", loc.tenant],
    ["Tenant group", loc.tenant_group],
    ["Facility", loc.facility],
    ["ASN", asnListed || loc.asn == null ? "" : String(loc.asn)],
    ["Time zone", loc.time_zone],
    ["Tags", (loc.tags || []).join(", ")],
    ["Description", loc.description],
  ].filter(([, value]) => value) as [string, string][];
});
const status = computed(() =>
  statusLine(props.detail?.alert, props.detail?.counts, props.since ? formatFeedTime(props.since) : ""),
);
// Planned, Decommissioning...: said; Active is the norm and isn't.
const lifecycle = computed(() => (props.loc?.status && props.loc.status.toLowerCase() !== "active" ? props.loc.status : ""));

const devices = computed<SiteDevice[]>(() => (props.detail && !props.loading && !props.error ? props.detail.devices : []));
const stacks = computed(() => groupStacks(devices.value));
const down = computed(() => sortDownDevices(devices.value));
// Who is on it (#512): the site's cases, linked to the ITSM, and the down
// devices nobody is on.
const cases = computed(() => {
  const { shown, more } = casesShown(props.detail?.cases || []);
  return { shown: shown.map((number) => ({ number, url: caseUrl(number, props.caseUrlTemplate || "") })), more };
});
const noCase = computed(() => noCaseText(down.value, Boolean(props.detail?.cases_read), cases.value.shown.length > 0));
const downShown = computed(() => (downExpanded.value ? down.value : down.value.slice(0, DOWN_SHOWN)));
// A stack's lead brings its members, as under All devices (#400), but not
// those with their own row here; it still says the stack's size (#405).
const stackOf = computed(() => new Map(stacks.value.map(({ device, members }) => [device, members])));
const downSet = computed(() => new Set(down.value));
function downMembers(device: SiteDevice) {
  return (stackOf.value.get(device) || []).filter((member) => !downSet.value.has(member));
}

const counts = computed(() => healthCounts(devices.value));
const filterKeys = computed(() => (["all", "active", "down", "unknown"] as const).filter((key) => key === "all" || counts.value[key]));
const FILTER_LABELS = { all: "All", active: "Active", down: "Down", unknown: "Unknown" };
const toolbar = computed(() => devices.value.length > TOOLBAR_ABOVE);
// A filter or search keeps a stack whole when any of its devices matches.
const listed = computed(() => {
  if (!toolbar.value) return stacks.value;
  const matching = new Set(filterDevices(devices.value, deviceFilter.value, deviceSearch.value));
  return stacks.value.filter(({ device, members }) => [device, ...members].some((d) => matching.has(d)));
});
// "12 (4 in stacks)": every device counted, the members folded into their stack's row.
const deviceCount = computed(() => {
  const inStacks = devices.value.length - stacks.value.length;
  return inStacks ? `${devices.value.length} (${inStacks} in stacks)` : String(devices.value.length);
});
const circuits = computed(() => props.detail?.circuits ?? []);
const asns = computed(() => props.detail?.asns ?? []);

function foldOpen(key: string, byDefault: boolean): boolean {
  return folds.value.has(key) ? folds.value.get(key)! : byDefault;
}
// One open by default isn't remembered as chosen: only a click is.  The
// fold opens from this state, not the browser's own toggle, which would undo
// it when the panel is redrawn on the same click.
function toggleFold(key: string, event: MouseEvent) {
  event.preventDefault();
  const fold = (event.currentTarget as HTMLElement).parentElement as HTMLDetailsElement;
  folds.value.set(key, !fold.open);
}
function toggleDevice(key: string) {
  if (openDevices.value.has(key)) openDevices.value.delete(key);
  else openDevices.value.add(key);
}
function statusClass(status: string) {
  const health = deviceHealth(status);
  return health === "active" ? "text-success" : health === "down" ? "text-error" : "text-muted";
}
</script>

<template>
  <aside
    id="location-inspector"
    class="z-30 flex flex-col border-default bg-default shadow-xl max-sm:fixed max-sm:inset-x-0 max-sm:bottom-0 max-sm:max-h-[75dvh] max-sm:rounded-t-xl max-sm:border-t sm:absolute sm:inset-y-0 sm:right-0 sm:w-96 sm:border-l"
    :class="{ hidden: !loc }"
    :aria-hidden="loc ? 'false' : 'true'"
    aria-labelledby="inspector-title"
  >
    <div v-if="loc" class="inspector-shell min-h-0 flex-1 overflow-y-auto">
      <div class="sticky top-0 z-10 flex items-start gap-2 border-b border-default bg-default px-4 py-3">
        <div class="min-w-0 flex-1">
          <h2 id="inspector-title" class="truncate text-lg font-semibold text-highlighted">{{ loc.name || "Site" }}</h2>
          <div id="inspector-subtitle" class="text-xs text-muted">{{ subtitle }}</div>
        </div>
        <UButton
          id="inspector-close"
          ref="close"
          icon="i-lucide-x"
          color="neutral"
          variant="ghost"
          aria-label="Close the site panel"
          @click="emit('close')"
        />
      </div>

      <!-- Co-located sites: the status is the chosen one's. -->
      <div v-if="group.length > 1" id="inspector-site-tabs" class="flex flex-wrap gap-1 px-4 pt-3" role="group" aria-label="Co-located locations">
        <UButton
          v-for="site in group"
          :key="site.id"
          class="inspector-site-tab"
          :data-location-id="site.id"
          size="xs"
          :color="site.id === loc.id ? 'primary' : 'neutral'"
          :variant="site.id === loc.id ? 'soft' : 'ghost'"
          :aria-pressed="site.id === loc.id ? 'true' : 'false'"
          @click="emit('select', site.id)"
        >{{ site.name }}</UButton>
      </div>

      <div id="inspector-content" class="space-y-4 p-4">
        <div class="site-summary space-y-3">
          <div v-if="info.length" class="site-info space-y-1 text-sm">
            <div v-for="[label, value] in info" :key="label" class="inspector-row flex gap-3">
              <span class="inspector-row-label w-24 shrink-0 text-muted">{{ label }}</span>
              <span class="inspector-row-value min-w-0 flex-1 break-words">
                {{ value }}
                <HelpTip v-if="label === 'Tenant' && tenantDescription" :text="tenantDescription" />
              </span>
            </div>
          </div>

          <div v-if="loading" class="site-status is-loading text-sm text-muted">Loading alert status…</div>
          <div v-else-if="error" class="site-status is-no_data text-sm text-muted">Alert status unavailable</div>
          <div v-else-if="detail" class="site-status rounded-md bg-elevated px-3 py-2 text-sm" :class="`is-${status.level}`">
            <div class="site-status-line flex items-center gap-2 font-medium text-highlighted">
              <span class="legend-dot" :class="`dot-${status.level.replace('_', '-')}`" aria-hidden="true" />{{ status.text }}
            </div>
            <div v-if="status.reason" class="site-status-reason mt-0.5 pl-5 text-muted">{{ status.reason }}</div>
            <div v-if="cases.shown.length || noCase" class="site-cases mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-5">
              <template v-if="cases.shown.length">
                <span class="text-muted">Case</span>
                <template v-for="item in cases.shown" :key="item.number">
                  <a
                    v-if="item.url"
                    class="site-case font-mono text-xs text-primary hover:underline"
                    :href="item.url"
                    target="_blank"
                    rel="noopener noreferrer"
                  >{{ item.number }}</a>
                  <span v-else class="site-case font-mono text-xs text-highlighted">{{ item.number }}</span>
                </template>
                <span v-if="cases.more" class="text-xs text-muted">+{{ cases.more }}</span>
              </template>
              <UBadge v-if="noCase" class="site-no-case" color="error" variant="subtle" size="sm">{{ noCase }}</UBadge>
            </div>
          </div>

          <div class="inspector-actions-row flex flex-wrap items-center gap-3 text-sm">
            <UBadge v-if="lifecycle" class="popup-badge" color="warning" variant="subtle">{{ lifecycle }}</UBadge>
            <a
              v-if="nautobotUrl"
              class="nautobot-link text-primary hover:underline"
              :href="nautobotLocationUrl(nautobotUrl, loc.id)"
              target="_blank"
              rel="noopener noreferrer"
            >Open in Nautobot ↗</a>
            <a class="nautobot-link text-primary hover:underline" :href="maintenanceHistoryHref(loc.id)">Maintenance history</a>
          </div>
        </div>

        <div v-if="error" class="inspector-error space-y-2 text-sm text-error">
          <p>Could not load details: {{ error }}</p>
          <UButton id="inspector-retry" color="neutral" variant="outline" size="sm" @click="emit('retry')">Retry</UButton>
        </div>
        <div v-else-if="loading" class="inspector-empty text-sm text-muted">Loading devices…</div>
        <template v-else-if="detail">
          <section v-if="down.length" aria-labelledby="inspector-down-title">
            <h3 id="inspector-down-title" class="panel-heading mb-1 text-sm font-semibold text-highlighted">Down · {{ down.length }}</h3>
            <ul class="device-rows" aria-label="Down devices">
              <DeviceRow
                v-for="device in downShown"
                :key="deviceKey(device)"
                :device="device"
                :open="openDevices.has(deviceKey(device))"
                :members="downMembers(device)"
                :size="(stackOf.get(device)?.length ?? 0) + 1"
                @toggle="toggleDevice"
              />
            </ul>
            <UButton
              v-if="down.length > DOWN_SHOWN"
              class="panel-more mt-1"
              data-down-toggle
              size="xs"
              color="neutral"
              variant="link"
              @click="downExpanded = !downExpanded"
            >{{ downExpanded ? "Show fewer" : `Show all ${down.length}` }}</UButton>
          </section>

          <div class="panel-folds space-y-2">
            <p v-if="!devices.length" class="inspector-empty text-sm text-muted">No devices at this site.</p>
            <details v-if="devices.length" class="panel-fold" data-fold="devices" :open="foldOpen('devices', !down.length && devices.length <= TOOLBAR_ABOVE)">
              <summary class="panel-fold-title" @click="toggleFold('devices', $event)">All devices · {{ deviceCount }}</summary>
              <div class="panel-fold-body">
                <div v-if="toolbar" class="inspector-device-toolbar mb-2 space-y-2">
                  <div class="device-filters flex flex-wrap gap-1" aria-label="Device filters">
                    <UButton
                      v-for="key in filterKeys"
                      :key="key"
                      class="device-filter-btn"
                      :data-device-filter="key"
                      size="xs"
                      :color="deviceFilter === key ? 'primary' : 'neutral'"
                      :variant="deviceFilter === key ? 'soft' : 'ghost'"
                      :aria-pressed="deviceFilter === key ? 'true' : 'false'"
                      @click="deviceFilter = key"
                    >{{ FILTER_LABELS[key] }} {{ counts[key] }}</UButton>
                  </div>
                  <UInput
                    id="inspector-device-search"
                    v-model="deviceSearch"
                    type="search"
                    size="sm"
                    icon="i-lucide-search"
                    placeholder="Search devices"
                    aria-label="Search devices"
                    class="w-full"
                  />
                </div>
                <ul v-if="listed.length" class="device-rows" aria-label="Devices">
                  <DeviceRow
                    v-for="entry in listed"
                    :key="deviceKey(entry.device)"
                    :device="entry.device"
                    :open="openDevices.has(deviceKey(entry.device))"
                    :members="entry.members"
                    @toggle="toggleDevice"
                  />
                </ul>
                <p v-else class="inspector-empty text-sm text-muted">No device matches.</p>
              </div>
            </details>

            <details v-if="detail.circuits_error || circuits.length" class="panel-fold" data-fold="circuits" :open="foldOpen('circuits', false)">
              <summary class="panel-fold-title" @click="toggleFold('circuits', $event)">
                Circuits<template v-if="!detail.circuits_error"> · {{ circuits.length }}</template>
              </summary>
              <div class="panel-fold-body">
                <p v-if="detail.circuits_error" class="inspector-empty text-sm text-muted">{{ detail.circuits_error }}</p>
                <ul v-else class="space-y-2" aria-label="Circuit list">
                  <li v-for="circuit in circuits" :key="circuit.id + (circuit.term_side || '')" class="circuit-card rounded-md border border-default p-2 text-xs">
                    <div class="flex items-center gap-2">
                      <a
                        v-if="nautobotUrl && circuit.id"
                        class="circuit-link min-w-0 flex-1 truncate text-sm font-medium text-primary hover:underline"
                        :href="nautobotCircuitUrl(nautobotUrl, circuit.id)"
                        target="_blank"
                        rel="noopener noreferrer"
                      >{{ circuit.cid }}</a>
                      <span v-else class="min-w-0 flex-1 truncate text-sm font-medium">{{ circuit.cid }}</span>
                      <span class="shrink-0" :class="statusClass(circuit.status)">{{ circuit.status || "Unknown" }}</span>
                    </div>
                    <div class="mt-1 space-y-0.5 text-muted">
                      <div
                        v-if="circuitMatchNote(circuit)"
                        class="circuit-match"
                        title="The circuit's description is this site's address; it has no termination here in Nautobot"
                      >{{ circuitMatchNote(circuit) }}</div>
                      <div v-if="circuit.provider || circuit.circuit_type">{{ [circuit.provider, circuit.circuit_type].filter(Boolean).join(" · ") }}</div>
                      <div v-if="circuitTermination(circuit)">{{ circuitTermination(circuit) }}</div>
                      <div v-if="circuit.commit_rate">Commit rate: {{ circuit.commit_rate }}</div>
                      <div v-if="circuit.xconnect_id">Cross-connect: {{ circuit.xconnect_id }}</div>
                      <div v-if="circuit.pp_info">Patch panel: {{ circuit.pp_info }}</div>
                      <div v-if="circuit.tenant">Tenant: {{ circuit.tenant }}</div>
                      <div v-if="circuit.description">{{ circuit.description }}</div>
                    </div>
                  </li>
                </ul>
              </div>
            </details>

            <details v-if="asns.length" class="panel-fold" data-fold="asns" :open="foldOpen('asns', false)">
              <summary class="panel-fold-title" @click="toggleFold('asns', $event)">ASNs · {{ asns.length }}</summary>
              <div class="panel-fold-body flex flex-wrap gap-1">
                <UBadge v-for="asn in asns" :key="String(asn.asn) + asn.tenant" class="asn-tag" color="neutral" variant="subtle" :title="asn.description">
                  AS{{ asn.asn }}<template v-if="asn.tenant"> · {{ asn.tenant }}</template>
                </UBadge>
              </div>
            </details>
          </div>
        </template>
      </div>
    </div>
  </aside>
</template>
