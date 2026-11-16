<!-- /alerts: the alert board, or with ?view=wall the wall screen (#243, #463); in a
     workspace (#697), /noc and /nwo/wall are the wall (the route's meta.view). From
     the route, not the address once: the menu goes from one to the other in place
     (#737). -->
<script setup lang="ts">
const route = useRoute();
const wall = computed(() => route.meta.view === "wall" || route.query.view === "wall");
</script>

<template>
  <AlertWall v-if="wall" />
  <AlertBoard v-else />
</template>
