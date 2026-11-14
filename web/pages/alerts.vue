<!-- /alerts: the alert board, or with ?view=wall the wall screen (#243, #463); in a
     workspace (#697), /noc and /nwo/wall are the wall (the route's meta.view). -->
<script setup lang="ts">
const wall =
  useRoute().meta.view === "wall" ||
  new URLSearchParams(window.location.search).get("view") === "wall";
</script>

<template>
  <AlertWall v-if="wall" />
  <AlertBoard v-else />
</template>
