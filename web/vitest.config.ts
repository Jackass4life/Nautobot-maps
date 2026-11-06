import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// The pages' logic (#463, #609), outside Nuxt: the pages themselves are tested
// end to end (e2e/, Playwright).
export default defineConfig({
  resolve: { alias: { "~": fileURLToPath(new URL("./", import.meta.url)) } },
  test: { include: ["tests/**/*.test.ts"], env: { TZ: "UTC" } },
});
