// The top bar's menu (#737): every page, in order, here in the web code so the menu
// is there on the first paint (it came from page-config until #737). The same keys,
// addresses and labels as the API's navigation_service.PAGES (api/tests/
// test_top_bar.py checks); each page's permission as permissions.PAGE_PERMISSIONS.
import type { Permission } from "~/composables/useCan";
import { WORKSPACES, pageAt, type WorkspacePage } from "./workspaces";

export type MenuPage = {
  key: string;
  path: string;
  label: string;
  title: string;
  permission: Permission;
};

export const MENU_PAGES: readonly MenuPage[] = [
  { key: "map", path: "/", label: "Map", title: "Every site on a map", permission: "page:map" },
  {
    key: "alerts",
    path: "/alerts",
    label: "Alert Board",
    title: "The sites in alarm, cases and history",
    permission: "page:board",
  },
  {
    key: "availability",
    path: "/availability",
    label: "Availability",
    title: "Availability per site and tenant, by month",
    permission: "page:availability",
  },
  {
    key: "maintenance",
    path: "/maintenance",
    label: "Maintenance",
    title: "Current and planned maintenance, and planning more",
    permission: "page:maintenance",
  },
  {
    key: "circuits",
    path: "/circuits",
    label: "Circuits",
    title: "Circuits without a termination, and their likely site",
    permission: "page:circuits",
  },
  {
    key: "tenants",
    path: "/tenants",
    label: "Customers",
    title: "Each customer's status, as they see it",
    permission: "tenant:status:view",
  },
  {
    key: "help",
    path: "/help",
    label: "Documentation",
    title: "How it works, setup and every guide",
    permission: "session:basic",
  },
  {
    key: "docs",
    path: "/api/docs",
    label: "API",
    title: "Every endpoint, with Try it",
    permission: "session:basic",
  },
  {
    key: "wall",
    path: "/alerts?view=wall",
    label: "Wall view",
    title: "For a wall screen: alarms only, no controls",
    permission: "page:wall",
  },
];

// The map, the board and the wall keep the page's ?tenant= / ?q= (#366).
const SCOPED = new Set(["map", "alerts", "wall"]);

type Workspace = { prefix: string; pages: Partial<Record<WorkspacePage, string>> };

/** A page's address for *role*: its workspace's (#697), else the flat one; with
 * *linkQuery* (``tenant=a&q=b``) on the scoped pages. */
export function pageHref(page: MenuPage, role?: string | null, linkQuery = ""): string {
  const workspace = role ? (WORKSPACES as Record<string, Workspace>)[role] : undefined;
  const sub = workspace?.pages[page.key as WorkspacePage];
  const href = sub === undefined ? page.path : workspace!.prefix + sub || "/";
  if (!linkQuery || !SCOPED.has(page.key)) return href;
  return `${href}${href.includes("?") ? "&" : "?"}${linkQuery}`;
}

/** The menu's pages: all of them until *allowed* (page-config's access.pages) is
 * known, then only those. Convenience only: the API checks every page. */
export function menuPages(allowed: readonly string[] | null): MenuPage[] {
  return allowed ? MENU_PAGES.filter((page) => allowed.includes(page.key)) : [...MENU_PAGES];
}

/** Which menu page *path* is (with ?view=wall: *wall*), in any workspace too; "admin"
 * for Admin; null for the others (a status link, the sign-in page). */
export function activePage(path: string, wall: boolean): string | null {
  const found = pageAt(path, wall);
  if (found) return found.rest ? "availability" : found.page;
  if (path === "/admin") return "admin";
  if (path === "/help" || path.startsWith("/help/")) return "help";
  if (path === "/api/docs") return "docs";
  return null;
}
