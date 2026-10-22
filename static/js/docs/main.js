// The API page (templates/docs.html, #230; Swagger UI since #436).  Swagger UI
// (vendor/swagger-ui-bundle.js, a classic script) draws the API from
// /openapi.json; started here, not inline, for the Content-Security-Policy.
import { initTopBar } from "../lib/nav.js";

initTopBar();

if (window.SwaggerUIBundle) {
  window.SwaggerUIBundle({
    url: "/openapi.json",
    dom_id: "#swagger-ui",
    deepLinking: true,
    // Our own page and bar around it: no Swagger top bar.
    layout: "BaseLayout",
    presets: [window.SwaggerUIBundle.presets.apis],
    defaultModelsExpandDepth: 0,
    docExpansion: "list",
    filter: true,
    tryItOutEnabled: false,
  });
}
