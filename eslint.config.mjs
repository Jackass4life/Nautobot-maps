// ESLint for the browser JavaScript in static/js (#164). Versions are pinned in
// package.json / package-lock.json; run `npm ci && npm run lint:js`.
import js from "@eslint/js";
import globals from "globals";

export default [
  // Third-party code (Leaflet) is not ours to lint.
  { ignores: ["static/vendor/**"] },
  js.configs.recommended,
  {
    files: ["static/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      // Every page script is an ES module (#294): <script type="module">,
      // and imported by the tests in Node.
      sourceType: "module",
      globals: {
        ...globals.browser,
        L: "readonly", // Leaflet, a plain script loaded before map/main.js
      },
    },
    rules: {
      // A leading underscore marks a deliberately unused name, e.g. `catch (_err)`.
      "no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],
    },
  },
];
