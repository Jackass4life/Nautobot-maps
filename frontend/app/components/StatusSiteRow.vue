<!-- One site on the tenant status page (#291, #463): its state, and since or
     until when; under its name, where it is, opening OpenStreetMap in a new
     tab (#493); and how many devices it has, and how many are down (#503). -->
<script setup lang="ts">
import { hasDown, siteDevices, sitePlace, siteWhen, stateLabel, type StatusSite } from "~/utils/status";

const props = defineProps<{ site: StatusSite; now: Date; dot: Record<string, string> }>();
const place = computed(() => sitePlace(props.site));
const devices = computed(() => siteDevices(props.site));
</script>

<template>
  <li class="status-site flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3" :class="`status-${site.state}`">
    <span class="size-2.5 shrink-0 rounded-full" :class="dot[site.state] || 'bg-slate-400'" aria-hidden="true" />
    <div class="min-w-0 flex-1 basis-48">
      <span class="block font-semibold break-words text-highlighted">{{ site.name }}</span>
      <a
        v-if="place"
        class="status-place inline-flex max-w-full items-start gap-1 text-sm text-muted hover:text-primary hover:underline"
        :href="place.href"
        target="_blank"
        rel="noopener noreferrer"
        :aria-label="`${place.text}: open on OpenStreetMap in a new tab`"
      >
        <UIcon name="i-lucide-map-pin" class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <span class="min-w-0 break-words">{{ place.text }}</span>
      </a>
      <span v-if="devices" class="status-devices block text-sm" :class="hasDown(site) ? 'text-toned' : 'text-muted'">{{
        devices
      }}</span>
    </div>
    <span :class="site.state === 'outage' || site.state === 'degraded' ? 'font-semibold text-highlighted' : 'text-muted'">
      {{ stateLabel(site.state) }}
      <span v-if="siteWhen(site, now)" class="font-normal text-muted">{{ siteWhen(site, now) }}</span>
    </span>
  </li>
</template>
