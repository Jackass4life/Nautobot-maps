<!-- The board's sites with missing or wrong coordinates (#368): one line
     under the map's count, the list behind its Check button, worst first,
     and a CSV with every one.  Nothing when all is well; a failure leaves it
     out too (the map works). -->
<script setup lang="ts">
import {
  COORDINATE_REFRESH_MS,
  canShow,
  coordinateList,
  coordinateSummary,
  type CoordinateProblem,
} from "~/utils/coordinates";
import { nautobotLocationUrl } from "~/utils/format";

// *query*: the page's ?tenant= / ?q= (#367): a map for some sites only lists theirs.
const props = defineProps<{ query: string; nautobotUrl: string }>();
const emit = defineEmits<{ show: [id: string] }>();

const sites = shallowRef<CoordinateProblem[]>([]);
const open = ref(false);
const summary = computed(() => coordinateSummary(sites.value));
const list = computed(() => coordinateList(sites.value));
const suffix = computed(() => (props.query ? `?${props.query}` : ""));

async function load() {
  try {
    const data = await $fetch<{ sites: CoordinateProblem[] }>(`/api/locations/coordinate-check${suffix.value}`);
    sites.value = data.sites || [];
  } catch (err) {
    console.warn("Coordinate check unavailable:", err);
  }
}
function where(item: CoordinateProblem) {
  return item.latitude == null ? "" : ` (${item.latitude}, ${item.longitude})`;
}

let timer: ReturnType<typeof setInterval> | undefined;
// Once the locations are in (the page mounts this then).
onMounted(() => {
  load();
  timer = setInterval(load, COORDINATE_REFRESH_MS);
});
onBeforeUnmount(() => clearInterval(timer));
</script>

<template>
  <div v-if="summary" id="coordinate-note" class="coordinate-note text-xs text-muted">
    <span id="coordinate-text">{{ summary }}</span>
    ·
    <button
      id="coordinate-toggle"
      class="coordinate-toggle text-primary hover:underline"
      type="button"
      :aria-expanded="open ? 'true' : 'false'"
      aria-controls="coordinate-list"
      @click="open = !open"
    >Check</button>
    <div v-show="open" id="coordinate-list" class="coordinate-list mt-2 max-h-64 space-y-2 overflow-y-auto">
      <ul class="coordinate-items space-y-2">
        <template v-for="(group, index) in [list.wrong, list.far]" :key="index">
          <li v-if="index === 1 && group.length" class="coordinate-group pt-1 font-medium">Far from the other sites in their region: worth a check</li>
          <li v-for="item in group" :key="item.site_id">
            <div class="coordinate-site flex items-center gap-2 text-toned">
              <a
                v-if="nautobotUrl"
                :href="nautobotLocationUrl(nautobotUrl, item.site_id)"
                target="_blank"
                rel="noopener noreferrer"
                title="Fix it in Nautobot"
                class="hover:underline"
              >{{ item.name || item.site_id }} ↗</a>
              <span v-else>{{ item.name || item.site_id }}</span>
              <button
                v-if="canShow(item)"
                class="coordinate-show text-primary hover:underline"
                type="button"
                @click="emit('show', item.site_id)"
              >Show</button>
            </div>
            <span v-if="item.ancestor_path" class="coordinate-path block">{{ item.ancestor_path }}</span>
            <div class="coordinate-reason">{{ item.reason }}{{ where(item) }}</div>
          </li>
        </template>
        <li v-if="list.more" class="coordinate-more">+{{ list.more }} more in the CSV</li>
      </ul>
      <a class="coordinate-csv text-primary hover:underline" :href="`/api/locations/coordinate-check.csv${suffix}`">Download CSV</a>
    </div>
  </div>
</template>
