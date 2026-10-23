<!-- A small (i) that explains what is next to it, for someone new to the
     page (#342): a click, or a tap, opens it; Escape closes it. -->
<script setup lang="ts">
defineProps<{ text: string }>();
</script>

<template>
  <UPopover :content="{ side: 'top' }">
    <button type="button" class="help-tip inline-flex align-middle text-muted hover:text-highlighted" :aria-label="`Help: ${text}`">
      <UIcon name="i-lucide-info" class="size-4" />
    </button>
    <template #content>
      <p class="info-tip-box max-w-72 p-3 text-sm font-normal text-toned">{{ text }}</p>
    </template>
  </UPopover>
</template>
