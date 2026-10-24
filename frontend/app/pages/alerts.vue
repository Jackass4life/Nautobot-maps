<!-- /alerts: the alert board, or with ?view=wall the wall screen (#243, #463). -->
<script setup lang="ts">
const wall = new URLSearchParams(window.location.search).get("view") === "wall";
</script>

<template>
  <AlertWall v-if="wall" />
  <AlertBoard v-else />
</template>
