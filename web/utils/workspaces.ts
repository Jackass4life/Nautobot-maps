// Each role's own addresses (#697): customer, NOC and NWO (operator) have a
// workspace; viewer and admin keep the flat addresses. The same table as the API's
// permissions.WORKSPACES (api/tests/test_permissions.py checks): each page's path in
// the workspace, "" = the workspace itself (the role's landing page). A tenant's
// status is at <workspace>/tenants/<name> with "tenants".
export const WORKSPACES = {
  customer: { prefix: "/customer", pages: { availability: "", tenants: "/tenants" } },
  noc: {
    prefix: "/noc",
    pages: {
      wall: "",
      alerts: "/board",
      availability: "/availability",
      maintenance: "/maintenance",
    },
  },
  operator: {
    prefix: "/nwo",
    pages: {
      map: "",
      wall: "/wall",
      alerts: "/board",
      availability: "/availability",
      tenants: "/tenants",
      maintenance: "/maintenance",
      circuits: "/circuits",
    },
  },
} as const;

export type WorkspacePage =
  "map" | "wall" | "alerts" | "availability" | "maintenance" | "circuits" | "tenants";

type Workspace = { prefix: string; pages: Partial<Record<WorkspacePage, string>> };
const TABLE = WORKSPACES as Record<string, Workspace>;

// Each page's flat address: the wall is the board with ?view=wall.
const FLAT: Record<WorkspacePage, string> = {
  map: "/",
  wall: "/alerts",
  alerts: "/alerts",
  availability: "/availability",
  maintenance: "/maintenance",
  circuits: "/circuits",
  tenants: "/tenants",
};

// The page files the workspace routes reuse (nuxt.config's pages:extend).
export const PAGE_FILES: Record<WorkspacePage, string> = {
  map: "pages/index.vue",
  wall: "pages/alerts.vue",
  alerts: "pages/alerts.vue",
  availability: "pages/availability.vue",
  maintenance: "pages/maintenance.vue",
  circuits: "pages/circuits.vue",
  tenants: "pages/tenants/index.vue",
};

/** Every workspace address, for the router: path, page and its file. */
export function workspaceRoutes(): { name: string; path: string; page: WorkspacePage }[] {
  const routes = [];
  for (const [role, { prefix, pages }] of Object.entries(TABLE)) {
    for (const [page, path] of Object.entries(pages) as [WorkspacePage, string][]) {
      routes.push({ name: `${role}-${page}`, path: prefix + path || "/", page });
      if (page === "tenants")
        routes.push({ name: `${role}-tenant`, path: `${prefix}${path}/:name`, page });
    }
  }
  return routes;
}

/** Which page *path* shows (flat or in any workspace), and the rest of it (a tenant's
 * "/<name>"); null for the others (help, admin, a status link). *wall*: ?view=wall. */
export function pageAt(path: string, wall: boolean): { page: WorkspacePage; rest: string } | null {
  if (path === "/alerts") return { page: wall ? "wall" : "alerts", rest: "" };
  for (const [page, flat] of Object.entries(FLAT) as [WorkspacePage, string][]) {
    if (path === flat && flat !== "/alerts") return { page, rest: "" };
  }
  const tenant = (base: string) =>
    path.startsWith(`${base}/`) && path.length > base.length + 1
      ? { page: "tenants" as const, rest: path.slice(base.length) }
      : null;
  const flatTenant = tenant("/tenants");
  if (flatTenant) return flatTenant;
  for (const { prefix, pages } of Object.values(TABLE)) {
    for (const [page, sub] of Object.entries(pages) as [WorkspacePage, string][]) {
      if (path === prefix + sub) return { page, rest: "" };
      const found = page === "tenants" ? tenant(prefix + sub) : null;
      if (found) return found;
    }
  }
  return null;
}

// A workspace's old address (#703: the NOC role was DOV until 0.5.1).
const RENAMED: Record<string, string> = { "/dov": "/noc" };

// "/NOC/Board" → "/noc/board": the workspace and its page lowercase, a tenant's name
// as it is; an old workspace address ("/dov/board") at its new one.
function lowercased(path: string): string {
  const parts = path.split("/");
  const asked = `/${(parts[1] ?? "").toLowerCase()}`;
  const prefix = RENAMED[asked] ?? asked;
  const workspace = Object.values(TABLE).find((w) => w.prefix === prefix);
  if (!workspace) return path;
  parts[1] = prefix.slice(1);
  const sub = `/${(parts[2] ?? "").toLowerCase()}`;
  if (parts[2] !== undefined && Object.values(workspace.pages).includes(sub))
    parts[2] = sub.slice(1);
  return parts.join("/");
}

export type WorkspaceMove = { to: string; refused: boolean } | null;

/**
 * Where a role's page load goes instead (#697), or null to stay: "/" to its landing
 * page; a page (flat or in another workspace) to the same page in its own workspace,
 * the query kept; a page outside its workspace to its landing page (*refused*: say
 * so). Roles without a workspace (viewer, admin, nobody signed in) go from a
 * workspace address to the flat one. A capitalised workspace ("/NOC") goes lowercase.
 * Convenience only: the API checks every page and request.
 */
export function workspaceMove(
  role: string | null | undefined,
  landing: string,
  path: string,
  query: string,
): WorkspaceMove {
  const here = query ? `${path}?${query}` : path;
  const clean = lowercased(path);
  const params = new URLSearchParams(query);
  const found = pageAt(clean, params.get("view") === "wall");
  const own = role ? TABLE[role] : undefined;
  let to: string;
  let refused = false;
  if (!found) {
    to = clean === path ? here : query ? `${clean}?${query}` : clean;
  } else if (own && own.pages[found.page] === undefined) {
    // A page outside the workspace: its landing page ("/" quietly).
    to = landing;
    refused = clean !== "/";
  } else {
    const workspace = own !== undefined;
    let base = workspace
      ? own.prefix + own.pages[found.page] + found.rest
      : FLAT[found.page] + found.rest;
    base ||= "/";
    // The wall's ?view=wall: in the flat address only.
    if (found.page === "wall") {
      if (workspace) params.delete("view");
      else params.set("view", "wall");
    }
    const text = params.toString();
    to = text ? `${base}?${text}` : base;
  }
  return to === here ? null : { to, refused };
}
