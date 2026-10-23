// What the pages read to draw themselves (#463): who is signed in, Nautobot's
// address and the menu; fetched once per page load, shared by every component.
import type { components } from "~/api/schema";

export type PageConfig = components["schemas"]["PageConfig"];

export function usePageConfig() {
  return useAsyncData("page-config", () => $fetch<PageConfig>("/api/page-config"), {
    default: () => ({ user: null, nautobot_url: "", pages: [] }) as PageConfig,
  });
}
