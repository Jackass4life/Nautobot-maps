// What the pages read to draw themselves (#463): who is signed in, Nautobot's
// address, the menu and each page's settings; fetched once per page load,
// shared by every component.
import type { components } from "~/api/api.schema";

export type PageConfig = components["schemas"]["PageConfig"];

export function usePageConfig() {
  return useAsyncData(
    "page-config",
    () => unwrap(useNuxtApp().$api.GET("/api/v1/page-config")) as Promise<PageConfig>,
    {
      default: () =>
        ({
          user: null,
          nautobot_url: "",
          pages: [],
          maintenance: { recent_days: 7, history_days: 30, max_sites: 200 },
          availability: { today: "", oldest_day: "", basis: "any_down", target: 99.9 },
          map: {
            tile_url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            tile_attribution: "",
            view: null,
          },
          board: { case_url_template: "", levels: {} },
          admin: null,
        }) as PageConfig,
    },
  );
}
