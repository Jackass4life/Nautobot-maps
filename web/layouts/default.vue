<!-- Every staff page (#737): the top bar mounted once, the page under it, so the
     menu stays as a page changes. The map fills the window under the bar. The wall
     has no bar; the sign-in page and a customer's status link have no layout. -->
<script setup lang="ts">
import { activePage } from "~/utils/navigation";

const route = useRoute();
const wall = computed(() => route.meta.view === "wall" || route.query.view === "wall");
const active = computed(() => activePage(route.path, wall.value));
</script>

<template>
  <div :class="active === 'map' ? 'flex h-dvh flex-col overflow-hidden' : undefined">
    <AppBar v-if="!wall" :active="active" />
    <slot />
  </div>
</template>
