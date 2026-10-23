<!-- The legend (#311, #392): on the map, behind a small button.  One
     meaning per colour, the alert level, as on the board. -->
<script setup lang="ts">
const open = ref(false);
const ITEMS = [
  ["critical", "Critical: a core device or every device down"],
  ["medium", "Medium: more than 25% of devices down"],
  ["low", "Low: a device down"],
  ["ok", "OK"],
  ["maintenance", "Maintenance"],
  ["no-data", "No data (no monitored devices)"],
  ["search", "Searched point"],
];
</script>

<template>
  <div id="map-legend" class="map-legend absolute bottom-6 left-3 z-[500] max-w-72">
    <div v-show="open" id="legend" class="mb-2 space-y-1.5 rounded-lg border border-default bg-default/95 p-3 text-xs shadow-lg">
      <div class="legend-title font-semibold text-highlighted">Legend</div>
      <div v-for="[level, text] in ITEMS" :key="level" class="legend-item flex items-center gap-2">
        <span class="legend-dot" :class="`dot-${level}`" />{{ text }}
      </div>
      <!-- #334: what a circle with a number means. -->
      <p class="legend-note pt-1 text-muted">
        Sites in alarm or maintenance are always their own pin. Sites without an alarm are small dots, grouped into numbered circles when zoomed out.
      </p>
    </div>
    <UButton
      id="legend-toggle"
      class="legend-toggle shadow"
      icon="i-lucide-info"
      color="neutral"
      variant="outline"
      size="sm"
      :aria-expanded="open ? 'true' : 'false'"
      aria-controls="legend"
      aria-label="Legend"
      title="What the colours mean"
      @click="open = !open"
    />
  </div>
</template>
