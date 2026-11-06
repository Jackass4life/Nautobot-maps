// What the API page uses of Swagger UI's ES bundle (#463); the package has no types for it.
declare module "swagger-ui-dist/swagger-ui-es-bundle.js" {
  interface SwaggerUIBundle {
    (options: Record<string, unknown> & { url: string; domNode: HTMLElement | null | undefined }): unknown;
    presets: { apis: unknown };
  }
  const bundle: SwaggerUIBundle;
  export default bundle;
}
