<!-- One site on the tenant status page (#291, #463): its state, and since or
     until when. -->
<script setup lang="ts">
defineProps<{ site: StatusSite; now: Date; dot: Record<string, string> }>();
</script>

<template>
  <li class="status-site flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3" :class="`status-${site.state}`">
    <span class="size-2.5 shrink-0 rounded-full" :class="dot[site.state] || 'bg-muted'" aria-hidden="true" />
    <span class="min-w-0 flex-1 basis-48 font-semibold break-words text-highlighted">{{ site.name }}</span>
    <span :class="site.state === 'outage' || site.state === 'degraded' ? 'font-semibold text-highlighted' : 'text-muted'">
      {{ stateLabel(site.state) }}
      <span v-if="siteWhen(site, now)" class="font-normal text-muted">{{ siteWhen(site, now) }}</span>
    </span>
  </li>
</template>
