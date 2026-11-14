import createClient from "openapi-fetch";
import type { paths } from "~/api/api.schema";
import { signInAddress } from "~/utils/signin";

// The typed API client (#572, #609): every call to the API goes through it, as
// `const { $api } = useNuxtApp()`, with the paths and bodies of api/openapi.json.
export default defineNuxtPlugin(() => {
  const config = useRuntimeConfig();
  // The API is this page's origin (#594); the sign-in proxy's cookie comes along.
  const api = createClient<paths>({ baseUrl: config.public.apiBase, credentials: "same-origin" });
  // Not signed in (any more), with the app's own sign-in on (#717): the API's 401 names
  // the sign-in page; go there, and back here afterwards.
  api.use({
    async onResponse({ response }) {
      if (response.status !== 401 || window.location.pathname === "/auth") return;
      const body = (await response
        .clone()
        .json()
        .catch(() => null)) as { sign_in?: unknown } | null;
      if (typeof body?.sign_in === "string" && body.sign_in.startsWith("/"))
        window.location.assign(
          signInAddress(body.sign_in, window.location.pathname + window.location.search),
        );
    },
  });
  return { provide: { api } };
});
