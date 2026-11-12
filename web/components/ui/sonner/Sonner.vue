<script lang="ts" setup>
// shadcn-vue's Sonner (new-york-v4, #640): the app's toasts, put once in app.vue.
// Its colours are shadcn's tokens (assets/css/shadcn.css, .toaster), so Nuxt UI's;
// the icons are the iconify Lucide set, bundled like every other icon.
import type { ToasterProps } from "vue-sonner";
import { Toaster } from "vue-sonner";
import { cn } from "@/lib/utils";

const props = defineProps<ToasterProps>();
</script>

<template>
  <Toaster :class="cn('toaster group', props.class)" v-bind="props">
    <template #success-icon>
      <UIcon name="i-lucide-circle-check" class="size-4" />
    </template>
    <template #info-icon>
      <UIcon name="i-lucide-info" class="size-4" />
    </template>
    <template #warning-icon>
      <UIcon name="i-lucide-triangle-alert" class="size-4" />
    </template>
    <template #error-icon>
      <UIcon name="i-lucide-octagon-x" class="size-4" />
    </template>
    <template #loading-icon>
      <div>
        <UIcon name="i-lucide-loader-2" class="size-4 animate-spin" />
      </div>
    </template>
    <template #close-icon>
      <UIcon name="i-lucide-x" class="size-4" />
    </template>
  </Toaster>
</template>
