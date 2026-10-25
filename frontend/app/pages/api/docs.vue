<!-- The API (#230; Swagger UI since #436; on Nuxt since #463): every endpoint,
     what it takes and answers, from /api/openapi.json.  Swagger UI comes from npm
     (swagger-ui-dist), loaded on this page only. -->
<script setup lang="ts">
import "swagger-ui-dist/swagger-ui.css";
import { errorMessage } from "~/utils/format";

useHead({ title: "API · Nautobot Maps" });

const box = useTemplateRef<HTMLElement>("swagger");
const failed = ref("");

onMounted(async () => {
  try {
    const { default: SwaggerUIBundle } = await import("swagger-ui-dist/swagger-ui-es-bundle.js");
    SwaggerUIBundle({
      url: "/api/openapi.json",
      domNode: box.value,
      deepLinking: true,
      // Our own page and bar around it: no Swagger top bar.
      layout: "BaseLayout",
      presets: [SwaggerUIBundle.presets.apis],
      defaultModelsExpandDepth: 0,
      docExpansion: "list",
      filter: true,
      tryItOutEnabled: false,
    });
  } catch (err) {
    failed.value = errorMessage(err);
  }
});
</script>

<template>
  <div>
    <AppBar active="docs" />
    <main class="mx-auto max-w-6xl space-y-4 px-4 py-6">
      <h1 class="text-2xl font-bold text-highlighted">API</h1>
      <p class="max-w-3xl text-sm text-muted">
        Every endpoint, what it takes and what it needs. <strong class="text-highlighted">Try it out</strong> sends a real request as
        you: your login and role apply, and POST and DELETE change data. As OpenAPI:
        <a class="text-primary hover:underline" href="/api/openapi.json">/api/openapi.json</a>.
      </p>
      <p v-if="failed" class="text-sm text-error">The API page could not load: {{ failed }}. The API as JSON: <a class="underline" href="/api/openapi.json">/api/openapi.json</a>.</p>
      <!-- Swagger UI draws the API here. -->
      <div id="swagger-ui" ref="swagger" class="docs-swagger" />
    </main>
  </div>
</template>
