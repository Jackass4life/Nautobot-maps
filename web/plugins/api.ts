import createClient from "openapi-fetch";
import type { paths } from "~/api/api.schema";

// The typed API client (#572, #609): every call to the API goes through it, as
// `const { $api } = useNuxtApp()`, with the paths and bodies of api/openapi.json.
export default defineNuxtPlugin(() => {
  const config = useRuntimeConfig();
  // The API is this page's origin (#594); the sign-in proxy's cookie comes along.
  const api = createClient<paths>({ baseUrl: config.public.apiBase, credentials: "same-origin" });
  return { provide: { api } };
});
