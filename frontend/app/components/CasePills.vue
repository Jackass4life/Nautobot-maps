<!-- Case numbers as pills (#289): a link to the ticket with CASE_URL_TEMPLATE. -->
<script setup lang="ts">
import { caseUrl } from "~/utils/board";

defineProps<{ cases: string[] | null | undefined; template: string; empty?: string }>();
</script>

<template>
  <span v-if="cases?.length" class="inline-flex flex-wrap gap-1">
    <template v-for="value in cases" :key="value">
      <a
        v-if="caseUrl(value, template)"
        class="case-pill"
        :href="caseUrl(value, template)"
        target="_blank"
        rel="noopener noreferrer"
        title="Open the ticket"
      >{{ value }}</a>
      <span v-else class="case-pill">{{ value }}</span>
    </template>
  </span>
  <span v-else-if="empty" class="text-dimmed">{{ empty }}</span>
</template>
