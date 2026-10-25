<!-- A row's tenants (#279, #342; as the board's tenantCell): the first, and
     "+N" that shows the rest in place; an (i) for a tenant's description. -->
<script setup lang="ts">
// *links*: each name opens the tenant's status page (#461), in a new tab so
// the report stays as it was (#494).
const props = defineProps<{ tenants: string[]; descriptions?: Record<string, string>; links?: boolean }>();
const all = ref(false);
const shown = computed(() => (all.value ? props.tenants : props.tenants.slice(0, 1)));
</script>

<template>
  <span v-if="tenants.length" class="tenant-list inline-flex flex-wrap items-center gap-x-1">
    <template v-for="(tenant, index) in shown" :key="tenant">
      <span v-if="index" class="text-muted">,</span>
      <a
        v-if="links"
        class="tenant-link hover:text-primary hover:underline"
        :href="`/status/${encodeURIComponent(tenant)}`"
        target="_blank"
        rel="noopener"
      >{{ tenant }}</a>
      <span v-else>{{ tenant }}</span>
      <UTooltip v-if="descriptions?.[tenant]" :text="descriptions[tenant]">
        <UIcon name="i-lucide-info" class="size-3.5 text-muted" :aria-label="`${tenant}: ${descriptions[tenant]}`" tabindex="0" />
      </UTooltip>
    </template>
    <button
      v-if="!all && tenants.length > 1"
      type="button"
      class="tenant-more-btn text-primary hover:underline"
      :title="tenants.slice(1).join(', ')"
      @click="all = true"
    >+{{ tenants.length - 1 }}</button>
  </span>
</template>
