// Nuxt's rules for Vue and TypeScript (@nuxt/eslint; `nuxt prepare` writes
// .nuxt/eslint.config.mjs).
import withNuxt from "./.nuxt/eslint.config.mjs";

export default withNuxt({ ignores: ["app/api/schema.d.ts"] });
