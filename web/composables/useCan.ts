// What the caller may do (#695): `can("alerts.ack")` from page-config's
// `access.permissions` (#693), the permission matrix as the API applies it. Buttons
// and links show only with their permission; the API still checks every request.
// Nothing is allowed until page-config has answered, so a button never shows and
// then goes.
export type Permission =
  | "page.map"
  | "page.board"
  | "page.wall"
  | "page.availability"
  | "page.circuits"
  | "page.maintenance"
  | "alerts.history"
  | "tenant.status.view"
  | "status.links.view"
  | "export.csv"
  | "alerts.ack"
  | "alerts.unack"
  | "alerts.case"
  | "criticality.override"
  | "maintenance.write"
  | "status.links.manage"
  | "alerts.ingest"
  | "examples.write"
  | "admin"
  | "session.basic";

/** Whether *permissions* (page-config's access.permissions) include *permission*. */
export function allows(
  permissions: readonly string[] | undefined,
  permission: Permission,
): boolean {
  return Boolean(permissions?.includes(permission));
}

export function useCan() {
  const { data: config } = usePageConfig();
  const can = (permission: Permission): boolean =>
    allows(config.value?.access?.permissions, permission);
  return { can, config };
}
